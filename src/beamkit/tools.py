"""The beamkit tools. Orchestration only: naming, decks, compose, records
do the work; bridge talks to prodtools."""
import json
import sys

from beamkit import __version__, beamfile, bridge, compose, decks, identity, naming, paths, publishing, records
from beamkit.decks import DEFAULT_DECK_URL

SLICE_MAX = 10000


class ToolError(RuntimeError):
    pass


def _identity(run_as, confirm, writes=True) -> identity.Identity:
    """Resolved before any side effect, so a refused call burns no dsconf
    and writes no record."""
    try:
        return identity.resolve(run_as, confirm, writes=writes)
    except identity.IdentityError as e:
        raise ToolError(str(e)) from e


def _outloc(outloc, ident):
    """beamkit holds the output location and the account at the same moment,
    so it can refuse the pair prodtools only discovers on the worker.
    Membership in OUTLOCS is compose.validate_inputs' rule; this is the
    one that needs the identity."""
    if outloc == "disk" and not ident.production:
        raise ToolError("outloc='disk' is /mu2e/persistent/datasets, where only mu2epro has "
                        "storage.modify: every worker would run g4bl to completion and then 403 in "
                        "pushOutput. Use outloc='scratch', or run_as='mu2epro'")
    return outloc


def _slice_size(slice_size, njobs):
    if slice_size is None:
        return min(njobs, SLICE_MAX)
    if isinstance(slice_size, bool) or not isinstance(slice_size, int) or not 1 <= slice_size <= SLICE_MAX:
        raise ToolError(f"slice_size must be an int in 1..{SLICE_MAX}, got {slice_size!r}")
    return slice_size


def _summary(output, n=5):
    lines = [l for l in (output or "").splitlines() if l.strip()]
    return "\n".join(lines[-n:])


def _pin(deck_ref, deck_dir, deck_url, ident):
    if (deck_ref is None) == (deck_dir is None):
        raise ToolError("pass exactly one of deck_ref (a commit sha or tag) or deck_dir (a local checkout)")
    if deck_dir is not None:
        if ident.production:
            raise ToolError("deck_dir is a development option: run_as='self' only")
        try:
            pin = decks.inspect_local(deck_dir)
        except decks.DeckError as e:
            raise ToolError(str(e)) from e
        if pin.sha is None:
            raise ToolError(f"deck_dir {deck_dir} is not a git checkout; the dsconf is derived from the commit")
        return pin
    try:
        return decks.materialize(deck_url, deck_ref, paths.decks_dir())
    except decks.DeckError as e:
        raise ToolError(str(e)) from e


def _is_retryable_run_dir(run_id, runs_dir) -> bool:
    """A run dir left by a push that never reached prodtools: state
    'enqueue_failed' with no campaign. Nothing was created anywhere else, so
    the retry overwrites it in place. Any other run dir stays refused."""
    try:
        rec = records.load(run_id, runs_dir)
    except records.RecordError:
        return False
    return rec.state == "enqueue_failed" and rec.campaign_id is None


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


