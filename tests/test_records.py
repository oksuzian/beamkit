import json

import pytest

from beamkit import BeamkitError, records


def _rec(run_id="T.e470313", created="2026-09-03T10:00:00+00:00", state="created"):
    return records.RunRecord(run_id=run_id, tag="T", dsconf="e470313", owner="u", run_as="self",
                             deck={"sha": "e" * 40}, params={}, events_per_job=10, njobs=3,
                             outloc="scratch", slice_size=3, state=state, created=created)


def test_round_trip(tmp_path):
    rec = _rec()
    rec.campaign_id = 5
    rec.ticks.append({"when": "t", "rc": 0, "needs_attention": False, "summary": "ok"})
    p = records.save(rec, tmp_path)
    assert p == tmp_path / "T.e470313" / "run.json"
    assert records.load("T.e470313", tmp_path) == rec
    assert json.loads(p.read_text())["campaign_id"] == 5


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


def test_record_without_site_reads_as_fermilab(tmp_path):
    rec = _rec()
    d = rec.to_dict()
    del d["site"]
    del d["nersc"]
    (tmp_path / "T.e470313").mkdir()
    (tmp_path / "T.e470313" / "run.json").write_text(json.dumps(d))
    loaded = records.load("T.e470313", tmp_path)
    assert loaded.site == "fermilab" and loaded.nersc == {}


def test_nersc_block_round_trips(tmp_path):
    rec = _rec(state="partially_submitted")
    rec.site = "nersc"
    rec.nersc = {"run_dir": "/global/cfs/x", "jobs": [{"slurm_id": "1", "offset": 0, "count": 3}]}
    records.save(rec, tmp_path)
    assert records.load("T.e470313", tmp_path) == rec


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
