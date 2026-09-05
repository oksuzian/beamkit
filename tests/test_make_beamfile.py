import json
import os
from pathlib import Path

import pytest

from beamkit import BeamkitError, beamfile, paths, records, tools

FIX = Path(__file__).parent / "fixtures"


def _record(njobs=5, run_as="self", owner="u"):
    rec = records.RunRecord(run_id="T.e470313", tag="T", dsconf="e470313", owner=owner, run_as=run_as,
                            deck={}, params={}, events_per_job=10, njobs=njobs, outloc="scratch",
                            slice_size=5, state="submitted", campaign_id=7, created=records.now_utc())
    records.save(rec, paths.runs_dir())
    return rec


def _files(indices):
    return [{"name": f"nts.u.T.e470313.{i:08d}.root", "index": i, "size": 1, "path": str(FIX / "plane47.root")}
            for i in indices]


@pytest.fixture
def fake(monkeypatch):
    calls = {"push_file": []}
    monkeypatch.setattr(tools.bridge, "dataset_files", lambda ds, loc: _files([0, 2, 3]))
    def fake_build(paths_, plane, cuts, out_path, python=None):
        Path(out_path).write_text("".join(beamfile.HEADER) + "row\n")
        return {"rows_in": 10, "rows_out": 1, "dropped": dict.fromkeys(beamfile.DROP_KEYS, 0),
                "sha256": "s" * 64, "size": Path(out_path).stat().st_size}
    monkeypatch.setattr(tools.beamfile, "build", fake_build)
    def push_file(path, location, parents, run_as, confirm):
        calls["push_file"].append(dict(path=str(path), location=location, parents=list(parents), run_as=run_as, confirm=confirm))
        return {"name": Path(path).name}
    monkeypatch.setattr(tools.bridge, "push_file", push_file)
    monkeypatch.setattr(tools.bridge, "push_file_available", lambda: True)
    return calls


def test_partial_run_pot_and_missing(fake, beamkit_home):
    _record(njobs=5)
    out = tools.make_beamfile("T.e470313", "bm", "self")
    assert out["pot"] == 30 and out["n_files"] == 3 and out["missing_indices"] == [1, 4]
    assert out["cuts"] == beamfile.FLAVORS["bm"] and out["rows"] == 1 and out["rows_in"] == 10
    assert out["path"] == str(beamkit_home / "beamfiles" / "T.e470313.bm.txt")
    assert out["sam_name"] is None and out["location"] is None
    side = json.loads((beamkit_home / "beamfiles" / "T.e470313.bm.json").read_text())
    assert side["pot"] == 30 and side["source_files"][0] == "nts.u.T.e470313.00000000.root"
    rec = records.load("T.e470313", paths.runs_dir())
    assert rec.beamfiles[0]["flavor"] == "bm" and fake["push_file"] == []


def test_zero_files_refused(fake, monkeypatch, beamkit_home):
    _record()
    monkeypatch.setattr(tools.bridge, "dataset_files", lambda ds, loc: [])
    with pytest.raises(BeamkitError, match="no files"):
        tools.make_beamfile("T.e470313", "bm", "self")
    assert not (beamkit_home / "beamfiles").exists() or not list((beamkit_home / "beamfiles").iterdir())


def test_existing_beamfile_refused(fake, beamkit_home):
    _record()
    tools.make_beamfile("T.e470313", "bm", "self")
    with pytest.raises(BeamkitError, match="exists"):
        tools.make_beamfile("T.e470313", "bm", "self")


def test_custom_cuts_and_label(fake):
    _record()
    cuts = {"keep_pdg": None, "drop_pdg": [], "min_p_mev": {"22": 5}}
    out = tools.make_beamfile("T.e470313", "soft5", "self", cuts=cuts)
    assert out["cuts"] == {"keep_pdg": None, "drop_pdg": [], "min_p_mev": {22: 5.0}} and out["flavor"] == "soft5"


def test_bad_flavor_refused_before_reading(fake, monkeypatch):
    _record()
    monkeypatch.setattr(tools.bridge, "dataset_files", lambda ds, loc: (_ for _ in ()).throw(AssertionError("read")))
    with pytest.raises(BeamkitError, match="preset"):
        tools.make_beamfile("T.e470313", "nope", "self")


