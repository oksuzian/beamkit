"""Second transport: the legacy NERSC Superfacility API v1.2
(api.nersc.gov), same client credential and same method surface as the IRI
v2 client, so backends/nersc.py cannot tell the two apart. Selected by
`transport = "sfapi"` in nersc.toml."""
import base64
import json
import math
import shlex
from pathlib import Path

from beamkit.iri import BaseClient, IriError

# Slurm sacct/squeue states -> the IRI vocabulary the backend reads.
STATE = {
    "PENDING": "queued", "CONFIGURING": "queued", "SUSPENDED": "queued", "REQUEUED": "queued",
    "RUNNING": "active", "COMPLETING": "active", "STAGE_OUT": "active",
    "COMPLETED": "completed",
    "FAILED": "failed", "OUT_OF_MEMORY": "failed", "NODE_FAIL": "failed", "BOOT_FAIL": "failed",
    "DEADLINE": "failed", "PREEMPTED": "failed", "TIMEOUT": "failed",
    "CANCELLED": "canceled",
}


def map_state(slurm_state) -> str:
    s = (slurm_state or "").split()[0].upper() if slurm_state else ""
    if s.startswith("CANCELLED"):
        return "canceled"
    try:
        return STATE[s]
    except KeyError:
        raise IriError(f"unknown Slurm state {slurm_state!r}")


def render_sbatch(spec) -> str:
    """The PSI/J JobSpec from backends.nersc.job_spec as an sbatch script;
    inherit_environment=False becomes --export=NONE."""
    res, attr = spec["resources"], spec["attributes"]
    custom = attr.get("custom_attributes") or {}
    minutes = max(1, math.ceil(int(attr["duration"]) / 60))
    lines = ["#!/bin/bash",
             f"#SBATCH -J {spec['name']}",
             f"#SBATCH -A {attr['account']}",
             f"#SBATCH -q {attr['queue_name']}",
             f"#SBATCH -N {res['node_count']}",
             f"#SBATCH -n {res['process_count']}",
             f"#SBATCH --ntasks-per-node={res['processes_per_node']}",
             f"#SBATCH -c {res['cpu_cores_per_process']}",
             f"#SBATCH -t {minutes}",
             f"#SBATCH -o {spec['stdout_path']}",
             f"#SBATCH -e {spec['stderr_path']}"]
    if res.get("exclusive_node_use"):
        lines.append("#SBATCH --exclusive")
    if custom.get("constraint"):
        lines.append(f"#SBATCH -C {custom['constraint']}")
    # custom_attributes "licenses"/"module" are IRI-side hints; Perlmutter's
    # sbatch rejects `-L cvmfs` ("Invalid license specification", 2026-09-13)
    # and cvmfs is mounted on every node without a module, so neither is rendered.
    if not spec.get("inherit_environment", True):
        lines.append("#SBATCH --export=NONE")
    lines.append(f"cd {shlex.quote(spec['directory'])} || exit 2")
    for k, v in (spec.get("environment") or {}).items():
        lines.append(f"export {k}={shlex.quote(str(v))}")
    cmd = " ".join(shlex.quote(a) for a in [spec["executable"], *spec.get("arguments", [])])
    # one task per process, as the IRI adapter does: srun launches
    # process_count copies and gives each its SLURM_PROCID; a bare exec
    # would run the executable once on the batch host
    lines.append(f"exec srun --export=ALL {cmd}")
    return "\n".join(lines) + "\n"


