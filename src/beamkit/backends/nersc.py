"""The NERSC backend: a run is a directory on CFS plus one Slurm job per
slice of procs_per_node indices, driven through the IRI Facility API.
Nothing here touches SAM, dCache, prodtools or a ledger."""
import json
import math
import os
from dataclasses import dataclass, field, replace
from pathlib import Path

from beamkit import (BeamkitError, beamfile, identity, iri, naming, nersc_cnf, nersc_config,
                     nersc_templates, paths, records)

WALLTIME_DEFAULT = 172800
CUSTOM_ATTRIBUTES = {"constraint": "cpu", "licenses": "cvmfs", "module": "cvmfs"}


@dataclass
class Block:
    """What only the NERSC backend reads and writes on a run record."""
    run_dir: str
    cnf: str
    walltime_s: int
    config: dict
    jobs: list = field(default_factory=list)


def slices(njobs, procs_per_node) -> list[tuple[int, int]]:
    """(offset, count) per Slurm job; index = offset + SLURM_PROCID."""
    return [(k * procs_per_node, min(procs_per_node, njobs - k * procs_per_node))
            for k in range(math.ceil(njobs / procs_per_node))]


def validate_walltime(walltime_s) -> int:
    if isinstance(walltime_s, bool) or not isinstance(walltime_s, int) or walltime_s < 1:
        raise BeamkitError(f"walltime_s must be a positive integer number of seconds, got {walltime_s!r}")
    return walltime_s


def duration_s(events_per_job, walltime_s) -> int:
    """About 1.4 s/event on the e470313 deck, doubled, plus 15 min for cold
    cvmfs and the apptainer start; capped by the caller's walltime."""
    return min(validate_walltime(walltime_s), int(events_per_job) * 2 + 900)


def job_spec(cfg, *, run_id, run_dir, offset, count, duration) -> dict:
    """One node per slice. A slice smaller than a node that fits the shared
    queue's cap runs there non-exclusive and is charged per core; a full
    slice, or a partial one over the cap, takes a whole node in cfg.qos
    (the charge is the same either way for a full node)."""
    shared = count < cfg.procs_per_node and count <= cfg.shared_max_procs
    return {
        "name": f"beamkit.{run_id}.{offset}",
        "executable": "/bin/bash",
        "arguments": [f"{run_dir}/job.sh"],
        "directory": run_dir,
        "stdout_path": f"{run_dir}/slurm/{offset}.out",
        "stderr_path": f"{run_dir}/slurm/{offset}.err",
        "inherit_environment": False,
        "environment": {"BK_OFFSET": str(offset)},
        "resources": {"node_count": 1, "process_count": count, "processes_per_node": count,
                      "cpu_cores_per_process": 1, "exclusive_node_use": not shared},
        "attributes": {"duration": int(duration), "queue_name": cfg.shared_qos if shared else cfg.qos,
                       "account": cfg.account,
                       "custom_attributes": dict(CUSTOM_ATTRIBUTES)},
    }


def make_client(cfg):
    """The transport named in nersc.toml; both expose the same methods."""
    if cfg.transport == "sfapi":
        from beamkit import sfapi
        return sfapi.SfapiClient(cfg)
    return iri.IriClient(cfg)


_CACHE = {}          # (nersc.toml path, mtime) -> [cfg, client or None]


def _entry() -> list:
    """nersc.toml as loaded, kept per (path, mtime): repeated tool calls in
    one server process re-read no config. A different BEAMKIT_HOME, or an
    edited file, is a miss."""
    home = paths.home()
    path = nersc_config.config_path(home)
    key = (str(path), path.stat().st_mtime_ns) if path.is_file() else None
    if key not in _CACHE:
        _CACHE[key] = [nersc_config.load(home), None]      # a missing file is refused here
    return _CACHE[key]


def config():
    """The config alone, for the refusals that come before any client exists."""
    return _entry()[0]


def available() -> tuple:
    """nersc.toml present and valid; the detail names account, base_dir, owner."""
    cfg_path = nersc_config.config_path(paths.home())
    try:
        cfg = config()
    except BeamkitError as e:
        return False, f"{cfg_path}: {e}"
    return True, f"account {cfg.account}, base_dir {cfg.base_dir}, owner {cfg.owner} ({cfg_path})"


