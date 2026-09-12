"""The beamkit tools; server.py registers them with FastMCP as they are, so
every parameter is annotated and `run_as` has no default where a call
writes. Orchestration only: naming, decks, compose, records do the work;
bridge talks to prodtools. Every refusal is a BeamkitError."""
import json
import sys
from typing import Optional

from beamkit import (BeamkitError, __version__, backends, beamfile, bridge, compose, decks, identity, naming,
                     paths, publishing, records)
from beamkit.backends import nersc as nersc_backend
from beamkit.decks import DEFAULT_DECK_URL

SLICE_MAX = 10000


def _outloc(outloc, ident):
    """beamkit holds the output location and the account at the same moment,
    so it can refuse the pair prodtools only discovers on the worker.
    Membership in OUTLOCS is compose.validate_inputs' rule; this is the
    one that needs the identity."""
    if outloc == "disk" and not ident.production:
        raise BeamkitError("outloc='disk' is /mu2e/persistent/datasets, where only mu2epro has "
                           "storage.modify: every worker would run g4bl to completion and then 403 in "
                           "pushOutput. Use outloc='scratch', or run_as='mu2epro'")


def _slice_size(slice_size, njobs):
    if slice_size is None:
        return min(njobs, SLICE_MAX)
    if isinstance(slice_size, bool) or not isinstance(slice_size, int) or not 1 <= slice_size <= SLICE_MAX:
        raise BeamkitError(f"slice_size must be an int in 1..{SLICE_MAX}, got {slice_size!r}")
    return slice_size


def _summary(output, n=5):
    lines = [l for l in (output or "").splitlines() if l.strip()]
    return "\n".join(lines[-n:])


def _pin(deck_ref, deck_dir, deck_url, ident):
    return decks.pin(deck_ref, deck_dir, deck_url, paths.decks_dir(), ident.production)


def _is_retryable_run_dir(run_id, runs_dir) -> bool:
    """A run dir left by a push that never reached prodtools: state
    'enqueue_failed' with no campaign. Nothing was created anywhere else, so
    the retry overwrites it in place. Any other run dir stays refused."""
    try:
        rec = records.load(run_id, runs_dir)
    except records.RecordError:
        return False
    return rec.state == "enqueue_failed" and rec.campaign_id is None


def _dataset(rec):
    return naming.dataset(rec.owner, rec.tag, rec.dsconf)


def _after_failed_push(rec, ident) -> str:
    """What a failed push_cnf left behind, and what the record should say.

    prodtools' _ENQUEUE_RECOVERY: the cnf may have reached SAM, and the
    campaign may have been created, before the error. Probe both. A cnf in
    SAM with its campaign in the ledger is a run that exists -- adopt the
    campaign into the record (state 'created') so make_recoveries submits
    it, instead of burning the dsconf and orphaning the campaign. A cnf in
    SAM with no campaign is a burned dsconf; a cnf not in SAM leaves the
    dsconf free and the run dir retryable in place."""
    name = naming.cnf_name(rec.owner, rec.tag, rec.dsconf)
    try:
        landed = bridge.cnf_exists(name)
    except Exception as e:
        return (f"; whether {name} reached SAM could not be determined "
                f"({type(e).__name__}: {e}), so check SAM before retrying")
    if not landed:
        return (f"; {name} is not in SAM, so the dsconf {rec.dsconf!r} is free and this call can be "
                f"retried once the cause is fixed")
    try:
        camps = bridge.campaigns(mine=ident.mine)
    except Exception as e:
        return (f"; {name} is in SAM, so the dsconf {rec.dsconf!r} is burned, but the ledger could not "
                f"be read ({type(e).__name__}: {e}): check `submissions status` for a campaign on it "
                f"before retrying")
    camp = next((c for c in camps if c.get("tarball") == name), None)
    if camp is None:
        return (f"; {name} is in SAM with no campaign, so the dsconf {rec.dsconf!r} is burned and the "
                f"next call allocates the next suffix")
    rec.campaign_id, rec.tarball, rec.state = camp["id"], name, "created"
    rec.datasets = [_dataset(rec)]
    return (f"; {name} is in SAM and campaign {camp['id']} exists for it, so the record now carries "
            f"that campaign in state 'created' and nothing was submitted: "
            f"make_recoveries({rec.run_id!r}, {ident.run_as!r}) submits it")


