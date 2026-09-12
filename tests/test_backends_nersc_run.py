import json
import tarfile

import pytest

from beamkit import BeamkitError, decks, iri, tools
from beamkit.backends import nersc
from tests.fake_iri import FakeResponse, FakeSession

SHA = "e470313" + "0" * 33
BASE = "/global/cfs/cdirs/m4599/Users/u/beamkit"


@pytest.fixture
def deck(tmp_path):
    d = tmp_path / "deckcache" / SHA[:12]
    d.mkdir(parents=True)
    (d / "Mu2E.in").write_text("param -unset First_Event=1\n")
    return d


@pytest.fixture
def nersc_home(beamkit_home, tmp_path):
    sfapi = tmp_path / "sfapi"
    sfapi.mkdir()
    (sfapi / "client_id").write_text("abcdefghijklm")
    key = sfapi / "priv_key.pem"
    key.write_text("-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----\n")
    key.chmod(0o400)
    beamkit_home.mkdir(parents=True)
    (beamkit_home / "nersc.toml").write_text(
        f'api = "https://api.iri.nersc.gov/api/v2"\nsfapi_dir = "{sfapi}"\naccount = "m4599"\n'
        f'base_dir = "{BASE}"\nqos = "debug"\nowner = "u"\nprocs_per_node = 128\n')
    return beamkit_home


@pytest.fixture
def fake(monkeypatch, deck, nersc_home):
    s = FakeSession()
    made = []
    def make_client(cfg):
        made.append(cfg)
        return iri.IriClient(cfg, token_provider=lambda: "tok", session=s)
    monkeypatch.setattr(nersc, "make_client", make_client)
    monkeypatch.setattr(decks, "materialize", lambda url, ref, cache: decks.DeckPin(url, ref, SHA, str(deck), True))
    s.made = made
    return s


def _run(**kw):
    base = dict(tag="T", deck_ref=SHA, run_as="self", events_per_job=10, njobs=300, site="nersc")
    base.update(kw)
    return tools.run_beamline(**base)


def test_happy_path_layout_record_and_jobs(fake, nersc_home):
    rec = _run()
    rd = f"{BASE}/runs/T.e470313"
    assert rec["site"] == "nersc" and rec["state"] == "submitted" and rec["campaign_id"] is None
    assert rec["owner"] == "u" and rec["datasets"] == ["nts.u.T.e470313.root"]
    assert rec["nersc"]["run_dir"] == rd and rec["nersc"]["cnf"] == "cnf.u.T.e470313.0.tar"
    assert {rd, rd + "/out", rd + "/slurm", rd + "/beamfiles"} <= fake.dirs
    assert set(fake.files) == {rd + "/cnf.u.T.e470313.0.tar", rd + "/job.sh", rd + "/inner.sh"}
    assert b"BK_DONE" in fake.files[rd + "/inner.sh"]
    jobs = rec["nersc"]["jobs"]
    assert [(j["offset"], j["count"]) for j in jobs] == [(0, 128), (128, 128), (256, 44)]
    assert [j["slurm_id"] for j in jobs] == ["58197742", "58197743", "58197744"]
    spec = fake.jobs["58197743"]["spec"]
    assert spec["environment"] == {"BK_OFFSET": "128"} and spec["resources"]["process_count"] == 128
    assert spec["attributes"]["duration"] == 920 and spec["attributes"]["queue_name"] == "debug"
    assert fake.jobs["58197743"]["idem"] == "T.e470313/128"
    # the local copy of the cnf is kept next to the record
    local = nersc_home / "runs" / "T.e470313" / "cnf.u.T.e470313.0.tar"
    with tarfile.open(local) as t:
        assert "jobpars.json" in t.getnames() and "work/Mu2E.in" in t.getnames()
    assert json.loads((nersc_home / "runs" / "T.e470313" / "run.json").read_text())["nersc"]["walltime_s"] == 172800


def test_layout_creates_base_and_runs_dirs_on_a_fresh_cfs(fake):
    """The API's mkdir is not -p: on a brand-new base_dir neither it nor
    base_dir/runs exists yet, and the layout must create both, not just
    the run dir and its subdirectories."""
    rec = _run(njobs=1)
    rd = f"{BASE}/runs/T.e470313"
    assert rec["state"] == "submitted"
    assert {BASE, BASE + "/runs", rd, rd + "/out", rd + "/slurm", rd + "/beamfiles"} <= fake.dirs