def _cfg_client():
    """(config, client): one client per config, so the session and its
    token outlive the tool call that created them."""
    entry = _entry()
    if entry[1] is None:
        entry[1] = make_client(entry[0])
    return entry[0], entry[1]


SUBMITTABLE = ("created", "partially_submitted")
TERMINAL = ("completed", "failed", "canceled")
MISSING_CAP = 50


def run_dir(cfg, run_id) -> str:
    return f"{cfg.base_dir}/runs/{run_id}"


def _remote_layout(cfg, client, rd, uploads):
    """The API's mkdir is not -p: build every component from cfg.base_dir
    down (base_dir, base_dir/runs, the run dir, then its subdirs) in
    order, since a fresh base_dir has neither yet."""
    for d in (cfg.base_dir, f"{cfg.base_dir}/runs", rd, f"{rd}/out", f"{rd}/slurm", f"{rd}/beamfiles"):
        if not client.exists(d):
            client.mkdir(d)
    for local, remote in uploads:
        client.upload(local, remote)


def _submit_missing(rec, cfg, client, runs_dir):
    """Submit every slice whose offset the record does not carry, in order.
    Slicing is by the record's own slice_size (fixed at create time), not
    cfg.procs_per_node: an operator lowering procs_per_node between a
    partial submit and this call must not re-slice the run underneath the
    jobs already on Slurm, which would submit some indices twice. A failure
    part way is saved as partially_submitted and raised; the next
    submit_run continues from there."""
    have = {j["offset"] for j in rec.block.jobs}
    all_slices = slices(rec.njobs, rec.slice_size)
    todo = [(o, c) for o, c in all_slices if o not in have]
    total = len(all_slices)
    rd = rec.block.run_dir
    dur = duration_s(rec.events_per_job, rec.block.walltime_s)
    for k, (offset, count) in enumerate(todo, start=len(have)):
        spec = job_spec(cfg, run_id=rec.run_id, run_dir=rd, offset=offset, count=count, duration=dur)
        try:
            jid = client.submit(spec)
        except Exception as e:
            rec.state, rec.error = "partially_submitted", (
                f"job {k} of {total} (offset {offset}) failed to submit: {e}; check squeue for "
                f"beamkit.{rec.run_id}.{offset} before submit_run")
            records.save(rec, runs_dir)
            raise BeamkitError(f"run {rec.run_id}: job {k} of {total} (offset {offset}) failed to submit: {e}; "
                               f"check squeue for beamkit.{rec.run_id}.{offset} before submit_run; "
                               f"{len(rec.block.jobs)} job(s) are running; submit_run({rec.run_id!r}, "
                               f"'self') submits the rest") from e
        rec.block.jobs.append({"slurm_id": jid, "offset": offset, "count": count,
                                  "submitted": records.now_utc()})
        records.save(rec, runs_dir)
    rec.state, rec.error = "submitted", None
    records.save(rec, runs_dir)


# --- creation hooks (runs.create)

def resolve_identity(req):
    return identity.resolve(req.run_as, site="nersc", owner=config().owner)


def validate(req, ident):
    """NERSC's own arguments: no slice_size (procs_per_node slices the run),
    outputs stay on CFS, walltime in range with its default filled."""
    if req.slice_size is not None:
        raise BeamkitError("slice_size applies to site='fermilab' only; a NERSC run is sliced by "
                           "procs_per_node from nersc.toml")
    cfg = config()
    if req.outloc != "scratch":
        raise BeamkitError(f"outloc={req.outloc!r}: outputs of a NERSC run stay on CFS under {cfg.base_dir}; "
                           f"pass outloc='scratch' (the default) or omit it")
    walltime_s = WALLTIME_DEFAULT if req.walltime_s is None else validate_walltime(req.walltime_s)
    return replace(req, walltime_s=walltime_s, slice_size=cfg.procs_per_node)


def taken(ident, tag):
    """allocate_dsconf's probe: a cnf name is taken when its run directory
    exists on CFS, unless a retryable local record explains that directory
    (a failed layout of our own): probing that as taken would burn a new
    dsconf and orphan the enqueue_failed record."""
    cfg, client = _cfg_client()
    runs_dir = paths.runs_dir()

    def probe(cnf_name):
        run_id = naming.run_id(tag, naming.dsconf_of(cnf_name))
        rec = records.try_load(run_id, runs_dir)
        if rec is not None and retryable(rec):
            return False
        return client.exists(run_dir(cfg, run_id))
    return probe