def run_beamline(tag: str, run_as: str, deck_ref: Optional[str] = None, params: Optional[dict] = None,
                 events_per_job: int = 1000, njobs: int = 1, main_input: str = "Mu2E.in",
                 outloc: str = "scratch", dsconf: Optional[str] = None, slice_size: Optional[int] = None,
                 submit: bool = True, confirm: bool = False, deck_dir: Optional[str] = None,
                 deck_url: str = DEFAULT_DECK_URL, site: str = "fermilab",
                 walltime_s: int = nersc_backend.WALLTIME_DEFAULT) -> dict:
    """Pin the deck, allocate desc=tag and dsconf=<sha7>, then either
    register the cnf and create its campaign through prodtools and submit
    it in one tick (site='fermilab'), or build the cnf locally, lay the run
    out on CFS and submit one Slurm job per procs_per_node indices through
    the IRI API (site='nersc'). Every caller value is refused before the
    deck fetch and the remote probe, so a refused call burns no dsconf and
    writes no record."""
    backends.validate_site(site)
    if site == "nersc":
        if slice_size is not None:
            raise BeamkitError("slice_size applies to site='fermilab' only; a NERSC run is sliced by "
                               "procs_per_node from nersc.toml")
        return nersc_backend.run_beamline(tag=tag, run_as=run_as, deck_ref=deck_ref, params=params,
                                          events_per_job=events_per_job, njobs=njobs, main_input=main_input,
                                          outloc=outloc, dsconf=dsconf, submit=submit, deck_dir=deck_dir,
                                          deck_url=deck_url, walltime_s=walltime_s)
    if walltime_s != nersc_backend.WALLTIME_DEFAULT:
        raise BeamkitError("walltime_s applies to site='nersc' only; the Fermilab path takes its resources "
                           "from prodtools")
    ident = identity.resolve(run_as, confirm)
    dev_dir = ident.dev_dir_for_shipping()
    naming.validate_tag(tag)
    params = compose.validate_inputs(events_per_job=events_per_job, njobs=njobs, outloc=outloc, params=params)
    _outloc(outloc, ident)
    slice_size = _slice_size(slice_size, njobs)
    pin = _pin(deck_ref, deck_dir, deck_url, ident)
    dsconf = naming.allocate_dsconf(ident.owner, tag, naming.dsconf_base(pin.sha), bridge.cnf_exists, explicit=dsconf)
    entry = compose.entry(tag=tag, dsconf=dsconf, deck_dir=pin.dir, main_input=main_input,
                          events_per_job=events_per_job, njobs=njobs, outloc=outloc, params=params)
    run_id = naming.run_id(tag, dsconf)
    runs_dir = paths.runs_dir()
    rdir = records.run_dir(runs_dir, run_id)
    if rdir.exists() and not _is_retryable_run_dir(run_id, runs_dir):
        raise BeamkitError(f"run dir {rdir} already exists; a run id is never reused")
    rdir.mkdir(parents=True, exist_ok=True)
    entry_path = compose.write_entry_json(entry, rdir / "entry.json")
    rec = records.RunRecord(run_id=run_id, tag=tag, dsconf=dsconf, owner=ident.owner, run_as=run_as,
                            deck=pin.as_record(), params=params, events_per_job=events_per_job,
                            njobs=njobs, outloc=outloc, slice_size=slice_size, state="created",
                            created=records.now_utc(),
                            prodtools=dict(bridge.prodtools_info(), dev_dir=dev_dir),
                            beamkit_version=__version__)
    records.save(rec, runs_dir)
    try:
        pushed = bridge.push_cnf(entry_path, tag, dsconf, slice_size, run_as, confirm, prodtools_dir=dev_dir)
    except Exception as e:
        rec.state, rec.error = "enqueue_failed", f"{type(e).__name__}: {e}"
        outcome = _after_failed_push(rec, ident)
        records.save(rec, runs_dir)
        raise BeamkitError(f"run {run_id}: push_cnf failed ({e}){outcome}") from e
    rec.campaign_id, rec.tarball, rec.datasets = pushed["campaign_id"], pushed["tarball"], [_dataset(rec)]
    # the campaign's njobs is what missing_indices is measured against; the
    # requested count is only what we asked for
    rec.njobs = pushed["njobs"]
    records.save(rec, runs_dir)
    if rec.njobs != njobs:
        raise BeamkitError(f"run {run_id}: prodtools reports campaign {rec.campaign_id} holds {rec.njobs} jobs "
                           f"but this call asked for {njobs}; the record now carries {rec.njobs} and nothing "
                           f"was submitted. Understand the difference, then make_recoveries({run_id!r}, "
                           f"{run_as!r}) submits it")
    if submit:
        _tick_into(rec, run_as, confirm, runs_dir,
                   failure=f"run {run_id}: campaign {rec.campaign_id} was created but the first tick failed; "
                           f"call make_recoveries({run_id!r}, {run_as!r}) to submit it",
                   campaign_id=rec.campaign_id)
    return rec.to_dict()


