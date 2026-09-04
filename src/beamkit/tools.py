"""The beamkit tools. Orchestration only: naming, decks, compose, records
do the work; bridge talks to prodtools."""
import json
import os
import sys

from beamkit import __version__, beamfile, bridge, compose, decks, naming, paths, records
from beamkit.decks import DEFAULT_DECK_URL

SLICE_MAX = 10000


class ToolError(RuntimeError):
    pass


def _require(run_as, confirm):
    """The same gate prodtools enforces, applied before any side effect so a
    refused mu2epro call burns no dsconf and writes no record."""
    if run_as not in naming.RUN_AS:
        raise ToolError(f"run_as must be one of {naming.RUN_AS}, got {run_as!r}")
    if run_as == "mu2epro" and os.environ.get("BEAMKIT_PRODTOOLS_DIR"):
        raise ToolError("BEAMKIT_PRODTOOLS_DIR is set, which ships that checkout to the workers; "
                        "prodtools refuses a dev prodtools_dir for run_as='mu2epro' outright. A "
                        "production run uses a published cvmfs prodtools release only: unset "
                        "BEAMKIT_PRODTOOLS_DIR")
    if run_as == "mu2epro" and not confirm:
        raise ToolError("run_as='mu2epro' registers artifacts in production SAM and submits "
                        "production grid jobs; pass confirm=True")


def _outloc(outloc, run_as):
    """beamkit holds the output location and the account at the same moment,
    so it can refuse the pair prodtools only discovers on the worker."""
    if outloc not in compose.OUTLOCS:
        raise ToolError(f"outloc must be one of {compose.OUTLOCS}, got {outloc!r}")
    if outloc == "disk" and run_as != "mu2epro":
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


def _pin(deck_ref, deck_dir, deck_url, run_as):
    if (deck_ref is None) == (deck_dir is None):
        raise ToolError("pass exactly one of deck_ref (a commit sha or tag) or deck_dir (a local checkout)")
    if deck_dir is not None:
        if run_as != "self":
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


def _dsconf_after_failed_push(owner, tag, dsconf) -> str:
    """Whether the cnf actually reached SAM decides whether the dsconf is
    burned; a push that failed before the SAM write leaves it free, and the
    retry reuses it. Never claim one without asking."""
    name = naming.cnf_name(owner, tag, dsconf)
    try:
        landed = bridge.cnf_exists(name)
    except Exception as e:
        return (f"; whether {name} reached SAM could not be determined "
                f"({type(e).__name__}: {e}), so check SAM before retrying")
    if landed:
        return (f"; {name} is in SAM, so the dsconf {dsconf!r} is burned and the next call "
                f"allocates the next suffix")
    return (f"; {name} is not in SAM, so the dsconf {dsconf!r} is free and this call can be "
            f"retried once the cause is fixed")


def run_beamline(tag, deck_ref=None, run_as="self", params=None, events_per_job=1000, njobs=1,
                 main_input="Mu2E.in", outloc="scratch", dsconf=None, slice_size=None,
                 submit=True, confirm=False, deck_dir=None, deck_url=DEFAULT_DECK_URL) -> dict:
    _require(run_as, confirm)
    _outloc(outloc, run_as)
    try:
        naming.validate_tag(tag)
        params = compose.validate_params({} if params is None else params)
        compose._positive_int("njobs", njobs)
    except (naming.NamingError, compose.ComposeError) as e:
        raise ToolError(str(e)) from e
    slice_size = _slice_size(slice_size, njobs)
    owner = naming.owner_for(run_as)
    pin = _pin(deck_ref, deck_dir, deck_url, run_as)
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
                            created=records.now_utc(), prodtools=bridge.prodtools_info(),
                            beamkit_version=__version__)
    records.save(rec, runs_dir)
    try:
        pushed = bridge.push_cnf(entry_path, tag, dsconf, slice_size, run_as, confirm)
    except Exception as e:
        rec.state, rec.error = "enqueue_failed", f"{type(e).__name__}: {e}"
        records.save(rec, runs_dir)
        raise ToolError(f"run {run_id}: push_cnf failed ({e})"
                        f"{_dsconf_after_failed_push(owner, tag, dsconf)}") from e
    rec.campaign_id, rec.tarball = pushed["campaign_id"], pushed["tarball"]
    # prodtools echoes the entry's outloc key ("nts.*.root"), a glob, not a name;
    # the dataset this run actually writes is the one _dataset composes.
    rec.datasets, rec.prodtools_datasets = [_dataset(rec)], list(pushed["datasets"])
    records.save(rec, runs_dir)
    if submit:
        _tick_into(rec, run_as, confirm, runs_dir,
                   failure=f"run {run_id}: campaign {rec.campaign_id} was created but the first tick failed; "
                           f"call make_recoveries({run_id!r}, {run_as!r}) to submit it")
        rec.state = "submitted"
        records.save(rec, runs_dir)
    return rec.to_dict()


def _tick_into(rec, run_as, confirm, runs_dir, failure):
    try:
        t = bridge.tick(run_as, rec.campaign_id, confirm)
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