def retryable(rec) -> bool:
    return rec.site == "nersc" and rec.state == "enqueue_failed" and not rec.block.jobs


def new_block(req, ident, pin, run_id, dsconf):
    cfg = config()
    return Block(run_dir=run_dir(cfg, run_id), cnf=naming.cnf_name(ident.owner, req.tag, dsconf),
                 walltime_s=req.walltime_s, config=cfg.as_record())


def enqueue(rec, req, ident, pin, rdir):
    """The cnf, job.sh and inner.sh built here and laid out on CFS."""
    cfg, client = _cfg_client()
    rd, cnf_name = rec.block.run_dir, rec.block.cnf
    local_cnf = rdir / cnf_name
    local_cnf.unlink(missing_ok=True)
    nersc_cnf.build_cnf(pin.dir, nersc_cnf.jobpars(owner=ident.owner, tag=rec.tag, dsconf=rec.dsconf,
                                                   main_input=req.main_input, events_per_job=req.events_per_job,
                                                   njobs=req.njobs, params=req.params), local_cnf)
    (rdir / "job.sh").write_text(nersc_templates.render_job(cfg, rd))
    (rdir / "inner.sh").write_text(nersc_templates.render_inner(
        cfg, run_id=rec.run_id, run_dir=rd, owner=ident.owner, tag=rec.tag, dsconf=rec.dsconf,
        events_per_job=req.events_per_job, main_input=req.main_input, params=req.params))
    _remote_layout(cfg, client, rd, [(local_cnf, f"{rd}/{cnf_name}"), (rdir / "job.sh", f"{rd}/job.sh"),
                                     (rdir / "inner.sh", f"{rd}/inner.sh")])


def after_failure(rec, ident) -> str:
    return "; fix the cause and call again, the run dir is reused"


def submit(rec, ident, confirm):
    cfg, client = _cfg_client()
    _submit_missing(rec, cfg, client, paths.runs_dir())


def submit_run(rec, run_as) -> dict:
    cfg, client = _cfg_client()
    identity.resolve(run_as, site="nersc", owner=cfg.owner)
    runs_dir = paths.runs_dir()
    run_id = rec.run_id
    if rec.state not in SUBMITTABLE:
        raise BeamkitError(f"run {run_id} is in state {rec.state!r}; submit_run applies to {SUBMITTABLE}")
    rd = rec.block.run_dir
    if not client.exists(f"{rd}/inner.sh"):
        raise BeamkitError(f"run {run_id}: remote layout at {rd} is missing inner.sh (job.sh cannot run); "
                           f"run run_beamline again")
    _submit_missing(rec, cfg, client, runs_dir)
    return rec.to_dict()


def make_recoveries(rec, run_as, confirm) -> dict:
    raise BeamkitError(f"run {rec.run_id}: no recovery on nersc; submit a new run (beamline_status reports "
                       f"the missing indices)")


def _nts_entries(client, rec) -> tuple:
    """One ls of out/: the run's nts files by job index, and how many logs
    sit beside them. A run that failed before its remote layout was ever
    created (e.g. the cnf itself exceeded the upload cap) has no out/ dir
    yet -- that is zero files, not an error, same as client.exists()."""
    rd = rec.block.run_dir
    try:
        entries = client.ls(f"{rd}/out")
    except iri.IriError as e:
        if "No such file" not in (e.detail or str(e)):
            raise
        entries = []
    files, logs = [], 0
    for e in entries:
        name = e["name"].rsplit("/", 1)[-1]
        index = naming.nts_index(name, rec.owner, rec.tag, rec.dsconf)
        if index is not None:
            files.append({"name": name, "index": index, "size": int(e["size"]),
                          "path": f"{rd}/out/{name}"})
        elif name.startswith(f"log.{rec.owner}.{rec.tag}.{rec.dsconf}."):
            logs += 1
    files.sort(key=lambda f: f["index"])
    return files, logs


def out_counts(client, rec) -> dict:
    files, logs = _nts_entries(client, rec)
    missing = sorted(set(range(rec.njobs)) - {f["index"] for f in files})
    return {"expected": rec.njobs, "nts": len(files), "logs": logs, "missing": missing[:MISSING_CAP],
            "nts_files": [f["name"] for f in files]}


