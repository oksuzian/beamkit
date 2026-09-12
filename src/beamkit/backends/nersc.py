"""The NERSC backend: a run is a directory on CFS plus one Slurm job per
slice of procs_per_node indices, driven through the IRI Facility API.
Nothing here touches SAM, dCache, prodtools or a ledger."""
import json
import math
from pathlib import Path

from beamkit import BeamkitError

WALLTIME_DEFAULT = 172800
CUSTOM_ATTRIBUTES = {"constraint": "cpu", "licenses": "cvmfs", "module": "cvmfs"}


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
                      "cpu_cores_per_process": 1, "exclusive_node_use": True},
        "attributes": {"duration": int(duration), "queue_name": cfg.qos, "account": cfg.account,
                       "custom_attributes": dict(CUSTOM_ATTRIBUTES)},
    }


from beamkit import (__version__, compose, decks, identity, iri, naming, nersc_cnf, nersc_config,
                     nersc_templates, paths, records)

make_client = iri.IriClient
SUBMITTABLE = ("created", "partially_submitted")
TERMINAL = ("completed", "failed", "canceled")
MISSING_CAP = 50


def run_dir(cfg, run_id) -> str:
    return f"{cfg.base_dir}/runs/{run_id}"


def _taken(cfg, client, tag):
    """allocate_dsconf's probe: a cnf name is taken when its run directory
    exists on CFS. The dsconf is the fourth dot-field of the cnf name."""
    def taken(cnf_name):
        dsconf = cnf_name.split(".")[3]
        return client.exists(run_dir(cfg, naming.run_id(tag, dsconf)))
    return taken


def _retryable(run_id, runs_dir) -> bool:
    try:
        rec = records.load(run_id, runs_dir)
    except records.RecordError:
        return False
    return rec.site == "nersc" and rec.state == "enqueue_failed" and not rec.nersc.get("jobs")


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
    have = {j["offset"] for j in rec.nersc["jobs"]}
    all_slices = slices(rec.njobs, rec.slice_size)
    todo = [(o, c) for o, c in all_slices if o not in have]
    total = len(all_slices)
    rd = rec.nersc["run_dir"]
    dur = duration_s(rec.events_per_job, rec.nersc["walltime_s"])
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
                               f"{len(rec.nersc['jobs'])} job(s) are running; submit_run({rec.run_id!r}, "
                               f"'self') submits the rest") from e
        rec.nersc["jobs"].append({"slurm_id": jid, "offset": offset, "count": count,
                                  "submitted": records.now_utc()})
        records.save(rec, runs_dir)
    rec.state, rec.error = "submitted", None
    records.save(rec, runs_dir)