def make_recoveries(run_id, run_as, confirm=False) -> dict:
    """One prodtools tick for this run's campaign. The recovery pass is
    ledger-wide (prodtools scopes only the top-up); the result says so."""
    _require(run_as, confirm)
    rec = _load(run_id)
    if rec.campaign_id is None:
        raise ToolError(f"run {run_id} has no campaign (state {rec.state!r}); nothing to recover")
    t = _tick_into(rec, run_as, confirm, paths.runs_dir(),
                   failure=f"run {run_id}: tick of campaign {rec.campaign_id} failed")
    rec.state = "submitted"
    records.save(rec, paths.runs_dir())
    return {"run_id": run_id, "campaign_id": rec.campaign_id, "rc": t["rc"],
            "needs_attention": t["needs_attention"], "output": t["output"], "ledger_wide": True,
            "note": "the verify/recovery pass covered every active campaign in this ledger, not only this run"}


def beamline_status(run_id) -> dict:
    rec = _load(run_id)
    campaign = None
    if rec.campaign_id is not None:
        campaign = bridge.campaign_status(rec.campaign_id, mine=(rec.run_as == "self"))
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


LOCATIONS = ("scratch", "disk", "tape")


def make_beamfile(run_id, flavor, run_as, plane="Z3712", cuts=None, publish=False,
                  location=None, confirm=False) -> dict:
    """A BLTrackFile from whatever nts files the run has in SAM. No
    completeness check: pot counts the files that exist."""
    if publish:
        _require(run_as, confirm)
    elif run_as not in naming.RUN_AS:
        raise ToolError(f"run_as must be one of {naming.RUN_AS}, got {run_as!r}")
    try:
        resolved = beamfile.resolve_cuts(flavor, cuts)
    except beamfile.BeamfileError as e:
        raise ToolError(str(e)) from e
    if publish:
        location = location or ("tape" if run_as == "mu2epro" else "scratch")
        if location not in LOCATIONS:
            raise ToolError(f"location must be one of {LOCATIONS}, got {location!r}")
        # knowable now; discovering it in the publish unwind costs hours of
        # dCache reads and throws the built beam file away
        try:
            available = bridge.push_file_available()
        except bridge.BridgeError as e:
            raise ToolError(f"make_beamfile(publish=True): {e}") from e
        if not available:
            raise ToolError("this prodtools has no push_file tool, so publish=True cannot succeed; "
                            "make_beamfile works with publish=False only until prodtools-write "
                            "gains push_file")
    rec = _load(run_id)
    # the file is NAMED from the record's identity and PUSHED as run_as; a
    # mismatch publishes one owner's name under the other account
    if publish and run_as != rec.run_as:
        raise ToolError(f"run {run_id} was created with run_as={rec.run_as!r}, so its beam file is "
                        f"named etc.{rec.owner}.…; publishing it as run_as={run_as!r} would push "
                        f"that name under the other identity. Pass run_as={rec.run_as!r}")
    out_dir = paths.beamfiles_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_txt = out_dir / f"{run_id}.{flavor}.txt"
    out_json = out_dir / f"{run_id}.{flavor}.json"
    if out_txt.exists() or out_json.exists():
        raise ToolError(f"{out_txt} exists; a beam file is never overwritten (pick another flavor label)")
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
    side = {"run_id": run_id, "flavor": flavor, "cuts": resolved, "plane": plane, "path": str(out_txt),
            "sha256": stats["sha256"], "size": stats["size"], "rows": stats["rows_out"],
            "rows_in": stats["rows_in"], "dropped": stats["dropped"],
            "pot": len(files) * rec.events_per_job, "n_files": len(files),
            "missing_indices": beamfile.missing_indices(present, rec.njobs),
            "source_files": [f["name"] for f in files], "sam_name": None, "location": None,
            "created": records.now_utc()}
    if publish:
        sam_name = f"etc.{rec.owner}.{rec.tag}Beam-{flavor}.{rec.dsconf}.txt"
        staged = out_dir / sam_name
        linked = False
        try:
            os.link(out_txt, staged)
            linked = True
            bridge.push_file(staged, location, side["source_files"], run_as, confirm)
        except Exception as e:
            # only what THIS call created: an os.link that failed because the
            # staged name was already there left someone else's file behind it
            if linked:
                try:
                    staged.unlink()
                except OSError:
                    pass
            try:
                out_txt.unlink()
            except OSError:
                pass
            raise ToolError(f"beam file discarded: publish failed, so this call left nothing new "
                            f"behind; it can be retried: {e}") from e
        side["sam_name"], side["location"] = sam_name, location
    out_json.write_text(json.dumps(side, indent=2) + "\n")
    rec.beamfiles.append(side)
    records.save(rec, paths.runs_dir())
    return side


def get_server_info() -> dict:
    return {"name": "beamkit", "version": __version__, "python": sys.executable,
            "prodtools": bridge.prodtools_info(), "deck_url": DEFAULT_DECK_URL,
            "decks_dir": str(paths.decks_dir()), "records_dir": str(paths.runs_dir()),
            "beamfiles_dir": str(paths.beamfiles_dir()), "slice_max": SLICE_MAX,
            "writes": "run_beamline, make_recoveries (and make_beamfile with publish=True) write through "
                      "prodtools; run_as='mu2epro' needs confirm=True"}
