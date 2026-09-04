"""The ONLY module that imports prodtools. Every import is inside a function
so the rest of beamkit, and every unit test, runs with prodtools absent.

Wraps prodtools' own MCP tool functions in-process (their gates included)
plus three read-only helpers from prodtools utils. The only subprocess
here is `git rev-parse` in prodtools_info; no ledger access."""
import os
import subprocess

_HINT = ("prodtools is not importable here. Start beamkit through "
         "scripts/start_mcp.sh with BEAMKIT_PRODTOOLS_ROOT set to a prodtools "
         "checkout whose mcp/.venv is installed")


class BridgeError(RuntimeError):
    pass


def _import(modname):
    import importlib
    try:
        return importlib.import_module(modname)
    except ImportError as e:
        raise BridgeError(f"{_HINT} ({modname}: {e})") from e


def push_cnf(json_path, desc, dsconf, slice_size, run_as, confirm) -> dict:
    tools = _import("prodtools_mcp_write.tools")
    kw = {}
    dev_dir = os.environ.get("BEAMKIT_PRODTOOLS_DIR")
    if dev_dir:
        kw["prodtools_dir"] = dev_dir
    return tools.push_cnf(json=str(json_path), desc=desc, dsconf=dsconf,
                          slice_size=slice_size, run_as=run_as, confirm=confirm, **kw)


def tick(run_as, campaign_id, confirm) -> dict:
    tools = _import("prodtools_mcp_write.tools")
    return tools.run_submissions(run_as=run_as, campaign_id=campaign_id, confirm=confirm)


def push_file_available() -> bool:
    """Whether this prodtools exposes a push_file tool. The boundary probe:
    make_beamfile asks before it reads a dataset or builds anything, so a
    publish=True that cannot possibly succeed costs a second, not hours."""
    tools = _import("prodtools_mcp_write.tools")
    return getattr(tools, "push_file", None) is not None


def push_file(path, location, parents, run_as, confirm) -> dict:
    tools = _import("prodtools_mcp_write.tools")
    fn = getattr(tools, "push_file", None)
    if fn is None:
        raise BridgeError("this prodtools has no push_file tool; make_beamfile works with "
                          "publish=False only until prodtools-write gains push_file")
    return fn(path=str(path), location=location, parents=list(parents),
              run_as=run_as, confirm=confirm)


def campaign_status(campaign_id, mine) -> dict:
    status = _import("prodtools_mcp.tools.status")
    return status.campaign_status(campaign_id=campaign_id, mine=mine)


def cnf_exists(cnf_name) -> bool:
    sw = _import("utils.samweb_wrapper")
    return bool(sw.locate_file(cnf_name))


def dataset_files(dataset, location) -> list[dict]:
    sw = _import("utils.samweb_wrapper")
    fr = _import("utils.file_resolver")
    jc = _import("utils.job_common")
    root = fr.dataset_dir(dataset, location)
    if not root:
        raise BridgeError(f"unknown dataset location {location!r} for {dataset}")
    sizes = sw.file_sizes_in_dataset(dataset)
    out = []
    for name in sizes:
        n = jc.Mu2eName.parse(name)
        if not n.sequencer.isdigit():
            raise BridgeError(f"{name}: sequencer {n.sequencer!r} is not a plain job index; "
                              f"beamkit reads only %08d-indexed g4bl outputs")
        out.append({"name": name, "index": int(n.sequencer), "size": sizes[name],
                    "path": f"{root}/{n.relpathname()}"})
    return sorted(out, key=lambda f: f["index"])


def prodtools_info() -> dict:
    runner = _import("prodtools_mcp_write.runner")
    root = runner.REPO_ROOT
    try:
        proc = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], capture_output=True,
                              text=True, stdin=subprocess.DEVNULL)
        commit = proc.stdout.strip() if proc.returncode == 0 else None
    except OSError:
        commit = None
    return {"root": root, "commit": commit, "dev_dir": os.environ.get("BEAMKIT_PRODTOOLS_DIR") or None}