def _job_status(client, job):
    st = client.status(job["slurm_id"])
    md = st.get("meta_data") or {}
    return {"slurm_id": job["slurm_id"], "offset": job["offset"], "count": job["count"],
            "state": st["state"], "exit_code": st.get("exit_code"),
            "elapsed": md.get("elapsed"), "node": md.get("nodelist")}


def status(rec) -> dict:
    """Slurm state per job and one ls of out/. Sets the run state:
    complete / short once every job is terminal, submitted otherwise.
    created and partially_submitted are left as they are."""
    cfg, client = _cfg_client()
    jobs = [_job_status(client, j) for j in rec.block.jobs]
    outs = out_counts(client, rec)
    if rec.state in ("submitted", "short", "complete") and jobs:
        if all(j["state"] in TERMINAL for j in jobs):
            rec.state = "complete" if outs["nts"] == outs["expected"] else "short"
        else:
            rec.state = "submitted"
    _refresh_beamfiles(client, rec)
    records.save(rec, paths.runs_dir())
    return {"jobs": jobs, "outputs": outs, "beamfiles": list(rec.beamfiles)}


def outputs(rec) -> dict:
    """A run with no out/ dir on CFS yet has no outputs, not an error."""
    cfg, client = _cfg_client()
    rd = rec.block.run_dir
    files, _ = _nts_entries(client, rec)
    return {"run_id": rec.run_id, "run_dir": rd, "n_files": len(files),
            "total_size": sum(f["size"] for f in files), "files": files, "beamfiles": list(rec.beamfiles)}


FETCH_KINDS = ("nts", "beamfiles")


def _fetch_list(client, rec, kind) -> list[dict]:
    if kind == "nts":
        return outputs(rec)["files"]
    files = []
    for bf in rec.beamfiles:
        if bf.get("state") != "complete":
            continue
        entry = client.ls(bf["path"])
        if len(entry) != 1:
            raise BeamkitError(f"run {rec.run_id}: beam file {bf['path']} is not one file on CFS ({len(entry)} entries)")
        files.append({"name": bf["path"].rsplit("/", 1)[-1], "label": bf["label"],
                      "size": int(entry[0]["size"]), "path": bf["path"]})
    return files


def fetch_outputs(rec, dest, kind) -> dict:
    """Copy the run's nts files (or its complete beam files) from CFS into
    dest through the API's download endpoint, one file per call. The API
    carries at most iri.DOWNLOAD_MAX bytes per file, so a run with one
    larger file is refused whole before any transfer (Globus or scp is
    the path for those). A file already in dest with the CFS size is left
    alone, so a rerun only fetches what is missing. Each file is written
    to a temporary name and renamed once its size matches CFS."""
    if kind not in FETCH_KINDS:
        raise BeamkitError(f"kind must be one of {FETCH_KINDS}, got {kind!r}")
    cfg, client = _cfg_client()
    files = _fetch_list(client, rec, kind)
    big = [f for f in files if f["size"] > iri.DOWNLOAD_MAX]
    if big:
        names = ", ".join(f"{f['name']} ({f['size']} bytes)" for f in big[:5])
        raise BeamkitError(f"run {rec.run_id}: {len(big)} of {len(files)} {kind} files exceed the API's "
                           f"{iri.DOWNLOAD_MAX}-byte download cap ({names}{', ...' if len(big) > 5 else ''}); "
                           f"nothing fetched: move them with Globus or scp from {rec.block.run_dir}")
    dest = Path(dest).expanduser().resolve()
    dest.mkdir(parents=True, exist_ok=True)
    fetched = skipped = 0
    for f in files:
        local = dest / f["name"]
        if local.is_file() and local.stat().st_size == f["size"]:
            f.update(local=str(local), status="present")
            skipped += 1
            continue
        data = client.download_bytes(f["path"])
        if len(data) != f["size"]:
            raise BeamkitError(f"run {rec.run_id}: {f['name']} is {f['size']} bytes on CFS "
                               f"but {len(data)} bytes came back; nothing written")
        tmp = dest / f".{f['name']}.part"
        tmp.write_bytes(data)
        os.replace(tmp, local)
        f.update(local=str(local), status="fetched")
        fetched += 1
    return {"run_id": rec.run_id, "kind": kind, "dest": str(dest), "n_files": len(files),
            "n_fetched": fetched, "n_present": skipped, "total_size": sum(f["size"] for f in files),
            "files": files}


BEAMFILE_WALLTIME_CAP = 4 * 3600


