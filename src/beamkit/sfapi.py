"""Second transport: the legacy NERSC Superfacility API v1.2
(api.nersc.gov). Same client credential as the IRI v2 transport, same
seven-method surface the NERSC backend uses (mkdir, ls, exists, upload,
download, submit, status), same return shapes, so backends/nersc.py
cannot tell the two apart. Selected by `transport = "sfapi"` in
nersc.toml. Differences worth knowing: v1.2 reports failures as HTTP 200
with status "ERROR" and an error string; a job is submitted as an sbatch
script rendered here from the PSI/J spec; job state comes from sacct
(`cached=false`, or a job older than today is invisible); mkdir is the
one shell command run (`utilities/command mkdir -p`), v1.2 having no
mkdir endpoint."""
import math
import shlex
import time
from pathlib import Path

from beamkit import iri
from beamkit.iri import IriError, _detail

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
    """The PSI/J JobSpec from backends.nersc.job_spec as an sbatch script.
    inherit_environment=False becomes --export=NONE; the environment block
    is exported by the script itself."""
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
    # and cvmfs is mounted on every node without a module, so neither is
    # rendered.
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


class SfapiClient:
    def __init__(self, cfg, token_provider=None, session=None):
        self.cfg = cfg
        self._token_provider = token_provider or (lambda: iri.sfapi_token(cfg))
        if session is None:
            import requests
            session = requests.Session()
        self._session = session
        self._token = None
        self._base = cfg.sfapi_api.rstrip("/")
        self._machine = cfg.machine

    def _headers(self):
        if self._token is None:
            self._token = self._token_provider()
        return {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}

    def _req(self, method, path, **kw):
        url = f"{self._base}{path}"
        try:
            resp = self._session.request(method, url, headers=self._headers(), timeout=120, **kw)
            if not resp.ok:
                detail = _detail(resp)
                hint = f"; {iri._AUTH_HINT}" if resp.status_code in (401, 403) else ""
                raise IriError(f"{method} {path} -> {resp.status_code}: {detail}{hint}",
                               status=resp.status_code, detail=detail)
            body = resp.json() if resp.text else {}
        except IriError:
            raise
        except Exception as e:
            raise IriError(f"{method} {path}: {type(e).__name__}: {e}") from e
        # v1.2 signals most failures inside a 200
        if isinstance(body, dict) and str(body.get("status", "")).upper() == "ERROR":
            err = str(body.get("error") or body)
            raise IriError(f"{method} {path} -> ERROR: {err[:300]}", detail=err)
        return body

    def whoami(self) -> dict:
        r = self._req("GET", "/account/")
        return {"username": r.get("name") or r.get("user") or r.get("uid"), **r} if isinstance(r, dict) else {}

    # --- tasks
    def wait_task(self, resp, timeout_s=600, poll_s=3) -> dict:
        try:
            tid = resp["task_id"]
        except Exception as e:
            raise IriError(f"wait_task: malformed task response {resp!r}: {type(e).__name__}: {e}") from e
        deadline = time.monotonic() + timeout_s
        while True:
            t = self._req("GET", f"/tasks/{tid}")
            st = t.get("status")
            if st == "completed":
                import json
                res = t.get("result")
                try:
                    res = json.loads(res) if isinstance(res, str) else (res or {})
                except ValueError:
                    res = {"output": res}
                if isinstance(res, dict) and str(res.get("status", "")).lower() not in ("", "ok"):
                    err = res.get("error") or res
                    raise IriError(f"task {tid} failed: {err}", detail=str(err))
                return res
            if st in ("failed", "canceled", "cancelled"):
                err = str(t.get("result") or "")
                raise IriError(f"task {tid} {st}: {err}", detail=err)
            if time.monotonic() >= deadline:
                raise IriError(f"task {tid} still {st} after {timeout_s} s")
            time.sleep(poll_s)

    # --- filesystem
    def _command(self, argv) -> dict:
        cmd = " ".join(shlex.quote(a) for a in argv)
        resp = self._req("POST", f"/utilities/command/{self._machine}", data={"executable": cmd})
        res = self.wait_task(resp)
        if int(res.get("exit_code", 0) or 0) != 0:
            raise IriError(f"{cmd!r} exited {res.get('exit_code')}: {res.get('error')}", detail=str(res.get("error")))
        return res

    def mkdir(self, path) -> None:
        # the only command this transport runs; v1.2 has no mkdir endpoint
        self._command(["mkdir", "-p", path])

    def _upath(self, op, path) -> str:
        # the path keeps its leading slash after the machine segment
        # (".../perlmutter//global/..."); with one slash the API resolves
        # it relative to the service's cwd and answers "No such file"
        return f"/utilities/{op}/{self._machine}/{path}"

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

    def exists(self, path) -> bool:
        try:
            self.ls(path)
            return True
        except IriError as e:
            if "No such file" in (e.detail or str(e)):
                return False
            raise

    def upload(self, local, remote) -> None:
        local = Path(local)
        size = local.stat().st_size
        if size > iri.UPLOAD_MAX:
            raise IriError(f"{local}: {size} bytes exceeds the {iri.UPLOAD_MAX}-byte upload cap of the API")
        with open(local, "rb") as fh:
            self._req("PUT", self._upath("upload", remote), files={"file": (local.name, fh)})

    def download(self, remote) -> str:
        r = self._req("GET", self._upath("download", remote), params={"binary": "false"})
        f = r.get("file")
        if f is None:
            raise IriError(f"download {remote}: no file in response {r!r}")
        return f

    # --- compute
    def submit(self, spec) -> str:
        script = render_sbatch(spec)
        resp = self._req("POST", f"/compute/jobs/{self._machine}", data={"job": script, "isPath": "false"})
        res = self.wait_task(resp)
        jid = res.get("jobid") if isinstance(res, dict) else None
        if not jid:
            raise IriError(f"submit returned no job id: {res!r}")
        return str(jid)

    def status(self, job_id) -> dict:
        r = self._req("GET", f"/compute/jobs/{self._machine}",
                      params={"index": 0, "sacct": "true", "cached": "false", "kwargs": [f"jobid={job_id}"]})
        rows = r.get("output") or []
        if not rows:
            # sacct has no row until the job is registered: treat as queued
            return {"state": "queued", "exit_code": None, "meta_data": {"state": "PENDING"}}
        row = rows[0]
        state = map_state(row.get("state"))
        try:
            exit_code = int(str(row.get("exitcode", "0:0")).split(":")[0])
        except ValueError:
            exit_code = None
        md = dict(row)
        md.setdefault("nodelist", row.get("nodelist"))
        md.setdefault("elapsed", row.get("elapsed"))
        return {"state": state, "exit_code": exit_code, "meta_data": md}
