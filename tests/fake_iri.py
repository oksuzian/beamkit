"""An in-memory stand-in for api.iri.nersc.gov/api/v2, shaped by the
responses recorded 2026-09-10/11 (plan ruling 4). Enough surface for
IriClient; nothing else."""
import json
import re
import uuid

CFS = "59e80c79-4dfd-4c53-9c07-7405685fcd37"
COMPUTE = "94351904-6dba-4c16-b5cd-fbd280d8615b"
BASE = "https://api.iri.nersc.gov/api/v2"


class FakeResponse:
    def __init__(self, status_code, body=None, text=None):
        self.status_code = status_code
        self._json = body
        self.text = text if text is not None else (json.dumps(body) if body is not None else "")

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


class FakeSession:
    def __init__(self):
        self.calls = []
        self.files = {}        # remote path -> bytes
        self.dirs = {"/global/cfs/cdirs/m4599/Users/u"}
        self.jobs = {}         # job id -> {"state", "exit_code", "spec"}
        self.tasks = {}        # task id -> result dict or ("failed", detail)
        self.next_job = 58197742
        self.fail_submit_at = None    # k-th submit (0-based) returns 500

    def _task(self, result):
        tid = str(uuid.uuid4())
        self.tasks[tid] = result
        return FakeResponse(200, {"task_id": tid, "task_uri": f"{BASE}/task/{tid}"})

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        path = url[len(BASE):]
        m = re.match(r"^/task/([^/]+)$", path)
        if m:
            r = self.tasks[m.group(1)]
            if isinstance(r, tuple):
                return FakeResponse(200, {"status": r[0], "result": {"error": r[1]}})
            return FakeResponse(200, {"status": "completed", "result": r})
        if path == "/account/whoami":
            return FakeResponse(200, {"name": "u", "uid": 1})
        if path == "/compute/resources":
            return FakeResponse(200, [{"id": "3cf3c048-855e-4dd8-a189-065a483954bb", "name": "jobs",
                                      "resource_type": "urn:doe-iri:resource:unknown", "current_status": "up",
                                      "description": "Slurm Commands"},
                                     {"id": COMPUTE, "name": "compute", "resource_type": "urn:doe-iri:resource:compute",
                                      "current_status": "up", "description": "Compute Nodes"}])
        if path == "/filesystem/resources":
            return FakeResponse(200, [{"id": CFS, "name": "cfs", "resource_type": None, "current_status": None}])
        m = re.match(rf"^/filesystem/(mkdir|ls|upload|download)/{CFS}$", path)
        if m:
            op = m.group(1)
            if op == "upload":
                remote = kw["params"]["path"]
                parent = remote.rsplit("/", 1)[0]
                if parent not in self.dirs:
                    return self._task(("failed", "Exception: Error: 400: Error downloading: No such file"))
                name, fh = kw["files"]["file"]
                self.files[remote] = fh.read()
                return self._task({"output": f"Uploaded {remote}"})
            p = kw["json"]["path"]
            if op == "mkdir":
                if p in self.dirs:
                    return self._task({"output": None})
                parent = p.rsplit("/", 1)[0]
                if parent not in self.dirs:
                    return self._task(("failed", f"Exception: Error: 404: mkdir: cannot create directory "
                                                 f"'{p}': No such file or directory"))
                self.dirs.add(p)
                return self._task({"output": None})
            if op == "ls":
                if p in self.files:
                    return self._task({"output": [self._entry(p, "f")]})
                if p not in self.dirs:
                    return self._task(("failed", f"Exception: Error: 400: ls: cannot access '{p}': No such file or directory"))
                kids = [self._entry(d, "d") for d in self.dirs if d.rsplit("/", 1)[0] == p]
                kids += [self._entry(f, "f") for f in self.files if f.rsplit("/", 1)[0] == p]
                return self._task({"output": kids})
            if op == "download":
                if p not in self.files:
                    return self._task(("failed", "Exception: Error: 400: No such file"))
                return self._task({"output": self.files[p].decode()})
        if path == f"/compute/job/{COMPUTE}" and method == "POST":
            k = sum(1 for c in self.calls if c[1] == url and c[0] == "POST") - 1
            if self.fail_submit_at == k:
                return FakeResponse(500, {"type": "about:blank", "status": 500, "title": "Internal Server Error",
                                          "detail": "sbatch: error: Batch job submission failed"})
            jid = str(self.next_job)
            self.next_job += 1
            self.jobs[jid] = {"state": "queued", "exit_code": 0, "spec": kw["json"],
                              "idem": kw["headers"].get("Idempotency-Key")}
            return FakeResponse(200, {"id": jid})
        m = re.match(rf"^/compute/status/{COMPUTE}/(\d+)$", path)
        if m:
            j = self.jobs.get(m.group(1))
            if j is None:
                return FakeResponse(404, {"type": "about:blank", "status": 404, "title": "Not Found",
                                          "detail": f"job {m.group(1)} not found"})
            return FakeResponse(200, {"status": {"state": j["state"], "exit_code": j["exit_code"], "time": 0.0,
                                                 "message": None,
                                                 "meta_data": {"elapsed": "00:05:13", "nodelist": "nid004381",
                                                               "state": j["state"].upper(), "exitcode": f"{j['exit_code']}:0"}}})
        return FakeResponse(404, {"type": "about:blank", "status": 404, "title": "Not Found", "detail": path})

    def _entry(self, p, kind):
        size = len(self.files[p]) if kind == "f" else 4096
        return {"name": p, "type": kind, "link_target": "", "user": "u", "group": "m4599",
                "permissions": "644", "last_modified": "2026-09-11 07:03:50", "size": str(size)}