def _tick_into(rec, run_as, confirm, runs_dir, failure, campaign_id):
    """One prodtools tick, filed into the record with the state it leaves
    the run in. campaign_id=None is the bare tick: every active campaign
    in the ledger is topped up."""
    try:
        t = bridge.tick(run_as, campaign_id, confirm)
    except Exception as e:
        rec.error = f"{type(e).__name__}: {e}"
        records.save(rec, runs_dir)
        raise BeamkitError(f"{failure}: {e}") from e
    rec.ticks.append({"when": records.now_utc(), "rc": t["rc"], "needs_attention": t["needs_attention"],
                      "summary": _summary(t["output"])})
    rec.error = None
    rec.state = "needs_attention" if t["needs_attention"] else "submitted"
    records.save(rec, runs_dir)
    return t


def _campaign(campaign_id, mine) -> dict:
    """This run's campaign as the ledger holds it. Missing is an error, not
    None: a tick for a campaign the ledger does not know would be a no-op
    reported as success."""
    for c in bridge.campaigns(mine):
        if c["id"] == campaign_id:
            return c
    raise BeamkitError(f"campaign {campaign_id} is not in the {'personal' if mine else 'production'} ledger")


def make_recoveries(run_id: str, run_as: str, confirm: bool = False) -> dict:
    """One prodtools tick for this run. While the campaign is active the tick
    is scoped to it. Once prodtools has marked it complete (every slice
    submitted, rows still verifying) a scoped tick is refused as not
    active, so the bare tick is used: its verify/recovery pass reaches this
    run's rows and its top-up feeds every other active campaign in the
    ledger. The result names which form ran."""
    ident = identity.resolve(run_as, confirm)
    rec = records.load(run_id, paths.runs_dir())
    if rec.site == "nersc":
        raise BeamkitError(f"run {run_id}: no recovery on nersc; submit a new run (beamline_status reports "
                           f"the missing indices)")
    if rec.campaign_id is None:
        raise BeamkitError(f"run {run_id} has no campaign (state {rec.state!r}); nothing to recover")
    if ident.run_as != rec.run_as:
        raise BeamkitError(f"run {run_id} lives in the run_as={rec.run_as!r} ledger; tick it as that identity")
    camp = _campaign(rec.campaign_id, mine=ident.mine)
    scoped = camp["state"] == "active"
    t = _tick_into(rec, run_as, confirm, paths.runs_dir(),
                   failure=f"run {run_id}: tick of campaign {rec.campaign_id} failed",
                   campaign_id=rec.campaign_id if scoped else None)
    return {"run_id": run_id, "campaign_id": rec.campaign_id, "campaign_state": camp["state"],
            "rc": t["rc"], "needs_attention": t["needs_attention"], "output": t["output"],
            "tick_scope": "campaign" if scoped else "ledger",
            "note": ("the verify/recovery pass covered every active campaign in this ledger, not only this run"
                     if scoped else
                     f"campaign {rec.campaign_id} is {camp['state']!r}, so this was the bare tick: the "
                     f"verify/recovery pass reached its rows and the top-up fed every active campaign in "
                     f"this ledger")}


def submit_run(run_id: str, run_as: str) -> dict:
    """NERSC runs only: submit the Slurm jobs of a run created with
    submit=False, or the jobs a partial submit did not reach. The Fermilab
    path submits through make_recoveries."""
    return nersc_backend.submit_run(run_id, run_as)


def beamline_status(run_id: str) -> dict:
    """The run record merged with prodtools' campaign_status (Fermilab) or
    with the Slurm job states and CFS output counts (NERSC)."""
    rec = records.load(run_id, paths.runs_dir())
    if rec.site == "nersc":
        n = nersc_backend.status(rec)
        return {"record": rec.to_dict(), "campaign": None, "nersc": n}
    campaign = None
    if rec.campaign_id is not None:
        campaign = bridge.campaign_status(rec.campaign_id, mine=identity.for_record(rec).mine)
    return {"record": rec.to_dict(), "campaign": campaign, "nersc": None}


