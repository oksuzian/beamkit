"""runs.create with a stub backend: the ordering rules of the spec's §5.2,
with no facility in the loop."""
import pytest

from beamkit import BeamkitError, backends, decks, identity, paths, records, runs
from beamkit.backends import fermilab

SHA = "e470313" + "0" * 33


class Stub:
    """A backend as a bag of functions that record their calls."""

    Block = fermilab.Block          # records.from_dict builds the block with it

    def __init__(self, *, taken=lambda name: False, fail_enqueue=None, njobs_from_site=None):
        self.calls = []
        self._taken, self._fail, self._njobs = taken, fail_enqueue, njobs_from_site

    def resolve_identity(self, req):
        self.calls.append("resolve_identity")
        return identity.Identity(run_as="self", owner="u", dev_dir=None)

    def validate(self, req, ident):
        self.calls.append("validate")
        if req.walltime_s is not None:
            raise BeamkitError("walltime_s applies to site='nersc' only")
        return req if req.slice_size is not None else runs.replace(req, slice_size=req.njobs)

    def taken(self, ident, tag):
        self.calls.append("taken")
        return self._taken

    def retryable(self, rec):
        return rec.state == "enqueue_failed" and rec.block.campaign_id is None

    def new_block(self, req, ident, pin, run_id, dsconf):
        self.calls.append("new_block")
        return fermilab.Block(prodtools={"root": "/pt", "commit": "c" * 40, "dev_dir": None})

    def enqueue(self, rec, req, ident, pin, rdir):
        self.calls.append("enqueue")
        self.record_on_disk_at_enqueue = (rdir / "run.json").is_file()
        if self._fail:
            raise self._fail
        rec.block.campaign_id = 7
        if self._njobs is not None:
            rec.njobs = self._njobs

    def after_failure(self, rec, ident):
        self.calls.append("after_failure")
        return "; stub outcome"

    def submit(self, rec, ident, confirm):
        self.calls.append("submit")
        rec.state = "submitted"
        records.save(rec, paths.runs_dir())


@pytest.fixture
def stub(monkeypatch, tmp_path):
    d = tmp_path / "deckcache" / SHA[:12]
    d.mkdir(parents=True)
    (d / "Mu2E.in").write_text("param -unset First_Event=1\n")
    monkeypatch.setattr(decks, "materialize", lambda url, ref, cache: decks.DeckPin(url, ref, SHA, str(d), True))
    s = Stub()
    real_get = backends.get
    monkeypatch.setattr(backends, "get", lambda site: s if site == "stub" else real_get(site))
    monkeypatch.setattr(backends, "SITES", (*backends.SITES, "stub"))   # records.from_dict checks membership
    return s


def _req(**kw):
    base = dict(tag="T", run_as="self", site="stub", deck_ref=SHA, events_per_job=10, njobs=3)
    base.update(kw)
    return runs.RunRequest(**base)


def test_happy_path_order_and_record(stub):
    out = runs.create(_req())
    assert stub.calls == ["resolve_identity", "validate", "taken", "new_block", "enqueue", "submit"]
    assert out["state"] == "submitted" and out["site"] == "stub" and out["datasets"] == ["nts.u.T.e470313.root"]
    assert stub.record_on_disk_at_enqueue, "the record is saved before the first remote effect"


def test_refused_input_burns_no_dsconf_and_writes_no_record(stub):
    with pytest.raises(BeamkitError, match="walltime_s applies"):
        runs.create(_req(walltime_s=5))
    assert "taken" not in stub.calls and not (paths.runs_dir()).exists()


def test_missing_main_input_is_refused_before_any_probe(stub):
    with pytest.raises(BeamkitError, match=r"main_input 'Nope\.in' not found in deck dir"):
        runs.create(_req(main_input="Nope.in"))
    assert "taken" not in stub.calls and not (paths.runs_dir()).exists()


def test_enqueue_failure_is_annotated_with_the_backend_outcome(stub):
    stub._fail = RuntimeError("boom")
    with pytest.raises(BeamkitError, match=r"run T\.e470313: enqueue failed \(boom\); stub outcome"):
        runs.create(_req())
    rec = records.load("T.e470313", paths.runs_dir())
    assert rec.state == "enqueue_failed" and rec.error == "RuntimeError: boom"
    assert stub.calls[-2:] == ["enqueue", "after_failure"] and "submit" not in stub.calls


def test_njobs_mismatch_raises_after_save_and_before_submit(stub):
    stub._njobs = 5
    with pytest.raises(BeamkitError, match="holds 5 jobs but this call asked for 3"):
        runs.create(_req())
    rec = records.load("T.e470313", paths.runs_dir())
    assert rec.njobs == 5 and rec.state == "created" and "submit" not in stub.calls


def test_submit_false_never_submits(stub):
    out = runs.create(_req(submit=False))
    assert out["state"] == "created" and "submit" not in stub.calls


def test_retryable_prior_attempt_is_overwritten_and_other_dirs_refused(stub):
    stub._fail = RuntimeError("boom")
    with pytest.raises(BeamkitError):
        runs.create(_req())
    stub._fail = None
    out = runs.create(_req())
    assert out["state"] == "submitted"
    with pytest.raises(BeamkitError, match="already exists; a run id is never reused"):
        runs.create(_req())


def test_dsconf_collision_takes_the_next_suffix(stub):
    stub._taken = lambda name: name == "cnf.u.T.e470313.0.tar"
    out = runs.create(_req())
    assert out["dsconf"] == "e470313-001" and out["run_id"] == "T.e470313-001"