def test_publish_self_defaults_scratch(fake, beamkit_home):
    _record()
    out = tools.make_beamfile("T.e470313", "bm", "self", publish=True)
    assert out["sam_name"] == "etc.u.TBeam-bm.e470313.txt" and out["location"] == "scratch"
    staged = beamkit_home / "beamfiles" / "etc.u.TBeam-bm.e470313.txt"
    assert fake["push_file"] == [dict(path=str(staged), location="scratch", run_as="self", confirm=False,
                                      parents=[f"nts.u.T.e470313.{i:08d}.root" for i in (0, 2, 3)])]
    assert staged.stat().st_ino == (beamkit_home / "beamfiles" / "T.e470313.bm.txt").stat().st_ino


def test_publish_mu2epro_defaults_tape_needs_confirm(fake):
    _record(run_as="mu2epro", owner="mu2e")
    with pytest.raises(BeamkitError, match="confirm"):
        tools.make_beamfile("T.e470313", "bm", "mu2epro", publish=True)
    out = tools.make_beamfile("T.e470313", "bm", "mu2epro", publish=True, confirm=True)
    assert out["location"] == "tape" and out["sam_name"] == "etc.mu2e.TBeam-bm.e470313.txt"


def test_publish_as_mu2epro_from_a_self_run_refused(fake, beamkit_home):
    _record(run_as="self", owner="u")
    with pytest.raises(BeamkitError, match="run_as='self'.*run_as='mu2epro'"):
        tools.make_beamfile("T.e470313", "bm", "mu2epro", publish=True, confirm=True)
    assert fake["push_file"] == []
    bf_dir = beamkit_home / "beamfiles"
    assert not bf_dir.exists() or not list(bf_dir.iterdir())


def test_publish_as_self_from_a_mu2epro_run_refused(fake, beamkit_home):
    _record(run_as="mu2epro", owner="mu2e")
    with pytest.raises(BeamkitError, match="run_as='mu2epro'.*run_as='self'"):
        tools.make_beamfile("T.e470313", "bm", "self", publish=True)
    assert fake["push_file"] == []
    bf_dir = beamkit_home / "beamfiles"
    assert not bf_dir.exists() or not list(bf_dir.iterdir())


def test_build_without_publish_ignores_the_identity(fake):
    """No push, no pushing identity to disagree with: a read-only build of a
    mu2epro run under your own account stays allowed."""
    _record(run_as="mu2epro", owner="mu2e")
    assert tools.make_beamfile("T.e470313", "bm", "self")["sam_name"] is None


def test_publish_bad_location(fake, beamkit_home):
    _record()
    with pytest.raises(BeamkitError, match="location"):
        tools.make_beamfile("T.e470313", "bm", "self", publish=True, location="resilient")
    assert fake["push_file"] == []
    assert not (beamkit_home / "beamfiles").exists() or not list((beamkit_home / "beamfiles").iterdir())


def test_publish_without_push_file_refused_before_any_read_or_build(fake, monkeypatch, beamkit_home):
    """The precondition is knowable up front, so it is checked up front: no
    dataset read, no ana subprocess, nothing written and nothing to discard."""
    _record()
    monkeypatch.setattr(tools.bridge, "push_file_available", lambda: False)
    monkeypatch.setattr(tools.bridge, "dataset_files",
                        lambda ds, loc: (_ for _ in ()).throw(AssertionError("read the dataset")))
    monkeypatch.setattr(tools.beamfile, "build",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("built the beam file")))
    with pytest.raises(BeamkitError, match="publish=False"):
        tools.make_beamfile("T.e470313", "bm", "self", publish=True)
    assert fake["push_file"] == []
    bf_dir = beamkit_home / "beamfiles"
    assert not bf_dir.exists() or not list(bf_dir.iterdir())


def test_publish_probe_failure_becomes_a_tool_error(fake, monkeypatch):
    _record()
    from beamkit import bridge
    monkeypatch.setattr(tools.bridge, "push_file_available",
                        lambda: (_ for _ in ()).throw(bridge.BridgeError("prodtools is not importable here")))
    monkeypatch.setattr(tools.bridge, "dataset_files",
                        lambda ds, loc: (_ for _ in ()).throw(AssertionError("read the dataset")))
    with pytest.raises(BeamkitError, match="not importable"):
        tools.make_beamfile("T.e470313", "bm", "self", publish=True)


def test_publish_failure_discards_beamfile_and_allows_retry(fake, monkeypatch, beamkit_home):
    _record()

    def failing_push(path, location, parents, run_as, confirm):
        raise RuntimeError("push down")
    monkeypatch.setattr(tools.bridge, "push_file", failing_push)
    with pytest.raises(BeamkitError, match="discarded"):
        tools.make_beamfile("T.e470313", "bm", "self", publish=True)
    bf_dir = beamkit_home / "beamfiles"
    names = {p.name for p in bf_dir.iterdir()} if bf_dir.exists() else set()
    assert not any(n.endswith(".txt") for n in names)
    assert not any(n.endswith(".json") for n in names)
    assert not any(n.startswith("etc.") for n in names)
    rec = records.load("T.e470313", paths.runs_dir())
    assert rec.beamfiles == []
    # the retry path is no longer blocked by a leftover staged link / exists-check
    out = tools.make_beamfile("T.e470313", "bm", "self", publish=False)
    assert out["sam_name"] is None and out["location"] is None


