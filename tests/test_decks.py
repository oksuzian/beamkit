import subprocess
from pathlib import Path

import pytest

from beamkit import decks


def _git(*args, cwd):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def upstream(tmp_path):
    """A bare repo with two commits: annotated tag `v1` on the first, lightweight tag `v2` on the second."""
    work = tmp_path / "work"
    work.mkdir()
    _git("init", "-q", "-b", "main", cwd=work)
    (work / "Mu2E.in").write_text("param -unset First_Event=1\n")
    (work / "Geometry").mkdir()
    (work / "Geometry" / "a.txt").write_text("a\n")
    _git("add", ".", cwd=work)
    _git("commit", "-q", "-m", "one", cwd=work)
    sha1 = _git("rev-parse", "HEAD", cwd=work)
    _git("tag", "-a", "v1", "-m", "tag one", cwd=work)
    (work / "Mu2E.in").write_text("param -unset First_Event=1\nparam epsMax=0.01\n")
    _git("commit", "-q", "-am", "two", cwd=work)
    sha2 = _git("rev-parse", "HEAD", cwd=work)
    _git("tag", "v2", cwd=work)
    bare = tmp_path / "upstream.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
    _git("config", "uploadpack.allowAnySHA1InWant", "true", cwd=bare)
    return {"url": f"file://{bare}", "sha1": sha1, "sha2": sha2, "work": work}


def test_resolve_full_sha_passthrough(upstream):
    assert decks.resolve_ref(upstream["url"], upstream["sha2"]) == upstream["sha2"]


def test_resolve_annotated_tag_gives_commit(upstream):
    assert decks.resolve_ref(upstream["url"], "v1") == upstream["sha1"]


def test_resolve_lightweight_tag(upstream):
    assert decks.resolve_ref(upstream["url"], "v2") == upstream["sha2"]


def test_resolve_branch(upstream):
    assert decks.resolve_ref(upstream["url"], "main") == upstream["sha2"]


def test_resolve_unknown_ref_is_error(upstream):
    with pytest.raises(decks.DeckError, match="neither"):
        decks.resolve_ref(upstream["url"], "nope")


def test_resolve_short_sha_is_error(upstream):
    with pytest.raises(decks.DeckError, match="neither"):
        decks.resolve_ref(upstream["url"], upstream["sha1"][:7])


def test_materialize_by_sha(upstream, tmp_path):
    cache = tmp_path / "decks"
    pin = decks.materialize(upstream["url"], upstream["sha1"], cache)
    assert pin.sha == upstream["sha1"] and pin.pinned is True and pin.ref == upstream["sha1"]
    assert Path(pin.dir) == cache / upstream["sha1"][:12]
    assert (Path(pin.dir) / "Mu2E.in").read_text() == "param -unset First_Event=1\n"
    assert (Path(pin.dir) / "Geometry" / "a.txt").exists()
    assert _git("rev-parse", "HEAD", cwd=pin.dir) == upstream["sha1"]


def test_materialize_by_tag_records_both(upstream, tmp_path):
    pin = decks.materialize(upstream["url"], "v1", tmp_path / "decks")
    assert pin.ref == "v1" and pin.sha == upstream["sha1"]
    assert pin.as_record() == {"url": upstream["url"], "ref": "v1", "sha": upstream["sha1"],
                               "dir": pin.dir, "pinned": True, "dirty": False}


def test_materialize_reuses_existing(upstream, tmp_path):
    cache = tmp_path / "decks"
    first = decks.materialize(upstream["url"], upstream["sha1"], cache)
    marker = Path(first.dir) / ".marker"
    marker.write_text("x")
    second = decks.materialize(upstream["url"], upstream["sha1"], cache)
    assert second.dir == first.dir and marker.exists()


def test_materialize_refuses_cache_dir_at_wrong_sha(upstream, tmp_path):
    cache = tmp_path / "decks"
    pin = decks.materialize(upstream["url"], upstream["sha1"], cache)
    # Corrupt the cache: move the sha2 checkout under sha1's name.
    other = decks.materialize(upstream["url"], upstream["sha2"], cache)
    import shutil
    shutil.rmtree(pin.dir)
    shutil.move(other.dir, pin.dir)
    with pytest.raises(decks.DeckError, match="expected"):
        decks.materialize(upstream["url"], upstream["sha1"], cache)