def list_beamline_runs(state: Optional[str] = None) -> dict:
    """Run records under this user's beamkit dir, newest first."""
    recs = records.list_runs(paths.runs_dir(), state=state)
    return {"records_dir": str(paths.runs_dir()), "count": len(recs), "runs": [r.to_dict() for r in recs]}


def beamline_outputs(run_id: str) -> dict:
    """Files of the run's nts dataset with sizes and paths: dCache via SAM
    for a Fermilab run, CFS via one ls for a NERSC run."""
    rec = records.load(run_id, paths.runs_dir())
    if rec.site == "nersc":
        return nersc_backend.outputs(rec)
    files = bridge.dataset_files(_dataset(rec), rec.outloc)
    return {"run_id": run_id, "dataset": _dataset(rec), "location": rec.outloc,
            "n_files": len(files), "total_size": sum(f["size"] for f in files), "files": files}


def make_beamfile(run_id: str, flavor: str, run_as: str, plane: str = "Z3712", cuts: Optional[dict] = None,
                  publish: bool = False, location: Optional[str] = None, confirm: bool = False,
                  label: Optional[str] = None) -> dict:
    """A BLTrackFile from whatever nts files the run has in SAM. No
    completeness check: pot counts the files that exist. flavor selects
    the cut table; label names the files and defaults to the flavor."""
    ident = identity.resolve(run_as, confirm, writes=publish)
    label = flavor if label is None else label
    resolved = beamfile.resolve_cuts(flavor, cuts)
    beamfile.validate_label(label)
    beamfile.validate_plane(plane)
    if publish:
        location = location or ident.default_publish_location
        publishing.check_ready(location)
    rec = records.load(run_id, paths.runs_dir())
    # the file is NAMED from the record's identity and PUSHED as run_as; a
    # mismatch publishes one owner's name under the other account
    if publish and ident.run_as != rec.run_as:
        raise BeamkitError(f"run {run_id} was created with run_as={rec.run_as!r}, so its beam file is "
                           f"named etc.{rec.owner}.…; publishing it as run_as={run_as!r} would push "
                           f"that name under the other identity. Pass run_as={rec.run_as!r}")
    out_dir = paths.beamfiles_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_txt = out_dir / f"{run_id}.{label}.txt"
    out_json = out_dir / f"{run_id}.{label}.json"
    if out_txt.exists() or out_json.exists():
        raise BeamkitError(f"{out_txt} exists; a beam file is never overwritten (pick another label)")
    dataset = _dataset(rec)
    files = bridge.dataset_files(dataset, rec.outloc)
    if not files:
        raise BeamkitError(f"no files in {dataset} at {rec.outloc}: nothing to build a beam file from")
    stats = beamfile.build([f["path"] for f in files], plane, resolved, out_txt)
    side = {"run_id": run_id, "flavor": flavor, "label": label, "cuts": resolved, "plane": plane,
            "path": str(out_txt), "sha256": stats["sha256"], "size": stats["size"], "rows": stats["rows_out"],
            "rows_in": stats["rows_in"], "dropped": stats["dropped"],
            "pot": len(files) * rec.events_per_job, "n_files": len(files),
            "missing_indices": beamfile.missing_indices([f["index"] for f in files], rec.njobs),
            "source_files": [f["name"] for f in files], "sam_name": None, "location": None,
            "created": records.now_utc()}
    if publish:
        sam_name = naming.beamfile_name(rec.owner, rec.tag, label, rec.dsconf)
        publishing.publish(out_txt, out_dir / sam_name, location, side["source_files"], run_as, confirm)
        side["sam_name"], side["location"] = sam_name, location
    out_json.write_text(json.dumps(side, indent=2) + "\n")
    rec.beamfiles.append(side)
    records.save(rec, paths.runs_dir())
    return side


def get_server_info() -> dict:
    """beamkit version, prodtools root and commit, directories, limits."""
    return {"name": "beamkit", "version": __version__, "python": sys.executable,
            "prodtools": bridge.prodtools_info(), "dev_dir": identity.dev_dir_from_env(),
            "deck_url": DEFAULT_DECK_URL,
            "decks_dir": str(paths.decks_dir()), "records_dir": str(paths.runs_dir()),
            "beamfiles_dir": str(paths.beamfiles_dir()), "slice_max": SLICE_MAX}
