"""The NERSC backend: a run is a directory on CFS plus one Slurm job per
slice of procs_per_node indices, driven through the IRI Facility API.
Nothing here touches SAM, dCache, prodtools or a ledger."""
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
from beamkit.decks import DEFAULT_DECK_URL   # noqa: F401  (kept for callers that pass the default)

make_client = iri.IriClient
SUBMITTABLE = ("created", "partially_submitted")


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


def _remote_layout(client, rd, uploads):
    for d in (rd, f"{rd}/out", f"{rd}/slurm", f"{rd}/beamfiles"):
        client.mkdir(d)
    for local, remote in uploads:
        client.upload(local, remote)


def _submit_missing(rec, cfg, client, runs_dir):
    """Submit every slice whose offset the record does not carry, in order.
    A failure part way is saved as partially_submitted and raised; the next
    submit_run continues from there."""
    have = {j["offset"] for j in rec.nersc["jobs"]}
    todo = [(o, c) for o, c in slices(rec.njobs, cfg.procs_per_node) if o not in have]
    total = len(slices(rec.njobs, cfg.procs_per_node))
    rd = rec.nersc["run_dir"]
    dur = duration_s(rec.events_per_job, rec.nersc["walltime_s"])
    for k, (offset, count) in enumerate(todo, start=len(have)):
        spec = job_spec(cfg, run_id=rec.run_id, run_dir=rd, offset=offset, count=count, duration=dur)
        try:
            jid = client.submit(spec, idem_key=f"{rec.run_id}/{offset}")
        except iri.IriError as e:
            rec.state, rec.error = "partially_submitted", f"job {k} of {total} (offset {offset}) failed to submit: {e}"
            records.save(rec, runs_dir)
            raise BeamkitError(f"run {rec.run_id}: job {k} of {total} (offset {offset}) failed to submit: {e}; "
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
    dsconf = naming.allocate_dsconf(ident.owner, tag, naming.dsconf_base(pin.sha), _taken(cfg, client, tag),
                                    explicit=dsconf)
    run_id = naming.run_id(tag, dsconf)
    runs_dir = paths.runs_dir()
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
        _remote_layout(client, rd, [(local_cnf, f"{rd}/{cnf_name}"), (rdir / "job.sh", f"{rd}/job.sh"),
                                    (rdir / "inner.sh", f"{rd}/inner.sh")])
    except BeamkitError as e:
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
    _submit_missing(rec, cfg, make_client(cfg), runs_dir)
    return rec.to_dict()
