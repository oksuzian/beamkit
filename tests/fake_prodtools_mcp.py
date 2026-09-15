"""A stand-in for the two prodtools MCP servers, run as a REAL stdio child:
`python -m tests.fake_prodtools_mcp write` or `... read`. Same tool names
and argument names as prodtools; canned answers; failures on demand via
environment variables (see docs/plans/2026-09-15-prodtools-over-mcp.md,
Task 2). The read role envelopes failures as {"error": {...}} the way
prodtools' safe_tool does; the write role raises, as prodtools-write does.
"""
import functools
import json
import os
import sys
import time

from mcp.server.fastmcp import FastMCP


def _record(tool, args):
    path = os.environ.get("FAKE_PRODTOOLS_CALLS")
    if path:
        with open(path, "a") as fh:
            fh.write(json.dumps({"tool": tool, "args": args}) + "\n")


def _trip(tool):
    if os.environ.get("FAKE_PRODTOOLS_DIE") == tool:
        sys.stderr.write(f"fake prodtools: dying inside {tool}\n")
        sys.stderr.flush()
        os._exit(7)
    if os.environ.get("FAKE_PRODTOOLS_FAIL") == tool:
        raise RuntimeError(f"{tool} refused by the fake; remedy: try the other thing")


def _shape(tool):
    """Returning-not-raising sibling of _trip: FAKE_PRODTOOLS_SHAPE=<tool> makes
    that read tool return a result missing its expected key(s), simulating a
    result-shape drift between beamkit and prodtools."""
    return os.environ.get("FAKE_PRODTOOLS_SHAPE") == tool


def _envelope(kind, message, remedy):
    return {"error": {"kind": kind, "message": message, "remedy": remedy}}


# --- write role: raw functions, exceptions propagate (isError results)

def push_cnf(json: str, desc: str, dsconf: str, slice_size: int, run_as: str,
             confirm: bool = False, prodtools_dir: str = None):
    args = {"json": json, "desc": desc, "dsconf": dsconf, "slice_size": slice_size,
            "run_as": run_as, "confirm": confirm}
    if prodtools_dir is not None:
        args["prodtools_dir"] = prodtools_dir
    _record("push_cnf", args)
    _trip("push_cnf")
    return {"tarball": f"cnf.u.{desc}.{dsconf}.0.tar", "datasets": ["nts.*.root"],
            "campaign_id": 7, "njobs": 3}


def run_submissions(run_as: str, campaign_id: int = None, confirm: bool = False):
    _record("run_submissions", {"run_as": run_as, "campaign_id": campaign_id, "confirm": confirm})
    _trip("run_submissions")
    return {"rc": 0, "needs_attention": False, "campaign_id": campaign_id, "output": "tick ok"}


def push_file(path: str, location: str, parents: list, run_as: str, confirm: bool = False):
    _record("push_file", {"path": path, "location": location, "parents": parents,
                          "run_as": run_as, "confirm": confirm})
    _trip("push_file")
    return {"name": "ok"}


# --- read role: safe_tool-style envelopes

def _safe(fn):
    @functools.wraps(fn)
    def wrapper(**kw):
        try:
            return fn(**kw)
        except RuntimeError as e:
            return _envelope("internal", str(e), "check the fake")
    return wrapper


@_safe
def campaign_status(campaign: str = None, campaign_id: int = None, include_queue: bool = True,
                    include_outputs: bool = True, mine: bool = False) -> dict:
    _record("campaign_status", {"campaign": campaign, "campaign_id": campaign_id,
                                "include_queue": include_queue, "include_outputs": include_outputs,
                                "mine": mine})
    _trip("campaign_status")
    return {"called": {"campaign_id": campaign_id, "mine": mine}}


@_safe
def list_campaigns(state: str = None, mine: bool = False) -> dict:
    _record("list_campaigns", {"state": state, "mine": mine})
    _trip("list_campaigns")
    if _shape("list_campaigns"):
        return {"unexpected": 1}
    return {"count": 1, "db_path": "/db", "called": {"state": state, "mine": mine},
            "campaigns": [{"id": 7, "state": "complete", "tarball": "cnf.u.T.e470313.0.tar"}]}


@_safe
def locate_file(name: str) -> dict:
    _record("locate_file", {"name": name})
    _trip("locate_file")
    if _shape("locate_file"):
        return {"unexpected": 1}
    exists = name.endswith("e470313.0.tar")
    return {"name": name, "exists": exists, "locations": ["enstore:/x"] if exists else []}


@_safe
def dataset_files(dataset: str, location: str) -> dict:
    _record("dataset_files", {"dataset": dataset, "location": location})
    _trip("dataset_files")
    if _shape("dataset_files"):
        return {"unexpected": 1}
    if location not in ("scratch", "disk", "tape"):
        return _envelope("invalid_argument", f"unknown dataset location {location!r} for {dataset}",
                         "Use one of scratch, disk, tape.")
    root = f"/pnfs/{location}/{dataset}"
    listed = json.loads(os.environ.get("FAKE_PRODTOOLS_FILES") or
                        '[["nts.u.T.e470313.00000002.root", 20], ["nts.u.T.e470313.00000000.root", 10]]')
    files = [{"name": n, "size": s, "path": f"{root}/aa/bb/{n}"} for n, s in sorted(listed)]
    return {"dataset": dataset, "location": location, "root": root, "n_files": len(files),
            "total_size": sum(f["size"] for f in files), "files": files}


@_safe
def get_server_info() -> dict:
    _record("get_server_info", {})
    return {"name": "prodtools", "writes": False}


# --- both roles

def env_probe() -> dict:
    """What the child sees: proves the parent's environment reached it."""
    return {"mark": os.environ.get("FAKE_PRODTOOLS_MARK")}


ROLES = {
    "write": {"push_cnf": push_cnf, "run_submissions": run_submissions, "push_file": push_file},
    "read": {"campaign_status": campaign_status, "list_campaigns": list_campaigns,
             "locate_file": locate_file, "dataset_files": dataset_files,
             "get_server_info": get_server_info},
}


def main():
    role = sys.argv[1]
    if os.environ.get("FAKE_PRODTOOLS_NO_START") == "1":
        sys.stderr.write("fake prodtools: refusing to start\n")
        sys.stderr.flush()
        sys.exit(3)
    if os.environ.get("FAKE_PRODTOOLS_HANG_START") == "1":
        time.sleep(600)
    omit = set(filter(None, os.environ.get("FAKE_PRODTOOLS_OMIT", "").split(",")))
    server = FastMCP(f"fake-prodtools-{role}")
    for name, fn in ROLES[role].items():
        if name not in omit:
            server.tool(name=name)(fn)
    server.tool(name="env_probe")(env_probe)
    sys.stderr.write(f"fake prodtools {role}: serving\n")
    sys.stderr.flush()
    server.run()


if __name__ == "__main__":
    main()