def run_beamline(tag, deck_ref=None, run_as="self", params=None, events_per_job=1000, njobs=1,
                 main_input="Mu2E.in", outloc="scratch", dsconf=None, slice_size=None,
                 submit=True, confirm=False, deck_dir=None, deck_url=DEFAULT_DECK_URL) -> dict:
    ident = _identity(run_as, confirm)
    try:
        dev_dir = ident.dev_dir_for_shipping()
        naming.validate_tag(tag)
        params = compose.validate_inputs(events_per_job=events_per_job, njobs=njobs, outloc=outloc, params=params)
    except (identity.IdentityError, naming.NamingError, compose.ComposeError) as e:
        raise ToolError(str(e)) from e
    _outloc(outloc, ident)
    slice_size = _slice_size(slice_size, njobs)
    owner = ident.owner
    pin = _pin(deck_ref, deck_dir, deck_url, ident)
    try:
        dsconf = naming.allocate_dsconf(owner, tag, naming.dsconf_base(pin.sha), bridge.cnf_exists, explicit=dsconf)
        entry = compose.entry(tag=tag, dsconf=dsconf, deck_dir=pin.dir, main_input=main_input,
                              events_per_job=events_per_job, njobs=njobs, outloc=outloc, params=params)
    except (naming.NamingError, compose.ComposeError) as e:
        raise ToolError(str(e)) from e
    run_id = f"{tag}.{dsconf}"
    runs_dir = paths.runs_dir()
    rdir = records.run_dir(runs_dir, run_id)
    if rdir.exists() and not _is_retryable_run_dir(run_id, runs_dir):
        raise ToolError(f"run dir {rdir} already exists; a run id is never reused")
    rdir.mkdir(parents=True, exist_ok=True)
    entry_path = compose.write_entry_json(entry, rdir / "entry.json")
    rec = records.RunRecord(run_id=run_id, tag=tag, dsconf=dsconf, owner=owner, run_as=run_as,
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
        raise ToolError(f"run {run_id}: push_cnf failed ({e}){outcome}") from e
    rec.campaign_id, rec.tarball = pushed["campaign_id"], pushed["tarball"]
    # prodtools echoes the entry's outloc key ("nts.*.root"), a glob, not a name;
    # the dataset this run actually writes is the one _dataset composes.
    rec.datasets, rec.prodtools_datasets = [_dataset(rec)], list(pushed["datasets"])
    # the campaign's njobs is what missing_indices is measured against; the
    # requested count is only what we asked for
    rec.njobs = pushed["njobs"]
    records.save(rec, runs_dir)
    if rec.njobs != njobs:
        raise ToolError(f"run {run_id}: prodtools reports campaign {rec.campaign_id} holds {rec.njobs} jobs "
                        f"but this call asked for {njobs}; the record now carries {rec.njobs} and nothing "
                        f"was submitted. Understand the difference, then make_recoveries({run_id!r}, "
                        f"{run_as!r}) submits it")
    if submit:
        t = _tick_into(rec, run_as, confirm, runs_dir,
                       failure=f"run {run_id}: campaign {rec.campaign_id} was created but the first tick failed; "
                               f"call make_recoveries({run_id!r}, {run_as!r}) to submit it",
                       campaign_id=rec.campaign_id)
        _state_after_tick(rec, t)
        records.save(rec, runs_dir)
    return rec.to_dict()


def _state_after_tick(rec, tick):
    rec.state = "needs_attention" if tick["needs_attention"] else "submitted"


def _tick_into(rec, run_as, confirm, runs_dir, failure, campaign_id):
    """One prodtools tick, filed into the record. campaign_id=None is the
    bare tick: every active campaign in the ledger is topped up."""
    try:
        t = bridge.tick(run_as, campaign_id, confirm)
    except Exception as e:
        rec.error = f"{type(e).__name__}: {e}"
        records.save(rec, runs_dir)
        raise ToolError(f"{failure}: {e}") from e
    rec.ticks.append({"when": records.now_utc(), "rc": t["rc"], "needs_attention": t["needs_attention"],
                      "summary": _summary(t["output"])})
    rec.error = None
    records.save(rec, runs_dir)
    return t


def _load(run_id):
    try:
        return records.load(run_id, paths.runs_dir())
    except records.RecordError as e:
        raise ToolError(str(e)) from e


def _campaign(campaign_id, mine) -> dict:
    """This run's campaign as the ledger holds it. Missing is an error, not
    None: a tick for a campaign the ledger does not know would be a no-op
    reported as success."""
    try:
        camps = bridge.campaigns(mine)
    except bridge.BridgeError as e:
        raise ToolError(f"could not read the ledger: {e}") from e
    for c in camps:
        if c["id"] == campaign_id:
            return c
    raise ToolError(f"campaign {campaign_id} is not in the {'personal' if mine else 'production'} ledger")


def make_recoveries(run_id, run_as, confirm=False) -> dict:
    """One prodtools tick for this run. While the campaign is active the tick
    is scoped to it. Once prodtools has marked it complete (every slice
    submitted, rows still verifying) a scoped tick is refused as not
    active, so the bare tick is used: its verify/recovery pass reaches this
    run's rows and its top-up feeds every other active campaign in the
    ledger. The result names which form ran."""
    ident = _identity(run_as, confirm)
    rec = _load(run_id)
    if rec.campaign_id is None:
        raise ToolError(f"run {run_id} has no campaign (state {rec.state!r}); nothing to recover")
    if ident.run_as != rec.run_as:
        raise ToolError(f"run {run_id} lives in the run_as={rec.run_as!r} ledger; tick it as that identity")
    camp = _campaign(rec.campaign_id, mine=ident.mine)
    scoped = camp["state"] == "active"
    t = _tick_into(rec, run_as, confirm, paths.runs_dir(),
                   failure=f"run {run_id}: tick of campaign {rec.campaign_id} failed",
                   campaign_id=rec.campaign_id if scoped else None)
    _state_after_tick(rec, t)
    records.save(rec, paths.runs_dir())
    return {"run_id": run_id, "campaign_id": rec.campaign_id, "campaign_state": camp["state"],
            "rc": t["rc"], "needs_attention": t["needs_attention"], "output": t["output"],
            "tick_scope": "campaign" if scoped else "ledger", "ledger_wide": True,
            "note": ("the verify/recovery pass covered every active campaign in this ledger, not only this run"
                     if scoped else
                     f"campaign {rec.campaign_id} is {camp['state']!r}, so this was the bare tick: the "
                     f"verify/recovery pass reached its rows and the top-up fed every active campaign in "
                     f"this ledger")}


def beamline_status(run_id) -> dict:
    rec = _load(run_id)
    campaign = None
    if rec.campaign_id is not None:
        campaign = bridge.campaign_status(rec.campaign_id, mine=identity.for_record(rec).mine)
    return {"record": rec.to_dict(), "campaign": campaign}


def list_beamline_runs(state=None) -> dict:
    try:
        recs = records.list_runs(paths.runs_dir(), state=state)
    except records.RecordError as e:
        raise ToolError(str(e)) from e
    return {"records_dir": str(paths.runs_dir()), "count": len(recs), "runs": [r.to_dict() for r in recs]}


def _dataset(rec):
    return f"nts.{rec.owner}.{rec.tag}.{rec.dsconf}.root"


def beamline_outputs(run_id) -> dict:
    rec = _load(run_id)
    try:
        files = bridge.dataset_files(_dataset(rec), rec.outloc)
    except bridge.BridgeError as e:
        raise ToolError(f"beamline_outputs: dataset_files failed: {e}") from e
    return {"run_id": run_id, "dataset": _dataset(rec), "location": rec.outloc,
            "n_files": len(files), "total_size": sum(f["size"] for f in files), "files": files}


def make_beamfile(run_id, flavor, run_as, plane="Z3712", cuts=None, publish=False,
                  location=None, confirm=False, label=None) -> dict:
    """A BLTrackFile from whatever nts files the run has in SAM. No
    completeness check: pot counts the files that exist. flavor selects
    the cut table; label names the files and defaults to the flavor."""
    ident = _identity(run_as, confirm, writes=publish)
    label = flavor if label is None else label
    try:
        resolved = beamfile.resolve_cuts(flavor, cuts)
        beamfile.validate_label(label)
        beamfile.validate_plane(plane)
    except beamfile.BeamfileError as e:
        raise ToolError(str(e)) from e
    if publish:
        location = location or ident.default_publish_location
        try:
            publishing.check_ready(location)
        except publishing.PublishError as e:
            raise ToolError(f"make_beamfile(publish=True): {e}") from e
    rec = _load(run_id)
    # the file is NAMED from the record's identity and PUSHED as run_as; a
    # mismatch publishes one owner's name under the other account
    if publish and ident.run_as != rec.run_as:
        raise ToolError(f"run {run_id} was created with run_as={rec.run_as!r}, so its beam file is "
                        f"named etc.{rec.owner}.…; publishing it as run_as={run_as!r} would push "
                        f"that name under the other identity. Pass run_as={rec.run_as!r}")
    out_dir = paths.beamfiles_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_txt = out_dir / f"{run_id}.{label}.txt"
    out_json = out_dir / f"{run_id}.{label}.json"
    if out_txt.exists() or out_json.exists():
        raise ToolError(f"{out_txt} exists; a beam file is never overwritten (pick another label)")
    dataset = _dataset(rec)
    try:
        files = bridge.dataset_files(dataset, rec.outloc)
    except bridge.BridgeError as e:
        raise ToolError(f"make_beamfile: dataset_files failed: {e}") from e
    if not files:
        raise ToolError(f"no files in {dataset} at {rec.outloc}: nothing to build a beam file from")
    present = [f["index"] for f in files]
    try:
        stats = beamfile.build([f["path"] for f in files], plane, resolved, out_txt)
    except beamfile.BeamfileError as e:
        raise ToolError(str(e)) from e
    side = {"run_id": run_id, "flavor": flavor, "label": label, "cuts": resolved, "plane": plane,
            "path": str(out_txt), "sha256": stats["sha256"], "size": stats["size"], "rows": stats["rows_out"],
            "rows_in": stats["rows_in"], "dropped": stats["dropped"],
            "pot": len(files) * rec.events_per_job, "n_files": len(files),
            "missing_indices": beamfile.missing_indices(present, rec.njobs),
            "source_files": [f["name"] for f in files], "sam_name": None, "location": None,
            "created": records.now_utc()}
    if publish:
        sam_name = f"etc.{rec.owner}.{rec.tag}Beam-{label}.{rec.dsconf}.txt"
        try:
            publishing.publish(out_txt, out_dir / sam_name, location, side["source_files"], run_as, confirm)
        except publishing.PublishError as e:
            raise ToolError(str(e)) from e
        side["sam_name"], side["location"] = sam_name, location
    out_json.write_text(json.dumps(side, indent=2) + "\n")
    rec.beamfiles.append(side)
    records.save(rec, paths.runs_dir())
    return side


def get_server_info() -> dict:
    return {"name": "beamkit", "version": __version__, "python": sys.executable,
            "prodtools": bridge.prodtools_info(), "dev_dir": identity.dev_dir_from_env(),
            "deck_url": DEFAULT_DECK_URL,
            "decks_dir": str(paths.decks_dir()), "records_dir": str(paths.runs_dir()),
            "beamfiles_dir": str(paths.beamfiles_dir()), "slice_max": SLICE_MAX,
            "writes": "run_beamline, make_recoveries (and make_beamfile with publish=True) write through "
                      "prodtools; run_as='mu2epro' needs confirm=True"}
