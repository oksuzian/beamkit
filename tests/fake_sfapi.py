"""In-memory stand-in for api.nersc.gov/api/v1.2, shaped by responses
recorded 2026-09-12/13: failures arrive as HTTP 200 with status "ERROR",
ls names are absolute, sacct rows carry Slurm vocabulary, command and
job submission are tasks. Same attribute names as tests/fake_iri.py so
backend tests can assert on files/dirs/jobs the same way; jobs[jid]["spec"]
is the PSI/J-shaped dict parsed back out of the sbatch script."""
import json
import re

from tests.fake_iri import FakeResponse

BASE = "https://api.nersc.gov/api/v1.2"
MACHINE = "perlmutter"


def parse_sbatch(script) -> dict:
    """Recover the fields backend tests look at from a rendered script."""
    opts = dict(re.findall(r"^#SBATCH (\S+?)[= ](\S+)$", script, re.M))
    env = dict(re.findall(r"^export (\w+)=(\S+)$", script, re.M))
    return {"name": opts.get("-J"),
            "environment": {k: v.strip("'") for k, v in env.items()},
            "resources": {"node_count": int(opts.get("-N", 1)), "process_count": int(opts.get("-n", 1)),
                          "processes_per_node": int(opts.get("--ntasks-per-node", opts.get("-n", 1))),
                          "cpu_cores_per_process": int(opts.get("-c", 1)),
                          "exclusive_node_use": "#SBATCH --exclusive" in script},
            "attributes": {"duration": int(opts.get("-t", 0)) * 60, "queue_name": opts.get("-q"),
                           "account": opts.get("-A"),
                           "custom_attributes": {"constraint": opts.get("-C"), "licenses": opts.get("-L")}},
            "script": script}


class FakeSfapiSession:
    def __init__(self):
        self.calls = []
        self.files = {}          # remote path -> bytes
        self.dirs = {"/global/cfs/cdirs/m4599/Users/u"}
        self.jobs = {}           # job id -> {"state", "exit_code", "spec"}  (state in Slurm vocabulary)
        self.tasks = {}
        self.next_job = 58197742
        self.next_task = 8593000
        self.fail_submit_at = None

    def _task(self, result):
        tid = str(self.next_task)
        self.next_task += 1
        self.tasks[tid] = result
        return FakeResponse(200, {"task_id": tid, "status": "OK", "error": None})

    def _ls_entry(self, p, kind):
        size = len(self.files[p]) if kind == "f" else 4096
        return {"perms": "drwxr-sr-x" if kind == "d" else "-rw-r--r--", "hardlinks": 1, "user": "u",
                "group": "m4599", "size": float(size), "date": "2026-09-12T14:28:17", "name": p}

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        path = url[len(BASE):]
        m = re.match(r"^/tasks/(\d+)$", path)
        if m:
            r = self.tasks[m.group(1)]
            return FakeResponse(200, {"id": m.group(1), "status": "completed", "result": json.dumps(r)})
        if path == "/account/":
            return FakeResponse(200, {"name": "u", "uid": 1})
        if path == f"/utilities/command/{MACHINE}" and method == "POST":
            argv = kw["data"]["executable"].split()
            if argv[:2] == ["mkdir", "-p"]:
                p = argv[2]
                parts = p.strip("/").split("/")
                for i in range(1, len(parts) + 1):
                    self.dirs.add("/" + "/".join(parts[:i]))
                return self._task({"status": "ok", "exit_code": 0, "output": "", "error": ""})
            return self._task({"status": "ok", "exit_code": 127, "output": "", "error": f"command not allowed: {argv}"})
        m = re.match(rf"^/utilities/(ls|upload|download)/{MACHINE}/(/.*)$", path)   # double slash, as the API needs
        if m:
            op, p = m.group(1), m.group(2)
            if op == "ls":
                if p in self.files:
                    return FakeResponse(200, {"status": "OK", "entries": [self._ls_entry(p, "f")], "error": None})
                if p not in self.dirs:
                    return FakeResponse(200, {"status": "ERROR", "entries": [],
                                              "error": f"/bin/ls: cannot access '{p}': No such file or directory\n"})
                kids = [self._ls_entry(d, "d") for d in self.dirs if d.rsplit("/", 1)[0] == p]
                kids += [self._ls_entry(f, "f") for f in self.files if f.rsplit("/", 1)[0] == p]
                dot = dict(self._ls_entry(p, "d"), name=".")
                return FakeResponse(200, {"status": "OK", "entries": [dot, *kids], "error": None})
            if op == "upload":
                if method != "PUT":
                    return FakeResponse(405, {"detail": "Method Not Allowed"})
                if p.rsplit("/", 1)[0] not in self.dirs:
                    return FakeResponse(200, {"status": "ERROR", "output": None, "error": "No such file or directory"})
                name, fh = kw["files"]["file"]
                self.files[p] = fh.read()
                return FakeResponse(200, {"status": "OK", "output": None, "error": None})
            if op == "download":
                if p not in self.files:
                    return FakeResponse(200, {"status": "ERROR", "file": None, "is_binary": False, "error": "No such file"})
                return FakeResponse(200, {"status": "OK", "file": self.files[p].decode(), "is_binary": False, "error": None})
        if path == f"/compute/jobs/{MACHINE}" and method == "POST":
            k = sum(1 for c in self.calls if c[1] == url and c[0] == "POST") - 1
            if self.fail_submit_at == k:
                return self._task({"status": "error", "jobid": None, "error": "sbatch: error: Batch job submission failed"})
            jid = str(self.next_job)
            self.next_job += 1
            self.jobs[jid] = {"state": "PENDING", "exit_code": 0, "spec": parse_sbatch(kw["data"]["job"])}
            return self._task({"status": "ok", "jobid": jid, "error": None})
        if path == f"/compute/jobs/{MACHINE}" and method == "GET":
            kwargs = dict(x.split("=", 1) for x in kw["params"].get("kwargs", []))
            j = self.jobs.get(kwargs.get("jobid"))
            if j is None or kw["params"].get("cached") != "false":
                return FakeResponse(200, {"status": "OK", "output": [], "error": None})
            return FakeResponse(200, {"status": "OK", "error": None, "output": [
                {"jobid": kwargs["jobid"], "state": j["state"], "elapsed": "00:05:13", "nodelist": "nid004381",
                 "exitcode": f"{j['exit_code']}:0", "qos": j["spec"]["attributes"]["queue_name"]}]})
        return FakeResponse(404, {"detail": "Not Found"})
