"""The Fermilab backend: a run is a prodtools campaign, driven through
bridge.py. This module carries the Fermilab half of every tool (the
interface in docs/specs/2026-09-17-backend-seam-design.md §6)."""
import json
from dataclasses import dataclass, field, replace

from beamkit import (BeamkitError, beamfile, bridge, compose, identity, naming, paths, publishing,
                     records)

SLICE_MAX = 10000


@dataclass
class Block:
    """What only the Fermilab backend reads and writes on a run record."""
    prodtools: dict                      # root, commit, dev_dir
    campaign_id: int | None = None
    tarball: str | None = None
    ticks: list = field(default_factory=list)


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
    rec.block.campaign_id, rec.block.tarball, rec.state = camp["id"], name, "created"
    return (f"; {name} is in SAM and campaign {camp['id']} exists for it, so the record now carries "
            f"that campaign in state 'created' and nothing was submitted: "
            f"make_recoveries({rec.run_id!r}, {ident.run_as!r}) submits it")


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
    rec.block.ticks.append({"when": records.now_utc(), "rc": t["rc"], "needs_attention": t["needs_attention"],
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


def available() -> tuple:
    return bridge.availability()


def status(rec):
    """prodtools' campaign_status for the run's campaign; None before one
    exists (enqueue_failed before the cnf reached SAM)."""
    if rec.block.campaign_id is None:
        return None
    return bridge.campaign_status(rec.block.campaign_id, mine=identity.for_record(rec).mine)


def outputs(rec) -> dict:
    """Files of the run's nts dataset in dCache, via SAM."""
    dataset = _dataset(rec)
    files = bridge.dataset_files(dataset, rec.outloc)
    return {"run_id": rec.run_id, "dataset": dataset, "location": rec.outloc,
            "n_files": len(files), "total_size": sum(f["size"] for f in files), "files": files}


def fetch_outputs(rec, dest, kind) -> dict:
    raise BeamkitError(f"run {rec.run_id} is a 'fermilab' run; its outputs are in dCache, see beamline_outputs")


def submit_run(rec, run_as) -> dict:
    raise BeamkitError(f"run {rec.run_id} is a 'fermilab' run; the Fermilab path submits through make_recoveries")


def make_recoveries(rec, run_as, confirm) -> dict:
    """One prodtools tick for this run. While the campaign is active the tick
    is scoped to it. Once prodtools has marked it complete (every slice
    submitted, rows still verifying) a scoped tick is refused as not
    active, so the bare tick is used: its verify/recovery pass reaches this
    run's rows and its top-up feeds every other active campaign in the
    ledger. The result names which form ran."""
    ident = identity.resolve(run_as, confirm)
    run_id = rec.run_id
    if rec.block.campaign_id is None:
        raise BeamkitError(f"run {run_id} has no campaign (state {rec.state!r}); nothing to recover")
    if ident.run_as != rec.run_as:
        raise BeamkitError(f"run {run_id} lives in the run_as={rec.run_as!r} ledger; tick it as that identity")
    camp = _campaign(rec.block.campaign_id, mine=ident.mine)
    scoped = camp["state"] == "active"
    t = _tick_into(rec, run_as, confirm, paths.runs_dir(),
                   failure=f"run {run_id}: tick of campaign {rec.block.campaign_id} failed",
                   campaign_id=rec.block.campaign_id if scoped else None)
    return {"run_id": run_id, "campaign_id": rec.block.campaign_id, "campaign_state": camp["state"],
            "rc": t["rc"], "needs_attention": t["needs_attention"], "output": t["output"],
            "tick_scope": "campaign" if scoped else "ledger",
            "note": ("the verify/recovery pass covered every active campaign in this ledger, not only this run"
                     if scoped else
                     f"campaign {rec.block.campaign_id} is {camp['state']!r}, so this was the bare tick: the "
                     f"verify/recovery pass reached its rows and the top-up fed every active campaign in "
                     f"this ledger")}


def make_beamfile(rec, *, flavor, run_as, plane, cuts, label, publish, location, confirm) -> dict:
    """A BLTrackFile from whatever nts files the run has in SAM, built here."""
    ident = identity.resolve(run_as, confirm, writes=publish)
    run_id = rec.run_id
    label = flavor if label is None else label
    resolved = beamfile.resolve_cuts(flavor, cuts)
    beamfile.validate_label(label)
    beamfile.validate_plane(plane)
    if publish:
        location = location or ident.default_publish_location
        publishing.check_ready(location)
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
    records.atomic_write_text(out_json, json.dumps(side, indent=2) + "\n")
    rec.beamfiles.append(side)
    records.save(rec, paths.runs_dir())
    return side


# --- creation hooks (runs.create)

def resolve_identity(req):
    return identity.resolve(req.run_as, req.confirm)


def validate(req, ident):
    """Fermilab's own arguments: no walltime (prodtools sizes the jobs), the
    slice size in range, and the outloc/identity pair. dev_dir_for_shipping
    is called here so a production call with a dev checkout is refused
    before the deck fetch."""
    if req.walltime_s is not None:
        raise BeamkitError("walltime_s applies to site='nersc' only; the Fermilab path takes its resources "
                           "from prodtools")
    ident.dev_dir_for_shipping()
    _outloc(req.outloc, ident)
    return replace(req, slice_size=_slice_size(req.slice_size, req.njobs))


def taken(ident, tag):
    return bridge.cnf_exists


def retryable(rec) -> bool:
    """A run dir left by a push that never reached prodtools: state
    'enqueue_failed' with no campaign. Nothing was created anywhere else, so
    the retry overwrites it in place."""
    return rec.state == "enqueue_failed" and rec.block.campaign_id is None


def new_block(req, ident, pin, run_id, dsconf):
    return Block(prodtools=dict(bridge.prodtools_info(), dev_dir=ident.dev_dir_for_shipping()))


def enqueue(rec, req, ident, pin, rdir):
    """entry.json, then push_cnf: the cnf into SAM and its campaign into the
    ledger. rec.njobs becomes the campaign's count, which missing_indices
    is measured against."""
    entry = compose.entry(tag=rec.tag, dsconf=rec.dsconf, deck_dir=pin.dir, main_input=req.main_input,
                          events_per_job=req.events_per_job, njobs=req.njobs, outloc=req.outloc, params=req.params)
    entry_path = compose.write_entry_json(entry, rdir / "entry.json")
    pushed = bridge.push_cnf(entry_path, rec.tag, rec.dsconf, rec.slice_size, req.run_as, req.confirm,
                             prodtools_dir=rec.block.prodtools["dev_dir"])
    rec.block.campaign_id, rec.block.tarball = pushed["campaign_id"], pushed["tarball"]
    rec.njobs = pushed["njobs"]


def after_failure(rec, ident) -> str:
    return _after_failed_push(rec, ident)


def submit(rec, ident, confirm):
    _tick_into(rec, ident.run_as, confirm, paths.runs_dir(),
               failure=f"run {rec.run_id}: campaign {rec.block.campaign_id} was created but the first tick "
                       f"failed; call make_recoveries({rec.run_id!r}, {ident.run_as!r}) to submit it",
               campaign_id=rec.block.campaign_id)