def run_beamline(*, tag, run_as, deck_ref, params, events_per_job, njobs, main_input, outloc, dsconf,
                 submit, deck_dir, deck_url, walltime_s) -> dict:
    cfg = nersc_config.load(paths.home())
    ident = identity.resolve(run_as, site="nersc", owner=cfg.owner)
    naming.validate_tag(tag)
    params = compose.validate_inputs(events_per_job=events_per_job, njobs=njobs, outloc="scratch", params=params)
    if outloc != "scratch":
        raise BeamkitError(f"outloc={outloc!r}: outputs of a NERSC run stay on CFS under {cfg.base_dir}; "
                           f"pass outloc='scratch' (the default) or omit it")
    validate_walltime(walltime_s)
    pin = decks.pin(deck_ref, deck_dir, deck_url, paths.decks_dir(), ident.production)
    if not (Path(pin.dir) / main_input).is_file():
        raise BeamkitError(f"main_input {main_input!r} not found in deck dir {pin.dir}")
    client = make_client(cfg)
    runs_dir = paths.runs_dir()
    # A retry after a failure that already created the remote run dir (e.g. a
    # transient upload error) must reuse that dsconf outright: probing the
    # remote for collision would see our own leftover directory as taken and
    # silently burn a new dsconf, orphaning the enqueue_failed record.
    base_dsconf = dsconf if dsconf is not None else naming.dsconf_base(pin.sha)
    candidate_run_id = naming.run_id(tag, base_dsconf)
    if _retryable(candidate_run_id, runs_dir):
        dsconf = base_dsconf
    else:
        dsconf = naming.allocate_dsconf(ident.owner, tag, naming.dsconf_base(pin.sha), _taken(cfg, client, tag),
                                        explicit=dsconf)
    run_id = naming.run_id(tag, dsconf)
    rdir = records.run_dir(runs_dir, run_id)
    if rdir.exists() and not _retryable(run_id, runs_dir):
        raise BeamkitError(f"run dir {rdir} already exists; a run id is never reused")
    rdir.mkdir(parents=True, exist_ok=True)
    rd = run_dir(cfg, run_id)
    cnf_name = naming.cnf_name(ident.owner, tag, dsconf)
    rec = records.RunRecord(run_id=run_id, tag=tag, dsconf=dsconf, owner=ident.owner, run_as=run_as,
                            deck=pin.as_record(), params=params, events_per_job=events_per_job, njobs=njobs,
                            outloc="scratch", slice_size=cfg.procs_per_node, state="created",
                            created=records.now_utc(), beamkit_version=__version__, site="nersc",
                            datasets=[naming.dataset(ident.owner, tag, dsconf)],
                            nersc={"run_dir": rd, "cnf": cnf_name, "walltime_s": walltime_s, "jobs": [],
                                   "config": cfg.as_record()})
    records.save(rec, runs_dir)
    try:
        local_cnf = rdir / cnf_name
        local_cnf.unlink(missing_ok=True)
        nersc_cnf.build_cnf(pin.dir, nersc_cnf.jobpars(owner=ident.owner, tag=tag, dsconf=dsconf,
                                                        main_input=main_input, events_per_job=events_per_job,
                                                        njobs=njobs, params=params), local_cnf)
        (rdir / "job.sh").write_text(nersc_templates.render_job(cfg, rd))
        (rdir / "inner.sh").write_text(nersc_templates.render_inner(
            cfg, run_id=run_id, run_dir=rd, owner=ident.owner, tag=tag, dsconf=dsconf,
            events_per_job=events_per_job, main_input=main_input, params=params))
        _remote_layout(cfg, client, rd, [(local_cnf, f"{rd}/{cnf_name}"), (rdir / "job.sh", f"{rd}/job.sh"),
                                         (rdir / "inner.sh", f"{rd}/inner.sh")])
    except Exception as e:
        rec.state, rec.error = "enqueue_failed", f"{type(e).__name__}: {e}"
        records.save(rec, runs_dir)
        raise BeamkitError(f"run {run_id}: nothing was submitted ({e}); fix the cause and call again, "
                           f"the run dir is reused") from e
    if submit:
        _submit_missing(rec, cfg, client, runs_dir)
    return rec.to_dict()


def submit_run(run_id, run_as) -> dict:
    cfg = nersc_config.load(paths.home())
    identity.resolve(run_as, site="nersc", owner=cfg.owner)
    runs_dir = paths.runs_dir()
    rec = records.load(run_id, runs_dir)
    if rec.site != "nersc":
        raise BeamkitError(f"run {run_id} is a {rec.site!r} run; submit_run is the NERSC backend's tool "
                           f"(the Fermilab path submits through make_recoveries)")
    if rec.state not in SUBMITTABLE:
        raise BeamkitError(f"run {run_id} is in state {rec.state!r}; submit_run applies to {SUBMITTABLE}")
    client = make_client(cfg)
    rd = rec.nersc["run_dir"]
    if not client.exists(f"{rd}/inner.sh"):
        raise BeamkitError(f"run {run_id}: remote layout at {rd} is missing inner.sh (job.sh cannot run); "
                           f"run run_beamline again")
    _submit_missing(rec, cfg, client, runs_dir)
    return rec.to_dict()


