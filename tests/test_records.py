import json

import pytest

from beamkit import BeamkitError, records
from beamkit.backends import fermilab, nersc


def _rec(run_id="T.e470313", created="2026-09-03T10:00:00+00:00", state="created", site="fermilab", block=None):
    if block is None:
        block = (fermilab.Block(prodtools={"root": "/pt", "commit": "c" * 40, "dev_dir": None}) if site == "fermilab"
                 else nersc.Block(run_dir="/global/cfs/x/runs/" + run_id, cnf="cnf.u.T.e470313.0.tar",
                                  walltime_s=100, config={}))
    return records.RunRecord(run_id=run_id, tag="T", dsconf="e470313", owner="u", run_as="self",
                             deck={}, params={}, events_per_job=10, njobs=3, outloc="scratch", slice_size=3,
                             state=state, site=site, block=block, created=created)


def test_round_trip(tmp_path):
    rec = _rec()
    rec.block.campaign_id = 5
    rec.block.ticks.append({"when": "t", "rc": 0, "needs_attention": False, "summary": "ok"})
    p = records.save(rec, tmp_path)
    assert p == tmp_path / "T.e470313" / "run.json"
    assert records.load("T.e470313", tmp_path) == rec
    assert json.loads(p.read_text())["fermilab"]["campaign_id"] == 5


def test_save_is_atomic_no_part_left(tmp_path):
    records.save(_rec(), tmp_path)
    assert sorted(x.name for x in (tmp_path / "T.e470313").iterdir()) == ["run.json"]


def test_load_missing_is_record_error(tmp_path):
    with pytest.raises(records.RecordError, match="no run record"):
        records.load("nope.abc1234", tmp_path)


def test_list_runs_newest_first_and_state_filter(tmp_path):
    records.save(_rec("A.1111111", "2026-09-01T00:00:00+00:00", "submitted"), tmp_path)
    records.save(_rec("B.2222222", "2026-09-03T00:00:00+00:00", "created"), tmp_path)
    records.save(_rec("C.3333333", "2026-09-02T00:00:00+00:00", "submitted"), tmp_path)
    assert [r.run_id for r in records.list_runs(tmp_path)] == ["B.2222222", "C.3333333", "A.1111111"]
    assert [r.run_id for r in records.list_runs(tmp_path, state="submitted")] == ["C.3333333", "A.1111111"]


def test_list_runs_empty_when_dir_missing(tmp_path):
    assert records.list_runs(tmp_path / "none") == []


def test_list_runs_refuses_unknown_state(tmp_path):
    with pytest.raises(records.RecordError):
        records.list_runs(tmp_path, state="running")


def test_now_utc_shape():
    s = records.now_utc()
    assert s.endswith("+00:00") and len(s) == len("2026-09-03T10:00:00+00:00")


def test_fermilab_block_round_trips_under_its_site_key(tmp_path):
    rec = _rec()
    rec.block.campaign_id, rec.block.tarball = 7, "cnf.u.T.e470313.0.tar"
    rec.block.ticks.append({"when": "t", "rc": 0, "needs_attention": False, "summary": ""})
    records.save(rec, tmp_path)
    d = json.loads((tmp_path / "T.e470313" / "run.json").read_text())
    assert d["site"] == "fermilab" and d["fermilab"]["campaign_id"] == 7 and "nersc" not in d
    assert "campaign_id" not in d and "block" not in d
    back = records.load("T.e470313", tmp_path)
    assert isinstance(back.block, fermilab.Block) and back.block.ticks[0]["rc"] == 0


def test_nersc_block_round_trips_under_its_site_key(tmp_path):
    rec = _rec(site="nersc")
    rec.block.jobs.append({"slurm_id": "1", "offset": 0, "count": 3, "submitted": "t"})
    records.save(rec, tmp_path)
    d = json.loads((tmp_path / "T.e470313" / "run.json").read_text())
    assert d["site"] == "nersc" and d["nersc"]["jobs"][0]["slurm_id"] == "1" and "fermilab" not in d
    back = records.load("T.e470313", tmp_path)
    assert isinstance(back.block, nersc.Block) and back.block.walltime_s == 100


@pytest.mark.parametrize("old", [
    {"campaign_id": 7},                       # pre-0.5.0 fermilab record
    {"site": "nersc", "nersc": {"jobs": []}, "campaign_id": None},   # pre-0.5.0 nersc record
    {"site": "fermilab"},                     # no block at all
])
def test_old_shape_is_refused_naming_the_file(tmp_path, old):
    d = tmp_path / "T.e470313"
    d.mkdir()
    base = {"run_id": "T.e470313", "tag": "T", "dsconf": "e470313", "owner": "u", "run_as": "self", "deck": {},
            "params": {}, "events_per_job": 10, "njobs": 3, "outloc": "scratch", "slice_size": 3,
            "state": "created", "beamkit_version": "0.4.0"}
    (d / "run.json").write_text(json.dumps({**base, **old}))
    with pytest.raises(records.RecordError, match=r"run\.json was written by beamkit 0\.4\.0 \(before 0\.5\.0\)"):
        records.load("T.e470313", tmp_path)
    with pytest.raises(records.RecordError, match="before 0.5.0"):
        records.list_runs(tmp_path)


def test_importing_records_does_not_import_backends():
    import subprocess, sys
    code = "import sys, beamkit.records; print('beamkit.backends' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "False"


def test_new_states_are_listable(tmp_path):
    for st in ("partially_submitted", "short", "complete"):
        records.save(_rec(run_id=f"T.{st}", state=st), tmp_path)
        assert [r.run_id for r in records.list_runs(tmp_path, state=st)] == [f"T.{st}"]


def test_try_load_is_none_when_there_is_no_record(tmp_path):
    """The callers that ask a question about a run dir rather than open one."""
    assert records.try_load("T.absent", tmp_path) is None
    records.save(_rec(), tmp_path)
    assert records.try_load("T.e470313", tmp_path).run_id == "T.e470313"


def test_claim_run_dir_refuses_a_used_id_unless_the_caller_says_retryable(tmp_path):
    records.claim_run_dir(tmp_path, "T.e470313", lambda *a: False)
    with pytest.raises(BeamkitError, match="already exists; a run id is never reused"):
        records.claim_run_dir(tmp_path, "T.e470313", lambda *a: False)
    assert records.claim_run_dir(tmp_path, "T.e470313", lambda *a: True).is_dir()


def test_atomic_write_text_leaves_no_part_file(tmp_path):
    records.atomic_write_text(tmp_path / "x.json", "{}\n")
    assert (tmp_path / "x.json").read_text() == "{}\n" and not list(tmp_path.glob("*.part"))
