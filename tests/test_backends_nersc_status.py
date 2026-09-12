import pytest

from beamkit import BeamkitError, iri, tools
from beamkit.backends import nersc
from tests.test_backends_nersc_run import BASE, SHA, deck, fake, nersc_home, _run   # noqa: F401

RD = f"{BASE}/runs/T.e470313"


def _land(fake, idx, with_nts=True):
    seq = f"{idx:08d}"
    fake.files[f"{RD}/out/log.u.T.e470313.{seq}.log"] = b"BK_DONE\n"
    if with_nts:
        fake.files[f"{RD}/out/nts.u.T.e470313.{seq}.root"] = b"r" * (100 + idx)


def test_status_while_running(fake):
    _run(njobs=3)
    fake.jobs["58197742"]["state"] = "active"
    _land(fake, 0)
    st = tools.beamline_status("T.e470313")
    assert st["record"]["state"] == "submitted"
    assert st["nersc"]["jobs"][0]["state"] == "active" and st["nersc"]["jobs"][0]["node"] == "nid004381"
    assert st["nersc"]["outputs"] == {"expected": 3, "nts": 1, "logs": 1, "missing": [1, 2],
                                      "nts_files": ["nts.u.T.e470313.00000000.root"]}
    assert st["campaign"] is None


def test_status_complete_when_terminal_and_all_nts(fake):
    _run(njobs=3)
    fake.jobs["58197742"]["state"] = "completed"
    for i in range(3):
        _land(fake, i)
    st = tools.beamline_status("T.e470313")
    assert st["record"]["state"] == "complete" and st["nersc"]["outputs"]["missing"] == []
    assert tools.list_beamline_runs(state="complete")["count"] == 1


def test_status_short_when_terminal_and_nts_missing(fake):
    _run(njobs=3)
    fake.jobs["58197742"]["state"] = "failed"
    fake.jobs["58197742"]["exit_code"] = 1
    _land(fake, 0)
    _land(fake, 1, with_nts=False)
    st = tools.beamline_status("T.e470313")
    assert st["record"]["state"] == "short" and st["nersc"]["outputs"]["missing"] == [1, 2]
    assert st["nersc"]["jobs"][0]["exit_code"] == 1


def test_missing_list_is_capped_at_50(fake):
    _run(njobs=300)
    for j in fake.jobs.values():
        j["state"] = "completed"
    st = tools.beamline_status("T.e470313")
    assert len(st["nersc"]["outputs"]["missing"]) == 50 and st["nersc"]["outputs"]["nts"] == 0


def test_status_of_a_created_run_asks_nothing_of_slurm(fake):
    _run(njobs=3, submit=False)
    n = len(fake.calls)
    st = tools.beamline_status("T.e470313")
    assert st["record"]["state"] == "created" and st["nersc"]["jobs"] == []
    assert all("/compute/status/" not in c[1] for c in fake.calls[n:])


def test_outputs_lists_cfs_paths(fake):
    _run(njobs=3)
    _land(fake, 0)
    _land(fake, 2)
    out = tools.beamline_outputs("T.e470313")
    assert out["run_dir"] == RD and out["n_files"] == 2 and out["total_size"] == 100 + 102
    assert [f["index"] for f in out["files"]] == [0, 2]
    assert out["files"][1]["path"] == f"{RD}/out/nts.u.T.e470313.00000002.root"


def test_beamline_outputs_on_an_enqueue_failed_run_pins_the_current_raise(fake, monkeypatch):
    """A run that failed before any remote write has no out/ dir on CFS at
    all. status() guards that with out_counts' missing-out/-is-zero rule;
    beamline_outputs calls outputs() directly, which has no such guard and
    lets the IriError through today (ledger Task 10 deferred). Pin the
    current behavior so a future change to it is a deliberate decision."""
    def boom(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(nersc.nersc_cnf, "build_cnf", boom)
    with pytest.raises(BeamkitError, match="nothing was submitted"):
        _run(njobs=1)
    rec = tools.beamline_status("T.e470313")["record"]
    assert rec["state"] == "enqueue_failed"
    with pytest.raises(iri.IriError, match="No such file"):
        tools.beamline_outputs("T.e470313")


def test_make_recoveries_refused_on_a_nersc_run(fake):
    _run(njobs=1)
    with pytest.raises(BeamkitError, match="no recovery on nersc; submit a new run"):
        tools.make_recoveries("T.e470313", "self")