def test_materialize_unfetchable_leaves_no_dir(tmp_path):
    cache = tmp_path / "decks"
    with pytest.raises(decks.DeckError):
        decks.materialize(f"file://{tmp_path}/missing.git", "0" * 40, cache)
    assert not any(cache.iterdir()) if cache.exists() else True


def test_materialize_dest_appearing_midway_is_reused(upstream, tmp_path, monkeypatch):
    import shutil
    cache = tmp_path / "decks"
    sha = upstream["sha1"]
    dest = cache / sha[:12]
    original_git = decks._git
    appeared = {"done": False}

    def wrapper(*args, cwd=None):
        if args and args[0] == "checkout" and not appeared["done"]:
            appeared["done"] = True
            # Simulate a concurrent materialize() winning the race: build
            # dest for real via a separate cache dir, then move it into
            # place before this call's part.rename(dest) can run.
            concurrent = decks.materialize(upstream["url"], sha, tmp_path / "other-decks")
            shutil.move(concurrent.dir, dest)
        return original_git(*args, cwd=cwd)

    monkeypatch.setattr(decks, "_git", wrapper)
    pin = decks.materialize(upstream["url"], sha, cache)
    assert pin.dir == str(dest)
    assert original_git("rev-parse", "HEAD", cwd=dest) == sha
    assert not list(cache.glob("*.part-*"))


def test_materialize_dest_is_a_plain_file_raises_deckerror_and_cleans_up(upstream, tmp_path):
    cache = tmp_path / "decks"
    cache.mkdir()
    sha = upstream["sha1"]
    (cache / sha[:12]).write_text("x")
    with pytest.raises(decks.DeckError, match="not a directory"):
        decks.materialize(upstream["url"], sha, cache)
    assert not list(cache.glob("*.part-*"))


def test_materialize_dest_becomes_wrong_commit_midway_raises_and_cleans_up(upstream, tmp_path, monkeypatch):
    import shutil
    cache = tmp_path / "decks"
    sha1 = upstream["sha1"]
    sha2 = upstream["sha2"]
    dest = cache / sha1[:12]
    original_git = decks._git
    appeared = {"done": False}

    def wrapper(*args, cwd=None):
        if args and args[0] == "checkout" and not appeared["done"]:
            appeared["done"] = True
            # A competing writer lands a DIFFERENT commit at the same
            # cache slot before this call's part.rename(dest) can run.
            concurrent = decks.materialize(upstream["url"], sha2, tmp_path / "other-decks")
            shutil.move(concurrent.dir, dest)
        return original_git(*args, cwd=cwd)

    monkeypatch.setattr(decks, "_git", wrapper)
    with pytest.raises(decks.DeckError) as excinfo:
        decks.materialize(upstream["url"], sha1, cache)
    assert sha1 in str(excinfo.value) and sha2 in str(excinfo.value)
    assert not list(cache.glob("*.part-*"))


def test_materialize_ignores_foreign_part_dirs(upstream, tmp_path):
    cache = tmp_path / "decks"
    sha = upstream["sha1"]
    stale = cache / f"{sha[:12]}.part-stale"
    stale.mkdir(parents=True)
    (stale / "marker.txt").write_text("keep me")
    pin = decks.materialize(upstream["url"], sha, cache)
    assert pin.sha == sha
    assert stale.exists()
    assert (stale / "marker.txt").read_text() == "keep me"


def test_inspect_local_clean_and_dirty(upstream):
    work = upstream["work"]
    pin = decks.inspect_local(work)
    assert pin.pinned is False and pin.sha == upstream["sha2"] and pin.dirty is False
    (work / "Mu2E.in").write_text("changed\n")
    assert decks.inspect_local(work).dirty is True


def test_inspect_local_not_a_dir(tmp_path):
    with pytest.raises(decks.DeckError):
        decks.inspect_local(tmp_path / "nope")


def test_inspect_local_not_git(tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    pin = decks.inspect_local(d)
    assert pin.sha is None and pin.pinned is False
