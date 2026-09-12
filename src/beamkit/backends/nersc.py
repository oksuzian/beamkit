"""The NERSC backend: a run is a directory on CFS plus one Slurm job per
slice of procs_per_node indices, driven through the IRI Facility API.
Nothing here touches SAM, dCache, prodtools or a ledger."""
import math

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