class SfapiClient(BaseClient):
    _FAILED = ("failed", "canceled", "cancelled")

    def _url(self, method, path) -> str:
        return f"{self.cfg.sfapi_api.rstrip('/')}{path}"

    def _body(self, method, path, body):
        # v1.2 signals most failures inside a 200
        if isinstance(body, dict) and str(body.get("status", "")).upper() == "ERROR":
            err = str(body.get("error") or body)
            raise IriError(f"{method} {path} -> ERROR: {err[:300]}", detail=err)
        return body

    def _task_ref(self, resp):
        return resp["task_id"], f"/tasks/{resp['task_id']}"

    def _task_done(self, tid, t) -> dict:
        # v1.2 answers with the result as a JSON string, and reports a failed
        # task as a non-ok status inside it
        res = t.get("result")
        try:
            res = json.loads(res) if isinstance(res, str) else (res or {})
        except ValueError:
            res = {"output": res}
        if isinstance(res, dict) and str(res.get("status", "")).lower() not in ("", "ok"):
            err = res.get("error") or res
            raise IriError(f"task {tid} failed: {err}", detail=str(err))
        return res

    def _task_error(self, t) -> str:
        return str(t.get("result") or "")

    def whoami(self) -> dict:
        r = self._req("GET", "/account/")
        return {"username": r.get("name") or r.get("user") or r.get("uid"), **r} if isinstance(r, dict) else {}

    # --- filesystem
    def mkdir(self, path) -> None:
        # the one shell command this transport runs; v1.2 has no mkdir endpoint
        cmd = f"mkdir -p {shlex.quote(path)}"
        res = self.wait_task(self._req("POST", f"/utilities/command/{self.cfg.machine}",
                                       data={"executable": cmd}))
        if int(res.get("exit_code", 0) or 0) != 0:
            raise IriError(f"{cmd!r} exited {res.get('exit_code')}: {res.get('error')}",
                           detail=str(res.get("error")))

    def _upath(self, op, path) -> str:
        # the path keeps its leading slash after the machine segment
        # (".../perlmutter//global/..."); with one slash the API resolves
        # it relative to the service's cwd and answers "No such file"
        return f"/utilities/{op}/{self.cfg.machine}/{path}"

    def ls(self, path) -> list[dict]:
        r = self._req("GET", self._upath("ls", path))
        out = []
        for e in r.get("entries") or []:
            name = e.get("name", "")
            if name in (".", ".."):
                continue
            full = name if name.startswith("/") else f"{path.rstrip('/')}/{name}"
            perms = e.get("perms") or ""
            out.append({"name": full, "type": "d" if perms.startswith("d") else "f",
                        "size": str(int(float(e.get("size") or 0))), "user": e.get("user"),
                        "group": e.get("group"), "permissions": perms, "last_modified": e.get("date")})
        return out

    def upload(self, local, remote) -> None:
        local = Path(local)
        self._check_upload(local)
        with open(local, "rb") as fh:
            self._req("PUT", self._upath("upload", remote), files={"file": (local.name, fh)})

    def _download(self, remote, binary):
        r = self._req("GET", self._upath("download", remote), params={"binary": binary})
        f = r.get("file")
        if f is None:
            raise IriError(f"download {remote}: no file in response {r!r}")
        return f

    def download(self, remote) -> str:
        return self._download(remote, "false")

    def download_bytes(self, remote) -> bytes:
        # binary=true answers the file base64-encoded in "file"
        try:
            return base64.b64decode(self._download(remote, "true"), validate=True)
        except (ValueError, TypeError) as e:
            raise IriError(f"download {remote}: response is not base64: {e}") from e

    # --- compute
    def submit(self, spec) -> str:
        res = self.wait_task(self._req("POST", f"/compute/jobs/{self.cfg.machine}",
                                       data={"job": render_sbatch(spec), "isPath": "false"}))
        jid = res.get("jobid") if isinstance(res, dict) else None
        if not jid:
            raise IriError(f"submit returned no job id: {res!r}")
        return str(jid)

    def status(self, job_id) -> dict:
        r = self._req("GET", f"/compute/jobs/{self.cfg.machine}",
                      params={"index": 0, "sacct": "true", "cached": "false", "kwargs": [f"jobid={job_id}"]})
        rows = r.get("output") or []
        if not rows:
            # sacct has no row until the job is registered: treat as queued
            return {"state": "queued", "exit_code": None, "meta_data": {"state": "PENDING"}}
        row = rows[0]
        try:
            exit_code = int(str(row.get("exitcode", "0:0")).split(":")[0])
        except ValueError:
            exit_code = None
        return {"state": map_state(row.get("state")), "exit_code": exit_code,
                "meta_data": {"nodelist": None, "elapsed": None, **row}}