def test_publish_link_collision_keeps_the_file_it_did_not_create(fake, beamkit_home):
    """os.link fails because the staged name already exists -- the copy of a
    beam file published by an earlier call. The unwind discards only what THIS
    call created, so that file survives."""
    _record()
    bf_dir = beamkit_home / "beamfiles"
    bf_dir.mkdir(parents=True, exist_ok=True)
    published = bf_dir / "etc.u.TBeam-bm.e470313.txt"
    published.write_text("the copy published by an earlier make_beamfile call\n")
    with pytest.raises(BeamkitError, match="discarded"):
        tools.make_beamfile("T.e470313", "bm", "self", publish=True)
    assert fake["push_file"] == []
    assert published.read_text() == "the copy published by an earlier make_beamfile call\n"
    assert {p.name for p in bf_dir.iterdir()} == {"etc.u.TBeam-bm.e470313.txt"}
    rec = records.load("T.e470313", paths.runs_dir())
    assert rec.beamfiles == []
    # the retry path is not blocked by the pre-existing staged name
    out = tools.make_beamfile("T.e470313", "bm", "self", publish=False)
    assert out["sam_name"] is None and out["location"] is None


def test_dataset_files_bridge_error_reaches_the_caller_as_is(fake, monkeypatch):
    _record()
    from beamkit import bridge
    monkeypatch.setattr(tools.bridge, "dataset_files",
                        lambda ds, loc: (_ for _ in ()).throw(bridge.BridgeError("boom")))
    with pytest.raises(bridge.BridgeError, match="boom"):
        tools.make_beamfile("T.e470313", "bm", "self")


@pytest.mark.skipif(not os.path.exists(beamfile.ANA_PYTHON), reason="ana interpreter not on this host")
def test_real_build_on_fixture(monkeypatch, beamkit_home):
    _record(njobs=1)
    monkeypatch.setattr(tools.bridge, "dataset_files", lambda ds, loc: _files([0]))
    out = tools.make_beamfile("T.e470313", "ps", "self")
    assert Path(out["path"]).read_bytes() == (FIX / "reference_ps.txt").read_bytes()
    assert out["pot"] == 10 and out["missing_indices"] == []


def test_label_names_the_files_and_flavor_names_the_cuts(fake, beamkit_home):
    """flavor used to be both the cut preset and the permanent SAM label, so
    dodging a local file collision by 'picking another flavor' renamed the
    SAM artifact. The label is its own thing; it defaults to the flavor."""
    _record()
    out = tools.make_beamfile("T.e470313", "bm", "self", publish=True, label="run3")
    assert out["flavor"] == "bm" and out["label"] == "run3" and out["cuts"] == beamfile.FLAVORS["bm"]
    assert out["path"] == str(beamkit_home / "beamfiles" / "T.e470313.run3.txt")
    assert out["sam_name"] == "etc.u.TBeam-run3.e470313.txt"
    again = tools.make_beamfile("T.e470313", "bm", "self", label="run4")
    assert again["sam_name"] is None and again["label"] == "run4"


@pytest.mark.parametrize("bad", ["Run3", "3run", "a-b", "x" * 17, ""])
def test_bad_label_refused_before_reading(fake, monkeypatch, bad):
    _record()
    monkeypatch.setattr(tools.bridge, "dataset_files", lambda ds, loc: (_ for _ in ()).throw(AssertionError("read")))
    with pytest.raises(BeamkitError, match="label"):
        tools.make_beamfile("T.e470313", "bm", "self", label=bad)


@pytest.mark.parametrize("bad", ["", "Z 3712", "3712", "Z3712;rm", 7])
def test_bad_plane_refused_before_reading(fake, monkeypatch, bad):
    """plane goes straight into the reader's argv; a typo used to cost the
    dataset listing plus an ana subprocess before _read_plane exited 3."""
    _record()
    monkeypatch.setattr(tools.bridge, "dataset_files", lambda ds, loc: (_ for _ in ()).throw(AssertionError("read")))
    with pytest.raises(BeamkitError, match="plane"):
        tools.make_beamfile("T.e470313", "bm", "self", plane=bad)