def _nts_index(name, rec):
    prefix = f"nts.{rec.owner}.{rec.tag}.{rec.dsconf}."
    base = name.rsplit("/", 1)[-1]
    if base.startswith(prefix) and base.endswith(".root"):
        seq = base[len(prefix):-5]
        if seq.isdigit():
            return int(seq)
    return None


def out_counts(client, rec) -> dict:
    """A run that failed before its remote layout was ever created (e.g.
    the cnf itself exceeded the upload cap) has no out/ dir yet -- that is
    zero files, not an error, same as client.exists() treats it."""
    try:
        entries = client.ls(f"{rec.nersc['run_dir']}/out")
    except iri.IriError as e:
        if "No such file" in (e.detail or str(e)):
            entries = []
        else:
            raise
    nts = {}
    logs = 0
    for e in entries:
        base = e["name"].rsplit("/", 1)[-1]
        idx = _nts_index(base, rec)
        if idx is not None:
            nts[idx] = e
        elif base.startswith(f"log.{rec.owner}.{rec.tag}.{rec.dsconf}."):
            logs += 1
    missing = sorted(set(range(rec.njobs)) - set(nts))
    return {"expected": rec.njobs, "nts": len(nts), "logs": logs, "missing": missing[:MISSING_CAP],
            "nts_files": [nts[i]["name"].rsplit("/", 1)[-1] for i in sorted(nts)]}


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
    cfg = nersc_config.load(paths.home())
    client = make_client(cfg)
    jobs = [_job_status(client, j) for j in rec.nersc.get("jobs", [])]
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
    cfg = nersc_config.load(paths.home())
    client = make_client(cfg)
    rd = rec.nersc["run_dir"]
    files = []
    for e in client.ls(f"{rd}/out"):
        idx = _nts_index(e["name"], rec)
        if idx is not None:
            name = e["name"].rsplit("/", 1)[-1]
            files.append({"name": name, "index": idx, "size": int(e["size"]), "path": f"{rd}/out/{name}"})
    files.sort(key=lambda f: f["index"])
    return {"run_id": rec.run_id, "run_dir": rd, "n_files": len(files),
            "total_size": sum(f["size"] for f in files), "files": files, "beamfiles": list(rec.beamfiles)}


from beamkit import beamfile

BEAMFILE_WALLTIME_CAP = 4 * 3600


def beamfile_duration(n_nts) -> int:
    return min(BEAMFILE_WALLTIME_CAP, 600 + 2 * int(n_nts))


def make_beamfile(*, run_id, flavor, run_as, plane, cuts, label, publish) -> dict:
    cfg = nersc_config.load(paths.home())
    identity.resolve(run_as, site="nersc", owner=cfg.owner)
    if publish:
        raise BeamkitError("publish=True on a NERSC run: publishing is part of harvest, which runs at Fermilab; "
                           "build with publish=False")
    label = flavor if label is None else label
    resolved = beamfile.resolve_cuts(flavor, cuts)
    beamfile.validate_label(label)
    beamfile.validate_plane(plane)
    runs_dir = paths.runs_dir()
    rec = records.load(run_id, runs_dir)
    if rec.site != "nersc":
        raise BeamkitError(f"run {run_id} is a {rec.site!r} run; pass site={rec.site!r}")
    if any(b["label"] == label for b in rec.beamfiles):
        raise BeamkitError(f"run {run_id}: label {label!r} is already spent by a submitted beam-file job; "
                           f"a beam file is never overwritten (pick another label)")
    client = make_client(cfg)
    counts = out_counts(client, rec)
    if counts["nts"] == 0:
        raise BeamkitError(f"run {run_id}: 0 of {counts['expected']} nts files on CFS "
                           f"({counts['logs']} logs); nothing to build a beam file from")
    rd = rec.nersc["run_dir"]
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
