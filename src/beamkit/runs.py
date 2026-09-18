"""Run creation, once. Every caller value is refused before the deck fetch
and the site probe (a refused call burns no dsconf and writes no record);
the record is saved before the first remote effect; a failed enqueue leaves
an enqueue_failed record the next call may overwrite."""
from dataclasses import dataclass, replace
from pathlib import Path

from beamkit import BeamkitError, __version__, backends, compose, decks, naming, paths, records
from beamkit.decks import DEFAULT_DECK_URL


@dataclass(frozen=True)
class RunRequest:
    """Every caller value of run_beamline. slice_size is Fermilab's and
    walltime_s NERSC's; each backend refuses the other's in validate()."""
    tag: str
    run_as: str
    site: str = "fermilab"
    deck_ref: str | None = None
    deck_dir: str | None = None
    deck_url: str = DEFAULT_DECK_URL
    params: dict | None = None
    events_per_job: int = 1000
    njobs: int = 1
    main_input: str = "Mu2E.in"
    outloc: str = "scratch"
    dsconf: str | None = None
    submit: bool = True
    confirm: bool = False
    slice_size: int | None = None
    walltime_s: int | None = None


def create(req: RunRequest) -> dict:
    backend = backends.get(req.site)
    ident = backend.resolve_identity(req)
    naming.validate_tag(req.tag)
    params = compose.validate_inputs(events_per_job=req.events_per_job, njobs=req.njobs, outloc=req.outloc,
                                     params=req.params)
    req = backend.validate(replace(req, params=params), ident)
    pin = decks.pin(req.deck_ref, req.deck_dir, req.deck_url, paths.decks_dir(), ident.production)
    if not (Path(pin.dir) / req.main_input).is_file():
        raise BeamkitError(f"main_input {req.main_input!r} not found in deck dir {pin.dir}")
    runs_dir = paths.runs_dir()

    def retryable(run_id, runs_dir):
        rec = records.try_load(run_id, runs_dir)
        return rec is not None and backend.retryable(rec)

    dsconf = naming.allocate_dsconf(ident.owner, req.tag, naming.dsconf_base(pin.sha),
                                    backend.taken(ident, req.tag), explicit=req.dsconf)
    run_id = naming.run_id(req.tag, dsconf)
    rdir = records.claim_run_dir(runs_dir, run_id, retryable)
    rec = records.RunRecord(run_id=run_id, tag=req.tag, dsconf=dsconf, owner=ident.owner, run_as=req.run_as,
                            deck=pin.as_record(), params=req.params, events_per_job=req.events_per_job,
                            njobs=req.njobs, outloc=req.outloc, slice_size=req.slice_size, state="created",
                            site=req.site, block=backend.new_block(req, ident, pin, run_id, dsconf),
                            datasets=[naming.dataset(ident.owner, req.tag, dsconf)],
                            created=records.now_utc(), beamkit_version=__version__)
    records.save(rec, runs_dir)
    try:
        backend.enqueue(rec, req, ident, pin, rdir)
    except Exception as e:
        rec.state, rec.error = "enqueue_failed", f"{type(e).__name__}: {e}"
        outcome = backend.after_failure(rec, ident)
        records.save(rec, runs_dir)
        raise BeamkitError(f"run {run_id}: enqueue failed ({e}){outcome}") from e
    records.save(rec, runs_dir)
    if rec.njobs != req.njobs:
        raise BeamkitError(f"run {run_id}: the site reports the run holds {rec.njobs} jobs but this call asked "
                           f"for {req.njobs}; the record now carries {rec.njobs} and nothing was submitted. "
                           f"Understand the difference, then make_recoveries({run_id!r}, {req.run_as!r}) "
                           f"submits it")
    if req.submit:
        backend.submit(rec, ident, req.confirm)
    return rec.to_dict()
