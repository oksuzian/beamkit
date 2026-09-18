"""The ONLY module that talks to prodtools, as an MCP client of the two
prodtools servers (prodtools-write, prodtools) spawned from
$BEAMKIT_PRODTOOLS_ROOT/mcp/scripts/. beamkit's interpreter imports no
prodtools code and needs none of its environment; the launchers set that
up inside the children. Children start on the first Fermilab call, live
until beamkit exits, and are respawned if they die. Every failure is a
BridgeError. Reads one environment variable, the root."""
import os
import threading
from pathlib import Path

from beamkit import BeamkitError, naming
from beamkit.decks import DeckError, _git
from beamkit.mcpclient import McpClientError, McpToolError, StdioServer

ROOT_VAR = "BEAMKIT_PRODTOOLS_ROOT"
ROOT_DEFAULT = "/cvmfs/mu2e.opensciencegrid.org/bin/prodtools/current"
LAUNCHERS = {"write": "mcp/scripts/start_write_mcp.sh", "read": "mcp/scripts/start_mcp.sh"}

# tool -> (child, arguments always sent, arguments sent only when set);
# the contract test checks these against the real servers' schemas
CALLS = {
    "push_cnf": ("write", frozenset({"json", "desc", "dsconf", "slice_size", "run_as", "confirm"}),
                 frozenset({"prodtools_dir"})),
    "run_submissions": ("write", frozenset({"run_as", "campaign_id", "confirm"}), frozenset()),
    "push_file": ("write", frozenset({"path", "location", "parents", "run_as", "confirm"}), frozenset()),
    "campaign_status": ("read", frozenset({"campaign_id", "mine"}), frozenset()),
    "list_campaigns": ("read", frozenset({"mine"}), frozenset()),
    "locate_file": ("read", frozenset({"name"}), frozenset()),
    "dataset_files": ("read", frozenset({"dataset", "location"}), frozenset()),
}
NEEDS = {"locate_file": "the prodtools release that carries locate_file and dataset_files (Mu2e/prodtools PR "
                        "'mcp: locate_file and dataset_files read-only tools', 2026-09)",
         "dataset_files": "the prodtools release that carries locate_file and dataset_files (Mu2e/prodtools PR "
                          "'mcp: locate_file and dataset_files read-only tools', 2026-09)"}

_HINT = (f"prodtools is not reachable: set {ROOT_VAR} to a prodtools tree that has "
         f"{LAUNCHERS['write']} and {LAUNCHERS['read']} (the cvmfs release, or a checkout after "
         f"`bash mcp/scripts/install.sh`)")


class BridgeError(BeamkitError):
    pass


def _need(out, key, tool):
    """Read `key` out of a tool's result dict, or raise BridgeError: a
    result-shape drift between beamkit and the far end's prodtools must
    surface as BridgeError, never a bare KeyError."""
    if not isinstance(out, dict) or key not in out:
        raise BridgeError(f"{tool}: prodtools returned no {key!r} in its result ({str(out)[:200]}); "
                          f"the prodtools at {prodtools_root()} may be older or newer than this beamkit")
    return out[key]


# --- where prodtools is

def prodtools_root() -> str:
    return os.environ.get(ROOT_VAR) or ROOT_DEFAULT


def launcher(kind) -> Path:
    return Path(prodtools_root()) / LAUNCHERS[kind]


def availability() -> tuple:
    """(available, detail) without spawning anything: both launchers must
    exist and be executable."""
    root = prodtools_root()
    missing = [str(launcher(k)) for k in LAUNCHERS if not os.access(launcher(k), os.X_OK)]
    if missing:
        return False, f"{_HINT}; missing: {', '.join(missing)}"
    return True, f"prodtools at {root}"


def prodtools_info() -> dict:
    root = prodtools_root()
    try:
        commit = _git("rev-parse", "HEAD", cwd=root)
    except (DeckError, OSError):
        commit = None       # a cvmfs release is not a git checkout
    return {"root": root, "commit": commit}


# --- the children

_servers = {}
_lock = threading.Lock()