def test_dsconf_collision_probes_the_remote_run_dir(fake):
    fake.dirs.add(f"{BASE}/runs/T.e470313")
    rec = _run(njobs=1)
    assert rec["dsconf"] == "e470313-001" and rec["nersc"]["run_dir"] == f"{BASE}/runs/T.e470313-001"


def test_submit_false_then_submit_run(fake):
    rec = _run(njobs=5, submit=False)
    assert rec["state"] == "created" and rec["nersc"]["jobs"] == [] and fake.jobs == {}
    rec = tools.submit_run("T.e470313", "self")
    assert rec["state"] == "submitted" and len(rec["nersc"]["jobs"]) == 1


def test_partial_submit_is_recorded_and_resumable(fake):
    fake.fail_submit_at = 1
    with pytest.raises(BeamkitError, match="job 1 of 3.*sbatch: error"):
        _run()
    rec = tools.beamline_status("T.e470313")["record"]
    assert rec["state"] == "partially_submitted" and [j["offset"] for j in rec["nersc"]["jobs"]] == [0]
    fake.fail_submit_at = None
    rec = tools.submit_run("T.e470313", "self")
    assert rec["state"] == "submitted" and [j["offset"] for j in rec["nersc"]["jobs"]] == [0, 128, 256]


def test_submit_run_refuses_a_submitted_run(fake):
    _run(njobs=1)
    with pytest.raises(BeamkitError, match="state 'submitted'"):
        tools.submit_run("T.e470313", "self")


def test_mu2epro_refused_before_any_client(fake):
    with pytest.raises(BeamkitError, match="run_as='self' only"):
        _run(run_as="mu2epro", confirm=True)
    assert fake.made == []


def test_outloc_other_than_scratch_refused(fake):
    with pytest.raises(BeamkitError, match="outputs of a NERSC run stay on CFS"):
        _run(outloc="disk")
    assert fake.made == []


def test_bad_walltime_refused_before_any_client(fake):
    with pytest.raises(BeamkitError, match="walltime_s"):
        _run(walltime_s=0)
    assert fake.made == []


def test_missing_config_is_named(beamkit_home, fake):
    (beamkit_home / "nersc.toml").unlink()
    with pytest.raises(BeamkitError, match="nersc.toml"):
        _run()


def test_upload_failure_leaves_a_retryable_record(fake, monkeypatch):
    monkeypatch.setattr(iri, "UPLOAD_MAX", 10)
    with pytest.raises(BeamkitError, match="upload cap"):
        _run(njobs=1)
    rec = tools.beamline_status("T.e470313")["record"]
    assert rec["state"] == "enqueue_failed" and rec["nersc"]["jobs"] == []
    monkeypatch.setattr(iri, "UPLOAD_MAX", 5_242_880)
    assert _run(njobs=1)["state"] == "submitted"


def test_local_run_dir_from_a_submitted_run_is_never_reused(fake):
    _run(njobs=1)
    fake.dirs.discard(f"{BASE}/runs/T.e470313")      # remote gone, local record remains
    with pytest.raises(BeamkitError, match="already exists; a run id is never reused"):
        _run(njobs=1)


def test_upload_failure_after_mkdir_retries_the_same_run_id_no_new_dsconf(fake):
    """A transient failure on an upload happens after the remote run dir is
    already created by client.mkdir. The retry must not treat that leftover
    directory as a collision and burn a new dsconf (T.e470313 -> -001)."""
    rd = f"{BASE}/runs/T.e470313"
    orig_request = fake.request
    uploads = {"n": 0}
    def flaky(method, url, **kw):
        if "/filesystem/upload/" in url:
            uploads["n"] += 1
            if uploads["n"] == 2:
                return FakeResponse(500, {"type": "about:blank", "status": 500,
                                          "title": "Internal Server Error",
                                          "detail": "Error uploading: connection reset"})
        return orig_request(method, url, **kw)
    fake.request = flaky

    with pytest.raises(BeamkitError):
        _run(njobs=1)
    rec = tools.beamline_status("T.e470313")["record"]
    assert rec["state"] == "enqueue_failed" and rec["nersc"]["jobs"] == []
    assert rd in fake.dirs

    rec2 = _run(njobs=1)
    assert rec2["run_id"] == "T.e470313" and rec2["state"] == "submitted"