def beamfile_duration(n_nts) -> int:
    return min(BEAMFILE_WALLTIME_CAP, 600 + 2 * int(n_nts))


def make_beamfile(rec, *, flavor, run_as, plane, cuts, label, publish, location, confirm) -> dict:
    if location is not None:
        raise BeamkitError("location applies to site='fermilab' publishing only")
    if publish:
        raise BeamkitError("publish=True on a NERSC run: publishing is part of harvest, which runs at Fermilab; "
                           "build with publish=False")
    cfg, client = _cfg_client()
    identity.resolve(run_as, site="nersc", owner=cfg.owner)
    label = flavor if label is None else label
    resolved = beamfile.resolve_cuts(flavor, cuts)
    beamfile.validate_label(label)
    beamfile.validate_plane(plane)
    run_id, runs_dir = rec.run_id, paths.runs_dir()
    if any(b["label"] == label for b in rec.beamfiles):
        raise BeamkitError(f"run {run_id}: label {label!r} is already spent by a submitted beam-file job; "
                           f"a beam file is never overwritten (pick another label)")
    counts = out_counts(client, rec)
    if counts["nts"] == 0:
        raise BeamkitError(f"run {run_id}: 0 of {counts['expected']} nts files on CFS "
                           f"({counts['logs']} logs); nothing to build a beam file from")
    rd = rec.block.run_dir
    name = naming.beamfile_name(rec.owner, rec.tag, label, rec.dsconf)
    stem = f"{rd}/beamfiles/{name[:-len('.txt')]}"
    local = records.run_dir(runs_dir, run_id) / "beamfiles"
    local.mkdir(exist_ok=True)
    job_py, job_sh = local / f"beamfile_job.{label}.py", local / f"beamfile.{label}.sh"
    job_py.write_text(nersc_templates.render_beamfile_job(
        run_dir=rd, owner=rec.owner, tag=rec.tag, dsconf=rec.dsconf, events_per_job=rec.events_per_job,
        njobs=rec.njobs, plane=plane, label=label, flavor=flavor, cuts=resolved))
    job_sh.write_text(nersc_templates.render_beamfile_sh(cfg, job_py=f"{rd}/beamfiles/{job_py.name}"))
    client.upload(job_py, f"{rd}/beamfiles/{job_py.name}")
    client.upload(job_sh, f"{rd}/beamfiles/{job_sh.name}")
    spec = job_spec(cfg, run_id=rec.run_id, run_dir=rd, offset=0, count=1,
                    duration=beamfile_duration(counts["nts"]))
    spec["name"] = f"beamkit.{rec.run_id}.beamfile.{label}"
    spec["arguments"] = [f"{rd}/beamfiles/{job_sh.name}"]
    spec["stdout_path"], spec["stderr_path"] = f"{rd}/slurm/beamfile.{label}.out", f"{rd}/slurm/beamfile.{label}.err"
    spec["environment"] = {}
    jid = client.submit(spec)
    entry = {"run_id": run_id, "flavor": flavor, "label": label, "cuts": resolved, "plane": plane,
             "slurm_id": jid, "state": "submitted", "exit_code": None, "n_files_at_submit": counts["nts"],
             "path": stem + ".txt", "sidecar": stem + ".json", "sha256": None, "size": None, "rows": None,
             "rows_in": None, "dropped": None, "pot": None, "n_files": None, "missing_indices": None,
             "sam_name": None, "location": None, "created": records.now_utc()}
    rec.beamfiles.append(entry)
    records.save(rec, runs_dir)
    return entry


SIDECAR_KEYS = ("sha256", "size", "rows", "rows_in", "dropped", "pot", "n_files", "missing_indices")


def _refresh_beamfiles(client, rec) -> None:
    """Beam-file jobs still 'submitted': read Slurm; on a terminal state
    read the sidecar and copy its numbers in, or record the failure."""
    for bf in rec.beamfiles:
        if bf.get("state") != "submitted":
            continue
        st = client.status(bf["slurm_id"])
        if st["state"] not in TERMINAL:
            continue
        bf["exit_code"] = st.get("exit_code")
        try:
            side = json.loads(client.download(bf["sidecar"]))
        except (iri.IriError, ValueError):
            bf["state"] = "failed"
            continue
        for k in SIDECAR_KEYS:
            bf[k] = side.get(k)
        bf["state"] = "complete" if st["state"] == "completed" else "failed"