def _server(kind) -> StdioServer:
    with _lock:
        s = _servers.get(kind)
        if s is None:
            ok, detail = availability()
            if not ok:
                raise BridgeError(detail)
            s = _servers[kind] = StdioServer(f"prodtools-{kind}", str(launcher(kind)))
        return s


def reset() -> None:
    """Close both children (tests, and a root change)."""
    with _lock:
        for s in _servers.values():
            s.close()
        _servers.clear()


def _started(kind) -> StdioServer:
    s = _server(kind)
    try:
        s.start()
    except McpClientError as e:
        raise BridgeError(str(e)) from e
    return s


def _call(tool, **args) -> dict:
    kind = CALLS[tool][0]
    s = _started(kind)
    if not s.has_tool(tool):
        need = NEEDS.get(tool)
        raise BridgeError(f"the prodtools at {prodtools_root()} has no {tool!r} tool"
                          + (f"; it needs {need}" if need else ""))
    try:
        out = s.call(tool, **args)
    except McpToolError as e:
        raise BridgeError(e.message) from e
    except McpClientError as e:
        raise BridgeError(str(e)) from e
    err = out.get("error") if isinstance(out, dict) else None
    if isinstance(err, dict):           # the read server's safe_tool envelope
        msg = f"{err.get('message', 'prodtools error')} ({err.get('kind', 'unknown')})"
        remedy = err.get("remedy")
        raise BridgeError(f"{msg}; {remedy}" if remedy else msg)
    return out


# --- the nine calls beamkit makes

def push_cnf(json_path, desc, dsconf, slice_size, run_as, confirm, prodtools_dir=None) -> dict:
    """prodtools_dir names a dev checkout to ship to the workers; None means
    the cvmfs release and the keyword is not sent at all."""
    kw = {"prodtools_dir": prodtools_dir} if prodtools_dir else {}
    return _call("push_cnf", json=str(json_path), desc=desc, dsconf=dsconf,
                 slice_size=slice_size, run_as=run_as, confirm=confirm, **kw)


def tick(run_as, campaign_id, confirm) -> dict:
    return _call("run_submissions", run_as=run_as, campaign_id=campaign_id, confirm=confirm)


def push_file_available() -> bool:
    """Whether this prodtools exposes a push_file tool. The boundary probe:
    make_beamfile asks before it reads a dataset or builds anything, so a
    publish=True that cannot possibly succeed costs a second, not hours."""
    return _started("write").has_tool("push_file")


def push_file(path, location, parents, run_as, confirm) -> dict:
    return _call("push_file", path=str(path), location=location, parents=list(parents),
                 run_as=run_as, confirm=confirm)


def campaign_status(campaign_id, mine) -> dict:
    return _call("campaign_status", campaign_id=campaign_id, mine=mine)


def campaigns(mine) -> list:
    """Every campaign in the caller's ledger (personal for mine=True,
    production otherwise) with its state. Ledger only, no network."""
    out = _call("list_campaigns", mine=mine)
    return list(_need(out, "campaigns", "list_campaigns"))


def cnf_exists(cnf_name) -> bool:
    out = _call("locate_file", name=cnf_name)
    return bool(_need(out, "exists", "locate_file"))


def dataset_files(dataset, location) -> list:
    """prodtools lists the files with sizes and /pnfs paths; the job index
    is beamkit's reading of the sequencer, and only a plain %08d one."""
    parts = dataset.split(".")
    if len(parts) != 5 or parts[0] != "nts":
        raise BridgeError(f"{dataset!r} is not an nts dataset name (nts.<owner>.<desc>.<dsconf>.root)")
    _, owner, desc, dsconf, _ = parts
    out = _call("dataset_files", dataset=dataset, location=location)
    files = []
    for f in _need(out, "files", "dataset_files"):
        name = _need(f, "name", "dataset_files")
        index = naming.nts_index(name, owner, desc, dsconf)
        if index is None:
            raise BridgeError(f"{name}: its sequencer is not a plain job index; "
                              f"beamkit reads only %08d-indexed g4bl outputs")
        files.append({"name": name, "index": index, "size": int(_need(f, "size", "dataset_files")),
                      "path": _need(f, "path", "dataset_files")})
    return sorted(files, key=lambda f: f["index"])
