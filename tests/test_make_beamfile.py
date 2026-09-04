import json
import os
from pathlib import Path

import pytest

from beamkit import beamfile, paths, records, tools

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
    with pytest.raises(tools.ToolError, match="no files"):
        tools.make_beamfile("T.e470313", "bm", "self")
    assert not (beamkit_home / "beamfiles").exists() or not list((beamkit_home / "beamfiles").iterdir())


def test_existing_beamfile_refused(fake, beamkit_home):
    _record()
    tools.make_beamfile("T.e470313", "bm", "self")
    with pytest.raises(tools.ToolError, match="exists"):
        tools.make_beamfile("T.e470313", "bm", "self")


def test_custom_cuts_and_label(fake):
    _record()
    cuts = {"keep_pdg": None, "drop_pdg": [], "min_p_mev": {"22": 5}}
    out = tools.make_beamfile("T.e470313", "soft5", "self", cuts=cuts)
    assert out["cuts"] == {"keep_pdg": None, "drop_pdg": [], "min_p_mev": {22: 5.0}} and out["flavor"] == "soft5"


def test_bad_flavor_refused_before_reading(fake, monkeypatch):
    _record()
    monkeypatch.setattr(tools.bridge, "dataset_files", lambda ds, loc: (_ for _ in ()).throw(AssertionError("read")))
    with pytest.raises(tools.ToolError, match="preset"):
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
    with pytest.raises(tools.ToolError, match="confirm"):
        tools.make_beamfile("T.e470313", "bm", "mu2epro", publish=True)
    out = tools.make_beamfile("T.e470313", "bm", "mu2epro", publish=True, confirm=True)
    assert out["location"] == "tape" and out["sam_name"] == "etc.mu2e.TBeam-bm.e470313.txt"


def test_publish_bad_location(fake, beamkit_home):
    _record()
    with pytest.raises(tools.ToolError, match="location"):
        tools.make_beamfile("T.e470313", "bm", "self", publish=True, location="resilient")
    assert fake["push_file"] == []
    assert not (beamkit_home / "beamfiles").exists() or not list((beamkit_home / "beamfiles").iterdir())


def test_publish_without_push_file_in_prodtools(fake, monkeypatch):
    _record()
    from beamkit import bridge
    def missing(*a, **k):
        raise bridge.BridgeError("this prodtools has no push_file tool; make_beamfile works with publish=False only")
    monkeypatch.setattr(tools.bridge, "push_file", missing)
    with pytest.raises(tools.ToolError, match="publish=False"):
        tools.make_beamfile("T.e470313", "bm", "self", publish=True)


def test_publish_failure_discards_beamfile_and_allows_retry(fake, monkeypatch, beamkit_home):
    _record()

    def failing_push(path, location, parents, run_as, confirm):
        raise RuntimeError("push down")
    monkeypatch.setattr(tools.bridge, "push_file", failing_push)
    with pytest.raises(tools.ToolError, match="discarded"):
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


def test_dataset_files_bridge_error_becomes_tool_error(fake, monkeypatch):
    _record()
    from beamkit import bridge
    monkeypatch.setattr(tools.bridge, "dataset_files",
                        lambda ds, loc: (_ for _ in ()).throw(bridge.BridgeError("boom")))
    with pytest.raises(tools.ToolError, match="dataset_files"):
        tools.make_beamfile("T.e470313", "bm", "self")


@pytest.mark.skipif(not os.path.exists(beamfile.ANA_PYTHON), reason="ana interpreter not on this host")
def test_real_build_on_fixture(monkeypatch, beamkit_home):
    _record(njobs=1)
    monkeypatch.setattr(tools.bridge, "dataset_files", lambda ds, loc: _files([0]))
    out = tools.make_beamfile("T.e470313", "ps", "self")
    assert Path(out["path"]).read_bytes() == (FIX / "reference_ps.txt").read_bytes()
    assert out["pot"] == 10 and out["missing_indices"] == []
