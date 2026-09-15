# prodtools over MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** beamkit stops importing prodtools; its Fermilab backend drives the two prodtools MCP servers as a stdio MCP client, so beamkit is pure Python everywhere and the beamkit cvmfs release is retired.

**Architecture:** `src/beamkit/mcpclient.py` is a synchronous handle on one MCP server spawned over stdio (private asyncio loop in a daemon thread). `src/beamkit/bridge.py` keeps its nine public functions and signatures but implements them as calls on two lazily spawned children, the prodtools write and read servers launched from `$BEAMKIT_PRODTOOLS_ROOT/mcp/scripts/`. Two read-only tools (`locate_file`, `dataset_files`) are added to prodtools' read server so the bridge needs nothing from prodtools' Python internals.

**Tech Stack:** Python 3.10+, `mcp>=1.2,<2` (client: `mcp.client.stdio.stdio_client`, `mcp.ClientSession`; server: `mcp.server.fastmcp.FastMCP`), pytest. prodtools side: `test/test_unit.py` (unittest, run with the system `python3`).

**Spec:** `docs/specs/2026-09-15-prodtools-over-mcp-design.md` (beamkit repo). Read it first; every task below argues from it.

## Global Constraints

- Two repositories. beamkit: `/exp/mu2e/app/users/oksuzian/beamkit`, branch `v1`. prodtools: `/exp/mu2e/app/users/oksuzian/muse_050125/prodtools`; Task 1 works on a new branch off the `mu2e` remote's `main` (`git fetch mu2e main && git checkout -b mcp-locate-and-dataset-files mu2e/main`); its PR targets `Mu2e/prodtools`.
- beamkit tests: `cd /exp/mu2e/app/users/oksuzian/beamkit && env -u PYTHONPATH .venv/bin/python -m pytest -q -p no:cacheprovider`. Never run pytest with the ops PYTHONPATH set.
- prodtools tests: `cd /exp/mu2e/app/users/oksuzian/muse_050125/prodtools && python3 -m pytest test/test_unit.py -q -p no:cacheprovider -k Mcp`.
- `bridge.py` public names and signatures stay exactly: `push_cnf(json_path, desc, dsconf, slice_size, run_as, confirm, prodtools_dir=None)`, `tick(run_as, campaign_id, confirm)`, `push_file_available()`, `push_file(path, location, parents, run_as, confirm)`, `campaign_status(campaign_id, mine)`, `campaigns(mine)`, `cnf_exists(name)`, `dataset_files(dataset, location)`, `prodtools_info()`, class `BridgeError(BeamkitError)`. `tools.py`, `publishing.py`, `identity.py` are not edited except where a task says so.
- Environment variables: `BEAMKIT_PRODTOOLS_ROOT` (default `/cvmfs/mu2e.opensciencegrid.org/bin/prodtools/current`), `BEAMKIT_PRODTOOLS_START_TIMEOUT` (default `180`, seconds), `BEAMKIT_PRODTOOLS_DIR` (unchanged, read only by `identity.py`).
- The child processes get beamkit's full `os.environ`. The `mcp` client's default environment is a short allowlist; always pass `env` explicitly.
- No per-call timeout on tool calls. Start and initialize are bounded by the start timeout.
- Download/upload caps, NERSC code, records, naming: untouched.
- Commit messages end with the two trailer lines given in the session reminder (`Co-Authored-By` and `Claude-Session`). Never `git push`, open a PR, or tag without the user's explicit go; Task 7 says where to ask.
- `mcp` package: `CallToolResult` has `content`, `structuredContent`, `isError`. A FastMCP tool that raises comes back as `isError: true` with text `Error executing tool <name>: <message>`. prodtools' READ server wraps tools in `safe_tool`, so its failures are ordinary results shaped `{"error": {"kind", "message", "remedy"}}`; its WRITE server registers raw functions, so its failures are `isError` results.

---

### Task 1: `locate_file` and `dataset_files` in prodtools' read-only server

**Files:**
- Modify: `mcp/src/prodtools_mcp/tools/discovery.py` (append after `dataset_details`)
- Modify: `mcp/src/prodtools_mcp/server.py:85-93` (TOOL_FUNCTIONS) and `:190-194` (after the `dataset_details` registration)
- Modify: `test/test_unit.py:10785` (tool count 6 → 8) and append two test classes after `TestMcpDatasetDetails` (ends before line 10540 `class TestMcpLineage`)
- Modify: `CLAUDE.md:57-60` and `mcp/README.md:9` tool lists
- Repo: `/exp/mu2e/app/users/oksuzian/muse_050125/prodtools`

**Interfaces:**
- Consumes: `prodtools_mcp.adapters.ToolError(kind, message, remedy)`, `classify_catalog_error(exc, message)`, `safe_tool`; `utils.samweb_wrapper.locate_file(filename) -> str` ("" when unknown), `utils.samweb_wrapper.file_sizes_in_dataset(dataset) -> {name: size}`, `utils.file_resolver.dataset_dir(dsname, location) -> str` ("" for an unknown location), `utils.job_common.Mu2eName.parse(name).relpathname()`.
- Produces (MCP tools on the read server): `locate_file(name: str) -> {"name": str, "exists": bool, "locations": [str]}`; `dataset_files(dataset: str, location: str) -> {"dataset", "location", "root", "n_files", "total_size", "files": [{"name", "size", "path"}]}` sorted by name; unknown location → `ToolError('invalid_argument', ...)`, which `safe_tool` turns into `{"error": {...}}`.

- [ ] **Step 1: Branch off Mu2e main**

```bash
cd /exp/mu2e/app/users/oksuzian/muse_050125/prodtools
git stash list   # informational; the dirty files (.mcp.json, wiki/*) are untouched by this task
git fetch mu2e main
git checkout -b mcp-locate-and-dataset-files mu2e/main
```

If checkout refuses because of the modified `.mcp.json`/`wiki` files, run `git stash push .mcp.json wiki/index.md wiki/log.md`, checkout, then `git stash pop`.

- [ ] **Step 2: Write the failing tests**

Append to `test/test_unit.py` right before `class TestMcpLineage(unittest.TestCase):`:

```python
class TestMcpLocateFile(unittest.TestCase):
    def test_known_file_exists_with_its_location(self):
        from prodtools_mcp.tools import discovery
        res = discovery.locate_file('cnf.u.T.e470313.0.tar',
                                    locate_fn=lambda n: 'enstore:/pnfs/x')
        self.assertEqual(res, {'name': 'cnf.u.T.e470313.0.tar', 'exists': True,
                               'locations': ['enstore:/pnfs/x']})

    def test_unknown_file_is_not_an_error(self):
        from prodtools_mcp.tools import discovery
        res = discovery.locate_file('cnf.u.T.e470313-001.0.tar', locate_fn=lambda n: '')
        self.assertEqual(res, {'name': 'cnf.u.T.e470313-001.0.tar', 'exists': False,
                               'locations': []})

    def test_catalog_failure_is_classified(self):
        from prodtools_mcp.tools import discovery
        from prodtools_mcp.adapters import ToolError

        def boom(n):
            raise RuntimeError('SAM down')

        with self.assertRaises(ToolError) as ctx:
            discovery.locate_file('x.y.z.w.0.tar', locate_fn=boom)
        self.assertEqual(ctx.exception.kind, 'catalog_unavailable')


class TestMcpDatasetFiles(unittest.TestCase):
    SIZES = {'nts.u.T.e470313.00000002.root': 20, 'nts.u.T.e470313.00000000.root': 10}

    def test_files_with_sizes_and_hashed_paths_sorted_by_name(self):
        from prodtools_mcp.tools import discovery
        from utils.job_common import Mu2eName
        res = discovery.dataset_files(
            'nts.u.T.e470313.root', 'scratch',
            sizes_fn=lambda ds: dict(self.SIZES),
            dataset_dir_fn=lambda ds, loc: f'/pnfs/{loc}/{ds}')
        self.assertEqual(res['root'], '/pnfs/scratch/nts.u.T.e470313.root')
        self.assertEqual(res['n_files'], 2)
        self.assertEqual(res['total_size'], 30)
        self.assertEqual([f['name'] for f in res['files']],
                         ['nts.u.T.e470313.00000000.root', 'nts.u.T.e470313.00000002.root'])
        rel = Mu2eName.parse('nts.u.T.e470313.00000000.root').relpathname()
        self.assertEqual(res['files'][0],
                         {'name': 'nts.u.T.e470313.00000000.root', 'size': 10,
                          'path': f'/pnfs/scratch/nts.u.T.e470313.root/{rel}'})

    def test_unknown_location_is_invalid_argument(self):
        from prodtools_mcp.tools import discovery
        from prodtools_mcp.adapters import ToolError
        with self.assertRaises(ToolError) as ctx:
            discovery.dataset_files('nts.u.T.e470313.root', 'resilient',
                                    sizes_fn=lambda ds: {},
                                    dataset_dir_fn=lambda ds, loc: '')
        self.assertEqual(ctx.exception.kind, 'invalid_argument')
        self.assertIn('resilient', ctx.exception.message)

    def test_listing_failure_is_classified(self):
        from prodtools_mcp.tools import discovery
        from prodtools_mcp.adapters import ToolError

        def boom(ds):
            raise RuntimeError('SAM down')

        with self.assertRaises(ToolError) as ctx:
            discovery.dataset_files('nts.u.T.e470313.root', 'scratch',
                                    sizes_fn=boom,
                                    dataset_dir_fn=lambda ds, loc: '/pnfs/x')
        self.assertEqual(ctx.exception.kind, 'catalog_unavailable')

    def test_registered_on_the_read_server(self):
        from prodtools_mcp import server
        self.assertIn('locate_file', server.TOOL_FUNCTIONS)
        self.assertIn('dataset_files', server.TOOL_FUNCTIONS)
        self.assertIn('locate_file', server.TOOL_NAMES)
        self.assertIn('dataset_files', server.TOOL_NAMES)
```

Then change the existing count assertion at `test/test_unit.py:10785` from `self.assertEqual(len(TOOL_NAMES), 6)` to `self.assertEqual(len(TOOL_NAMES), 8)`.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd /exp/mu2e/app/users/oksuzian/muse_050125/prodtools && python3 -m pytest test/test_unit.py -q -p no:cacheprovider -k "McpLocateFile or McpDatasetFiles or McpServerInfo or McpReadServer"`
Expected: FAIL with `AttributeError: module 'prodtools_mcp.tools.discovery' has no attribute 'locate_file'` and the count assertion `6 != 8` (the class holding line 10785 may be named differently; run `-k Mcp` if the filter misses it and read the failures).

- [ ] **Step 4: Implement the two tools**

Append to `mcp/src/prodtools_mcp/tools/discovery.py`:

```python
def _default_locate_fn(name):
    from utils.samweb_wrapper import locate_file
    return locate_file(name)


def locate_file(name, locate_fn=None):
    """Whether SAM knows `name`, and its first location. An unknown name
    is `exists: false`, not an error: this is the existence probe that
    json2jobdef's pushout path uses (samweb_wrapper.locate_file returns
    '' for it). Every other SAM failure is classified."""
    try:
        loc = (locate_fn or _default_locate_fn)(name)
    except Exception as exc:
        raise classify_catalog_error(
            exc, f'locate failed for {name}: {exc}') from exc
    return {'name': name, 'exists': bool(loc),
            'locations': [loc] if loc else []}


def _default_sizes_fn(dataset):
    from utils.samweb_wrapper import file_sizes_in_dataset
    return file_sizes_in_dataset(dataset)


def _default_dataset_dir_fn(dataset, location):
    from utils.file_resolver import dataset_dir
    return dataset_dir(dataset, location)


def dataset_files(dataset, location, sizes_fn=None, dataset_dir_fn=None):
    """Every file of `dataset` with its size and the absolute /pnfs path
    it has at `location` (scratch, disk or tape), sorted by name. The
    path is where the file lives by the location tables
    (file_resolver.dataset_dir + Mu2eName.relpathname); presence on that
    location is not checked here."""
    try:
        root = (dataset_dir_fn or _default_dataset_dir_fn)(dataset, location)
    except ValueError as exc:
        raise ToolError('invalid_argument', f'{dataset}: {exc}',
                        'Pass a Mu2e dataset name, tier.owner.desc.dsconf.ext.') from exc
    if not root:
        raise ToolError('invalid_argument',
                        f'unknown dataset location {location!r} for {dataset}',
                        'Use one of scratch, disk, tape.')
    try:
        sizes = (sizes_fn or _default_sizes_fn)(dataset)
    except Exception as exc:
        raise classify_catalog_error(
            exc, f'file listing failed for {dataset}: {exc}') from exc
    files = [{'name': n, 'size': int(s),
              'path': f'{root}/{Mu2eName.parse(n).relpathname()}'}
             for n, s in sorted(sizes.items())]
    return {'dataset': dataset, 'location': location, 'root': root,
            'n_files': len(files),
            'total_size': sum(f['size'] for f in files), 'files': files}
```

In `mcp/src/prodtools_mcp/server.py`, extend `TOOL_FUNCTIONS`:

```python
TOOL_FUNCTIONS = {
    'campaign_status': safe_tool(status.campaign_status),
    'list_campaigns': safe_tool(status.list_campaigns),
    'find_datasets': safe_tool(discovery.find_datasets),
    'dataset_details': safe_tool(discovery.dataset_details),
    'locate_file': safe_tool(discovery.locate_file),
    'dataset_files': safe_tool(discovery.dataset_files),
    'trace_provenance': safe_tool(lineage.trace_provenance),
}
```

and register the two tools inside `create_mcp_server()`, right after the `dataset_details` registration:

```python
    @mcp.tool(description='Whether SAM knows a file, and its first '
                          'location. An unknown name is exists=false, '
                          'not an error.')
    def locate_file(name: str) -> dict:
        return TOOL_FUNCTIONS['locate_file'](name=name)

    @mcp.tool(description='Every file of a dataset with its size and '
                          'its /pnfs path at a location (scratch, disk '
                          'or tape), sorted by name.')
    def dataset_files(dataset: str, location: str) -> dict:
        return TOOL_FUNCTIONS['dataset_files'](dataset=dataset,
                                               location=location)
```

Add two lines to the `WHAT IT ANSWERS` block of `INSTRUCTIONS` in the same file:

```
- "Does SAM know this file?"  -> locate_file(name="cnf.mu2e....0.tar")
- "Where are its files?"      -> dataset_files(dataset="nts....root", location="scratch")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m pytest test/test_unit.py -q -p no:cacheprovider -k Mcp`
Expected: all pass, including the AST test that checks Optional annotations (both new tools take plain `str`).

- [ ] **Step 6: Update the tool lists in the docs**

`CLAUDE.md:57-60`: the read-only tool list becomes `campaign_status`, `list_campaigns`, `find_datasets`, `dataset_details`, `locate_file`, `dataset_files`, `trace_provenance`, `get_server_info`. `mcp/README.md:9`: same list.

- [ ] **Step 7: Commit**

```bash
git add mcp/src/prodtools_mcp/tools/discovery.py mcp/src/prodtools_mcp/server.py test/test_unit.py CLAUDE.md mcp/README.md
git commit -m "mcp: locate_file and dataset_files read-only tools

beamkit is moving from importing prodtools in-process to calling the
MCP servers; these two cover the last helpers it took from utils
(samweb locate, dataset file listing with /pnfs paths).

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0138sWPyeSCDFbbn2cB9JBDy"
```

Do not push. Task 7 asks the user before the PR.

---

### Task 2: `mcpclient.StdioServer` and the fake prodtools server

**Files:**
- Create: `src/beamkit/mcpclient.py`
- Create: `tests/fake_prodtools_mcp.py`
- Create: `tests/test_mcpclient.py`
- Repo: `/exp/mu2e/app/users/oksuzian/beamkit`

**Interfaces:**
- Consumes: `beamkit.BeamkitError`; `mcp.ClientSession`, `mcp.StdioServerParameters`, `mcp.client.stdio.stdio_client(params, errlog=<TextIO>)`.
- Produces: `class McpClientError(BeamkitError)`; `class McpToolError(McpClientError)` with attributes `server`, `tool`, `message`; `class StdioServer(name, command, args=(), env=None, start_timeout=None)` with `start()`, `started` (bool property), `call(tool, **args) -> dict`, `has_tool(name) -> bool`, `tools` (dict name → `mcp.types.Tool`), `stderr_tail` (list of the last 20 stderr lines), `close()`. Module constants `START_TIMEOUT_VAR = "BEAMKIT_PRODTOOLS_START_TIMEOUT"`, `START_TIMEOUT_DEFAULT = 180.0`, `STDERR_TAIL = 20`.
- The fake server, run as `python -m tests.fake_prodtools_mcp write|read`, is the process every later test spawns. Its environment knobs: `FAKE_PRODTOOLS_CALLS=<file>` appends one JSON line `{"tool", "args"}` per call; `FAKE_PRODTOOLS_FAIL=<tool>` makes that tool fail (raise on the write role, error envelope on the read role); `FAKE_PRODTOOLS_DIE=<tool>` exits the process with status 7 inside that call; `FAKE_PRODTOOLS_OMIT=<tool,...>` leaves those tools unregistered; `FAKE_PRODTOOLS_NO_START=1` prints `fake prodtools: refusing to start` to stderr and exits 3 before serving; `FAKE_PRODTOOLS_HANG_START=1` sleeps 600 s before serving; `FAKE_PRODTOOLS_FILES=<json list of [name, size]>` sets what `dataset_files` lists; `FAKE_PRODTOOLS_MARK=<text>` is echoed by the `env_probe` tool.

- [ ] **Step 1: Write the fake prodtools server**

`tests/fake_prodtools_mcp.py`:

```python
"""A stand-in for the two prodtools MCP servers, run as a REAL stdio child:
`python -m tests.fake_prodtools_mcp write` or `... read`. Same tool names
and argument names as prodtools; canned answers; failures on demand via
environment variables (see docs/plans/2026-09-15-prodtools-over-mcp.md,
Task 2). The read role envelopes failures as {"error": {...}} the way
prodtools' safe_tool does; the write role raises, as prodtools-write does.
"""
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
    def wrapper(**kw):
        try:
            return fn(**kw)
        except RuntimeError as e:
            return _envelope("internal", str(e), "check the fake")
    wrapper.__name__ = fn.__name__
    wrapper.__annotations__ = dict(fn.__annotations__)
    wrapper.__doc__ = fn.__doc__
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
    return {"count": 1, "db_path": "/db", "called": {"state": state, "mine": mine},
            "campaigns": [{"id": 7, "state": "complete", "tarball": "cnf.u.T.e470313.0.tar"}]}


@_safe
def locate_file(name: str) -> dict:
    _record("locate_file", {"name": name})
    _trip("locate_file")
    exists = name.endswith("e470313.0.tar")
    return {"name": name, "exists": exists, "locations": ["enstore:/x"] if exists else []}


@_safe
def dataset_files(dataset: str, location: str) -> dict:
    _record("dataset_files", {"dataset": dataset, "location": location})
    _trip("dataset_files")
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
```

- [ ] **Step 2: Write the failing client tests**

`tests/test_mcpclient.py`:

```python
"""StdioServer against tests/fake_prodtools_mcp.py as a real child process."""
import os
import sys

import pytest

from beamkit import mcpclient
from beamkit.mcpclient import McpClientError, McpToolError, StdioServer

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fake(role, **env_extra):
    env = dict(os.environ, PYTHONPATH=REPO, **{k: str(v) for k, v in env_extra.items()})
    return StdioServer(f"fake-{role}", sys.executable, ["-m", "tests.fake_prodtools_mcp", role],
                       env=env, start_timeout=60)


@pytest.fixture
def write():
    s = fake("write")
    yield s
    s.close()


@pytest.fixture
def read():
    s = fake("read")
    yield s
    s.close()


def test_start_lists_tools_and_call_returns_the_dict(write):
    assert not write.started
    write.start()
    assert write.started and write.has_tool("push_cnf") and not write.has_tool("campaign_status")
    out = write.call("run_submissions", run_as="self", campaign_id=7, confirm=False)
    assert out == {"rc": 0, "needs_attention": False, "campaign_id": 7, "output": "tick ok"}


def test_call_starts_lazily(read):
    out = read.call("locate_file", name="cnf.u.T.e470313.0.tar")
    assert read.started and out["exists"] is True


def test_child_gets_the_parents_environment():
    s = fake("read", FAKE_PRODTOOLS_MARK="hello-child")
    try:
        assert s.call("env_probe") == {"mark": "hello-child"}
    finally:
        s.close()


def test_raising_tool_is_a_tool_error_with_the_servers_message():
    s = fake("write", FAKE_PRODTOOLS_FAIL="push_cnf")
    try:
        with pytest.raises(McpToolError) as ei:
            s.call("push_cnf", json="/tmp/e.json", desc="T", dsconf="e470313", slice_size=1,
                   run_as="self", confirm=False)
        assert ei.value.tool == "push_cnf" and ei.value.server == "fake-write"
        assert str(ei.value) == "push_cnf refused by the fake; remedy: try the other thing"
        assert not str(ei.value).startswith("Error executing tool")
    finally:
        s.close()


def test_unknown_tool_is_a_client_error(read):
    with pytest.raises(McpClientError, match="no tool 'nope'"):
        read.call("nope")


def test_child_death_closes_and_next_call_respawns():
    s = fake("read", FAKE_PRODTOOLS_DIE="list_campaigns")
    try:
        with pytest.raises(McpClientError, match="exited during list_campaigns"):
            s.call("list_campaigns", mine=True)
        assert not s.started
        assert s.call("locate_file", name="x.y.z.w.0.tar")["exists"] is False
        assert s.started
    finally:
        s.close()


def test_no_start_reports_the_childs_stderr():
    s = fake("read", FAKE_PRODTOOLS_NO_START="1")
    try:
        with pytest.raises(McpClientError, match="did not start.*refusing to start"):
            s.start()
        assert not s.started and "fake prodtools: refusing to start" in s.stderr_tail
    finally:
        s.close()


def test_start_timeout():
    s = fake("read", FAKE_PRODTOOLS_HANG_START="1")
    s.start_timeout = 2
    try:
        with pytest.raises(McpClientError, match="did not start within 2 s"):
            s.start()
        assert not s.started
    finally:
        s.close()


def test_start_timeout_default_and_env(monkeypatch):
    assert StdioServer("x", "true").start_timeout == mcpclient.START_TIMEOUT_DEFAULT
    monkeypatch.setenv(mcpclient.START_TIMEOUT_VAR, "7")
    assert StdioServer("x", "true").start_timeout == 7.0


def test_close_is_idempotent_and_restartable(read):
    read.start()
    read.close()
    read.close()
    assert not read.started
    assert read.call("get_server_info")["name"] == "prodtools"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd /exp/mu2e/app/users/oksuzian/beamkit && env -u PYTHONPATH .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_mcpclient.py`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'beamkit.mcpclient'`.

- [ ] **Step 4: Implement `mcpclient.py`**

`src/beamkit/mcpclient.py`:

```python
"""A synchronous handle on one MCP server spawned over stdio.

beamkit's tools are synchronous functions that FastMCP runs on its own
event loop, so a nested asyncio.run is impossible; the client session
lives on a private loop in a daemon thread and every call is marshalled
there. The whole session lifetime runs in ONE task on that loop (anyio
cancel scopes must be exited by the task that entered them), so start()
schedules a serve task that opens the transport and the session, signals
ready, and waits for stop.

Knows nothing about prodtools; bridge.py owns that."""
import asyncio
import atexit
import concurrent.futures
import json
import os
import sys
import threading
from collections import deque

from beamkit import BeamkitError

START_TIMEOUT_VAR = "BEAMKIT_PRODTOOLS_START_TIMEOUT"
START_TIMEOUT_DEFAULT = 180.0
STDERR_TAIL = 20


class McpClientError(BeamkitError):
    pass


class McpToolError(McpClientError):
    """The server answered isError: message is the server's own text."""

    def __init__(self, server, tool, message):
        super().__init__(message)
        self.server, self.tool, self.message = server, tool, message


def _strip_prefix(text, tool):
    prefix = f"Error executing tool {tool}: "
    return text[len(prefix):] if text.startswith(prefix) else text


class StdioServer:
    def __init__(self, name, command, args=(), env=None, start_timeout=None):
        self.name = name
        self.command = command
        self.args = list(args)
        # the mcp client's default env is a short allowlist; beamkit's
        # children need the caller's whole environment (tokens, ledger
        # path, BEAMKIT_PRODTOOLS_DIR)
        self.env = dict(os.environ) if env is None else dict(env)
        self.start_timeout = (float(os.environ.get(START_TIMEOUT_VAR, START_TIMEOUT_DEFAULT))
                              if start_timeout is None else float(start_timeout))
        self._stderr_tail = deque(maxlen=STDERR_TAIL)
        self._pump = None
        self._lock = threading.RLock()
        self._loop = None
        self._thread = None
        self._serve_fut = None
        self._stop = None
        self._session = None
        self.tools = {}
        atexit.register(self.close)

    # --- state
    @property
    def started(self):
        return self._session is not None

    @property
    def stderr_tail(self):
        return list(self._stderr_tail)

    def has_tool(self, name):
        return name in self.tools

    # --- lifecycle
    def start(self):
        with self._lock:
            if self._session is not None:
                return
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(target=self._loop.run_forever,
                                            name=f"mcp-{self.name}", daemon=True)
            self._thread.start()
            ready = concurrent.futures.Future()
            self._serve_fut = asyncio.run_coroutine_threadsafe(self._serve(ready), self._loop)
            try:
                ready.result(self.start_timeout)
            except concurrent.futures.TimeoutError:
                self._teardown()
                raise McpClientError(f"{self.name} server did not start within "
                                     f"{self.start_timeout:.0f} s ({self.command})") from None
            except Exception as e:
                self._teardown()
                if self._pump is not None:
                    self._pump.join(2)      # let the child's last stderr lines land
                tail = " | ".join(self._stderr_tail)
                raise McpClientError(f"{self.name} server did not start ({self.command}): "
                                     f"{type(e).__name__}: {e}; child stderr: {tail}") from e

    async def _serve(self, ready):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        self._stop = asyncio.Event()
        params = StdioServerParameters(command=self.command, args=self.args, env=self.env)
        errlog = self._stderr_tee()
        try:
            async with stdio_client(params, errlog=errlog) as (read, write):
                errlog.close()      # the child holds its own copy; the pump ends when it exits
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listing = await session.list_tools()
                    self.tools = {t.name: t for t in listing.tools}
                    self._session = session
                    ready.set_result(None)
                    await self._stop.wait()
        except BaseException as e:       # noqa: BLE001 - reported through ready or swallowed on stop
            if not ready.done():
                ready.set_exception(e)
        finally:
            if not errlog.closed:
                errlog.close()
            self._session = None
            self.tools = {}

    def _stderr_tee(self):
        """A pipe the child writes to; a pump thread keeps the last lines
        and forwards everything to our stderr."""
        r, w = os.pipe()

        def pump():
            with os.fdopen(r, "rb", buffering=0) as fh:
                for raw in iter(fh.readline, b""):
                    line = raw.decode("utf-8", "replace")
                    self._stderr_tail.append(line.rstrip("\n"))
                    sys.stderr.write(line)
                    sys.stderr.flush()
        self._pump = threading.Thread(target=pump, name=f"mcp-{self.name}-stderr", daemon=True)
        self._pump.start()
        return os.fdopen(w, "w")

    def close(self):
        with self._lock:
            if self._loop is None:
                return
            if self._stop is not None and self._serve_fut is not None and not self._serve_fut.done():
                self._loop.call_soon_threadsafe(self._stop.set)
                try:
                    self._serve_fut.result(10)
                except Exception:   # noqa: BLE001 - shutting down regardless
                    pass
            self._teardown()

    def _teardown(self):
        loop, thread = self._loop, self._thread
        self._session = None
        self.tools = {}
        if self._serve_fut is not None and not self._serve_fut.done():
            self._serve_fut.cancel()
            try:        # give the cancelled serve task a few loop turns to terminate the child
                asyncio.run_coroutine_threadsafe(asyncio.sleep(0.5), loop).result(3)
            except Exception:   # noqa: BLE001
                pass
        self._serve_fut = self._stop = None
        self._loop = self._thread = None
        if loop is not None:
            loop.call_soon_threadsafe(loop.stop)
            if thread is not None:
                thread.join(5)
            if not loop.is_running():
                loop.close()

    # --- calls
    def call(self, tool, **args):
        with self._lock:
            if self._session is None:
                self.start()
            if tool not in self.tools:
                raise McpClientError(f"{self.name} server has no tool {tool!r}; it has "
                                     f"{', '.join(sorted(self.tools)) or 'none'}")
            fut = asyncio.run_coroutine_threadsafe(self._session.call_tool(tool, args), self._loop)
            try:
                res = fut.result()
            except Exception as e:      # noqa: BLE001 - the transport is gone, whatever the type
                self.close()
                raise McpClientError(f"{self.name} server exited during {tool}: "
                                     f"{type(e).__name__}: {e}") from e
        text = "".join(c.text for c in res.content if getattr(c, "type", None) == "text")
        if res.isError:
            raise McpToolError(self.name, tool, _strip_prefix(text, tool))
        if res.structuredContent is not None:
            return res.structuredContent
        try:
            return json.loads(text)
        except ValueError as e:
            raise McpClientError(f"{self.name} {tool}: result is not JSON: {text[:200]!r}") from e
```

If `test_child_death_closes_and_next_call_respawns` hangs instead of raising, the transport reported the death as an `Exception` object on the read stream rather than by raising; in that case `call_tool` raises `McpError('Connection closed')`, which the `except Exception` above covers. If it still hangs, wrap `fut.result()` in a loop that also polls `self._serve_fut.done()` every second and raises the same `McpClientError` when the serve task has ended.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_mcpclient.py -x`
Expected: 10 passed. Whole suite afterwards: `env -u PYTHONPATH .venv/bin/python -m pytest -q -p no:cacheprovider`, expected 383 passed, 2 skipped (373 before plus these 10).

- [ ] **Step 6: Commit**

```bash
git add src/beamkit/mcpclient.py tests/fake_prodtools_mcp.py tests/test_mcpclient.py
git commit -m "feat: mcpclient.StdioServer, a sync handle on a stdio MCP server

Private loop in a daemon thread, one serve task for the session's whole
life, stderr tail for start failures, respawn after a child dies. The
fake prodtools server the tests spawn is a real FastMCP child.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0138sWPyeSCDFbbn2cB9JBDy"
```

---

### Task 3: `bridge.py` as an MCP client

**Files:**
- Rewrite: `src/beamkit/bridge.py`
- Rewrite: `tests/test_bridge.py`
- Modify: `tests/conftest.py` (add the fixture below)
- Repo: `/exp/mu2e/app/users/oksuzian/beamkit`

**Interfaces:**
- Consumes: `beamkit.mcpclient.StdioServer`, `McpClientError`, `McpToolError` (Task 2); `beamkit.decks._git`, `DeckError`.
- Produces (new public names, besides the nine kept): `ROOT_VAR = "BEAMKIT_PRODTOOLS_ROOT"`, `ROOT_DEFAULT`, `LAUNCHERS = {"write": "mcp/scripts/start_write_mcp.sh", "read": "mcp/scripts/start_mcp.sh"}`, `prodtools_root() -> str`, `launcher(kind) -> pathlib.Path`, `availability() -> tuple[bool, str]`, `reset()` (close both children), `CALLS` (table used by the contract test in Task 4: `{tool: (kind, frozenset(always_sent), frozenset(optional))}`), `NEEDS = {"locate_file": ..., "dataset_files": ...}` naming the prodtools change that adds each tool.
- The conftest fixture `fake_prodtools_root(tmp_path, monkeypatch)` creates `<tmp>/mcp/scripts/start_write_mcp.sh` and `start_mcp.sh` that exec the fake server, sets `BEAMKIT_PRODTOOLS_ROOT`, calls `bridge.reset()` on teardown, and returns the tmp root. Later tasks reuse it.

- [ ] **Step 1: Add the fixture to `tests/conftest.py`**

Append:

```python
import os
import stat
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def fake_prodtools_root(tmp_path, monkeypatch):
    """A prodtools tree whose two MCP launchers run tests/fake_prodtools_mcp.py.
    bridge spawns exactly these paths, so the whole client path is real."""
    from beamkit import bridge
    scripts = tmp_path / "prodtools" / "mcp" / "scripts"
    scripts.mkdir(parents=True)
    for script, role in (("start_write_mcp.sh", "write"), ("start_mcp.sh", "read")):
        p = scripts / script
        p.write_text(f"#!/bin/bash\nexport PYTHONPATH={REPO}\n"
                     f"exec {sys.executable} -m tests.fake_prodtools_mcp {role}\n")
        p.chmod(p.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("BEAMKIT_PRODTOOLS_ROOT", str(tmp_path / "prodtools"))
    monkeypatch.setenv("BEAMKIT_PRODTOOLS_START_TIMEOUT", "60")
    monkeypatch.setenv("FAKE_PRODTOOLS_CALLS", str(tmp_path / "calls.jsonl"))
    for var in ("FAKE_PRODTOOLS_FAIL", "FAKE_PRODTOOLS_DIE", "FAKE_PRODTOOLS_OMIT",
                "FAKE_PRODTOOLS_NO_START", "FAKE_PRODTOOLS_HANG_START", "FAKE_PRODTOOLS_FILES"):
        monkeypatch.delenv(var, raising=False)
    bridge.reset()
    yield tmp_path / "prodtools"
    bridge.reset()
```

(`pytest` is already imported at the top of conftest; keep one import.)

- [ ] **Step 2: Write the failing bridge tests**

Replace `tests/test_bridge.py` entirely:

```python
"""bridge.py against the fake prodtools servers spawned from a fake
BEAMKIT_PRODTOOLS_ROOT (see conftest.fake_prodtools_root). Every test here
exercises the real MCP client path; only the far end is canned."""
import json
import os
import subprocess

import pytest

from beamkit import bridge


def calls(root):
    path = root.parent / "calls.jsonl"
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines()]


def test_missing_root_is_a_clear_error(monkeypatch, tmp_path):
    monkeypatch.setenv("BEAMKIT_PRODTOOLS_ROOT", str(tmp_path / "nowhere"))
    bridge.reset()
    ok, detail = bridge.availability()
    assert ok is False and "BEAMKIT_PRODTOOLS_ROOT" in detail and "start_write_mcp.sh" in detail
    with pytest.raises(bridge.BridgeError, match="BEAMKIT_PRODTOOLS_ROOT"):
        bridge.tick("self", 1, False)


def test_default_root_is_the_cvmfs_release(monkeypatch):
    monkeypatch.delenv("BEAMKIT_PRODTOOLS_ROOT", raising=False)
    assert bridge.prodtools_root() == "/cvmfs/mu2e.opensciencegrid.org/bin/prodtools/current"
    assert str(bridge.launcher("write")).endswith("/mcp/scripts/start_write_mcp.sh")
    assert str(bridge.launcher("read")).endswith("/mcp/scripts/start_mcp.sh")


def test_availability_needs_both_launchers(fake_prodtools_root):
    assert bridge.availability() == (True, f"prodtools at {fake_prodtools_root}")
    (fake_prodtools_root / "mcp" / "scripts" / "start_mcp.sh").unlink()
    ok, detail = bridge.availability()
    assert ok is False and "start_mcp.sh" in detail


def test_push_cnf_forwards_as_keywords(fake_prodtools_root, tmp_path):
    out = bridge.push_cnf(tmp_path / "entry.json", "T", "e470313", 3, "self", False)
    assert out["campaign_id"] == 7 and out["tarball"] == "cnf.u.T.e470313.0.tar"
    assert calls(fake_prodtools_root)[-1] == {
        "tool": "push_cnf", "args": {"json": str(tmp_path / "entry.json"), "desc": "T", "dsconf": "e470313",
                                     "slice_size": 3, "run_as": "self", "confirm": False}}


def test_push_cnf_forwards_an_explicit_dev_dir(fake_prodtools_root, tmp_path):
    bridge.push_cnf(tmp_path / "entry.json", "T", "e470313", 3, "self", False,
                    prodtools_dir="/exp/mu2e/app/users/u/prodtools")
    assert calls(fake_prodtools_root)[-1]["args"]["prodtools_dir"] == "/exp/mu2e/app/users/u/prodtools"


def test_push_cnf_sends_no_prodtools_dir_keyword_for_the_release(fake_prodtools_root, tmp_path, monkeypatch):
    """None means the cvmfs release: the keyword is absent, not None, and the
    environment is not consulted here -- identity reads it, once."""
    monkeypatch.setenv("BEAMKIT_PRODTOOLS_DIR", "/exp/mu2e/app/users/u/prodtools")
    bridge.push_cnf(tmp_path / "entry.json", "T", "e470313", 3, "self", False)
    assert "prodtools_dir" not in calls(fake_prodtools_root)[-1]["args"]


def test_tick_forwards(fake_prodtools_root):
    out = bridge.tick("mu2epro", 7, True)
    assert out["rc"] == 0
    assert calls(fake_prodtools_root)[-1] == {"tool": "run_submissions",
                                              "args": {"run_as": "mu2epro", "campaign_id": 7, "confirm": True}}


def test_write_failure_is_the_servers_own_message(fake_prodtools_root, monkeypatch):
    monkeypatch.setenv("FAKE_PRODTOOLS_FAIL", "run_submissions")
    bridge.reset()
    with pytest.raises(bridge.BridgeError) as ei:
        bridge.tick("self", 7, False)
    assert str(ei.value) == "run_submissions refused by the fake; remedy: try the other thing"


def test_read_error_envelope_becomes_bridge_error(fake_prodtools_root, monkeypatch):
    monkeypatch.setenv("FAKE_PRODTOOLS_FAIL", "campaign_status")
    bridge.reset()
    with pytest.raises(bridge.BridgeError, match=r"campaign_status refused by the fake.*\(internal\)"):
        bridge.campaign_status(7, True)


def test_push_file_available_follows_the_listing(fake_prodtools_root, monkeypatch):
    assert bridge.push_file_available() is True
    monkeypatch.setenv("FAKE_PRODTOOLS_OMIT", "push_file")
    bridge.reset()
    assert bridge.push_file_available() is False


def test_push_file_missing_in_prodtools_is_clear_error(fake_prodtools_root, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_PRODTOOLS_OMIT", "push_file")
    bridge.reset()
    with pytest.raises(bridge.BridgeError, match="publish=False"):
        bridge.push_file(tmp_path / "f.txt", "scratch", ["a.root"], "self", False)


def test_push_file_present_forwards(fake_prodtools_root, tmp_path):
    assert bridge.push_file(tmp_path / "f.txt", "tape", ("a.root", "b.root"), "self", False) == {"name": "ok"}
    assert calls(fake_prodtools_root)[-1]["args"] == {"path": str(tmp_path / "f.txt"), "location": "tape",
                                                      "parents": ["a.root", "b.root"], "run_as": "self",
                                                      "confirm": False}


def test_campaign_status_forwards(fake_prodtools_root):
    assert bridge.campaign_status(7, True) == {"called": {"campaign_id": 7, "mine": True}}


def test_campaigns_is_the_ledger_only_listing(fake_prodtools_root):
    assert bridge.campaigns(mine=True) == [{"id": 7, "state": "complete", "tarball": "cnf.u.T.e470313.0.tar"}]
    assert calls(fake_prodtools_root)[-1]["args"] == {"state": None, "mine": True}


def test_cnf_exists(fake_prodtools_root):
    assert bridge.cnf_exists("cnf.u.T.e470313.0.tar") is True
    assert bridge.cnf_exists("cnf.u.T.e470313-001.0.tar") is False


def test_dataset_files_sorted_by_index_with_paths(fake_prodtools_root):
    files = bridge.dataset_files("nts.u.T.e470313.root", "scratch")
    assert files == [
        {"name": "nts.u.T.e470313.00000000.root", "index": 0, "size": 10,
         "path": "/pnfs/scratch/nts.u.T.e470313.root/aa/bb/nts.u.T.e470313.00000000.root"},
        {"name": "nts.u.T.e470313.00000002.root", "index": 2, "size": 20,
         "path": "/pnfs/scratch/nts.u.T.e470313.root/aa/bb/nts.u.T.e470313.00000002.root"},
    ]


def test_dataset_files_unknown_location(fake_prodtools_root):
    with pytest.raises(bridge.BridgeError, match="location"):
        bridge.dataset_files("nts.u.T.e470313.root", "resilient")


def test_dataset_files_refuses_composite_sequencer(fake_prodtools_root, monkeypatch):
    monkeypatch.setenv("FAKE_PRODTOOLS_FILES", json.dumps([["nts.u.T.e470313.001430_00000052.root", 5]]))
    bridge.reset()
    with pytest.raises(bridge.BridgeError, match="001430_00000052"):
        bridge.dataset_files("nts.u.T.e470313.root", "scratch")


def test_missing_new_tool_names_the_prodtools_needed(fake_prodtools_root, monkeypatch):
    monkeypatch.setenv("FAKE_PRODTOOLS_OMIT", "locate_file,dataset_files")
    bridge.reset()
    with pytest.raises(bridge.BridgeError, match="no 'locate_file' tool.*locate_file and dataset_files"):
        bridge.cnf_exists("cnf.u.T.e470313.0.tar")
    with pytest.raises(bridge.BridgeError, match="no 'dataset_files' tool"):
        bridge.dataset_files("nts.u.T.e470313.root", "scratch")


def test_children_are_lazy_and_reused(fake_prodtools_root):
    assert bridge._servers == {}
    bridge.campaign_status(7, False)
    assert set(bridge._servers) == {"read"} and bridge._servers["read"].started
    bridge.tick("self", 7, False)
    assert set(bridge._servers) == {"read", "write"}
    first = bridge._servers["read"]
    bridge.campaigns(mine=False)
    assert bridge._servers["read"] is first


def test_child_death_respawns_on_the_next_call(fake_prodtools_root, monkeypatch):
    monkeypatch.setenv("FAKE_PRODTOOLS_DIE", "list_campaigns")
    bridge.reset()
    with pytest.raises(bridge.BridgeError, match="exited during list_campaigns"):
        bridge.campaigns(mine=True)
    assert bridge.cnf_exists("cnf.u.T.e470313.0.tar") is True


def test_child_that_will_not_start_reports_its_stderr(fake_prodtools_root, monkeypatch):
    monkeypatch.setenv("FAKE_PRODTOOLS_NO_START", "1")
    bridge.reset()
    with pytest.raises(bridge.BridgeError, match="did not start.*refusing to start"):
        bridge.campaigns(mine=True)


def test_prodtools_info_reports_root_and_commit(fake_prodtools_root):
    subprocess.run(["git", "init", "-q", str(fake_prodtools_root)], check=True)
    subprocess.run(["git", "-C", str(fake_prodtools_root), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    info = bridge.prodtools_info()
    assert info["root"] == str(fake_prodtools_root) and len(info["commit"]) == 40


def test_prodtools_info_without_git_reports_commit_none(fake_prodtools_root):
    assert bridge.prodtools_info() == {"root": str(fake_prodtools_root), "commit": None}


def test_prodtools_info_spawns_nothing(fake_prodtools_root):
    bridge.prodtools_info()
    bridge.availability()
    assert bridge._servers == {}


def test_calls_table_covers_every_remote_call():
    assert set(bridge.CALLS) == {"push_cnf", "run_submissions", "push_file", "campaign_status",
                                 "list_campaigns", "locate_file", "dataset_files"}
    kind, always, optional = bridge.CALLS["push_cnf"]
    assert kind == "write" and "json" in always and optional == frozenset({"prodtools_dir"})
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_bridge.py`
Expected: FAIL; the first errors are `AttributeError: module 'beamkit.bridge' has no attribute 'reset'` from the fixture.

- [ ] **Step 4: Rewrite `bridge.py`**

```python
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

from beamkit import BeamkitError
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
    if not push_file_available():
        raise BridgeError("this prodtools has no push_file tool; make_beamfile works with "
                          "publish=False only until prodtools-write gains push_file")
    return _call("push_file", path=str(path), location=location, parents=list(parents),
                 run_as=run_as, confirm=confirm)


def campaign_status(campaign_id, mine) -> dict:
    return _call("campaign_status", campaign_id=campaign_id, mine=mine)


def campaigns(mine) -> list:
    """Every campaign in the caller's ledger (personal for mine=True,
    production otherwise) with its state. Ledger only, no network."""
    return list(_call("list_campaigns", mine=mine)["campaigns"])


def cnf_exists(cnf_name) -> bool:
    return bool(_call("locate_file", name=cnf_name)["exists"])


def dataset_files(dataset, location) -> list:
    """prodtools lists the files with sizes and /pnfs paths; the job index
    is beamkit's reading of the sequencer, and only a plain %08d one."""
    out = _call("dataset_files", dataset=dataset, location=location)
    files = []
    for f in out["files"]:
        parts = f["name"].split(".")
        seq = parts[4] if len(parts) >= 6 else ""
        if not seq.isdigit():
            raise BridgeError(f"{f['name']}: sequencer {seq!r} is not a plain job index; "
                              f"beamkit reads only %08d-indexed g4bl outputs")
        files.append({"name": f["name"], "index": int(seq), "size": int(f["size"]), "path": f["path"]})
    return sorted(files, key=lambda f: f["index"])
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_bridge.py -x`
Expected: 25 passed. Then the whole suite; expected: everything passes except `tests/test_bridge_contract.py` if `BEAMKIT_PRODTOOLS_ROOT` is set in your shell (it is rewritten in Task 4; run the suite with `env -u BEAMKIT_PRODTOOLS_ROOT` for now).

- [ ] **Step 6: Commit**

```bash
git add src/beamkit/bridge.py tests/test_bridge.py tests/conftest.py
git commit -m "feat: bridge drives the prodtools MCP servers instead of importing prodtools

Same nine functions, same signatures. Two lazily spawned children from
\$BEAMKIT_PRODTOOLS_ROOT/mcp/scripts; write failures are the server's own
message, read failures unwrap the safe_tool envelope; a missing tool
names the prodtools release that carries it.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0138sWPyeSCDFbbn2cB9JBDy"
```

---

### Task 4: The contract test checks tool schemas and the copied facts

**Files:**
- Rewrite: `tests/test_bridge_contract.py`
- Modify: `tests/_bridge_contract_probe.py` (drop the bridge-binding half; keep the copied-facts half)
- Repo: `/exp/mu2e/app/users/oksuzian/beamkit`

**Interfaces:**
- Consumes: `bridge.CALLS`, `bridge.launcher(kind)`, `bridge.prodtools_root()` (Task 3); `mcpclient.StdioServer` (Task 2); `mcp.types.Tool.inputSchema`.
- Produces: nothing new. Both tests skip unless `BEAKMIT_PRODTOOLS_ROOT` names a checkout with `mcp/.venv/bin/python` (the launchers need it).

- [ ] **Step 1: Trim the probe to the copied facts**

In `tests/_bridge_contract_probe.py` delete everything from `import inspect` through the `bind(...)` calls and the `bridge.*` calls, i.e. keep only: the module docstring (rewrite it as below), the `sys.path` setup with the `samweb_client`/`ifdh` mocks, the imports of `utils.job_common`, `utils.jobdesc`, `utils.json2jobdef`, the "Facts beamkit copies" block, the g4bl recipe block, and the final `print(json.dumps({"failures": failures}))`. The file becomes:

```python
"""Run by test_bridge_contract in a fresh interpreter: import the REAL
prodtools modules whose facts beamkit copies rather than imports (only
bridge may talk to prodtools, and it does so over MCP) and check the
copies. Prints a JSON report on stdout. Nothing here touches SAM, the
ledger or the grid."""
import json
import sys
from unittest.mock import MagicMock

root = sys.argv[1]
for name in ("samweb_client", "ifdh"):        # Fermilab-only, absent off the gpvms
    sys.modules[name] = MagicMock()
sys.path[:0] = [root, f"{root}/mcp/src"]

import utils.job_common as jc                       # noqa: E402
import utils.jobdesc as jobdesc                     # noqa: E402
import utils.json2jobdef as j2j                     # noqa: E402

from beamkit import compose, naming                 # noqa: E402

failures = []
if tuple(compose.G4BL_WORKER_PARAMS) != tuple(j2j.G4BL_WORKER_PARAMS):
    failures.append(f"compose.G4BL_WORKER_PARAMS {compose.G4BL_WORKER_PARAMS} != json2jobdef {j2j.G4BL_WORKER_PARAMS}")
if not set(compose.OUTLOCS) <= set(jobdesc.OUTLOC_VALID):
    failures.append(f"compose.OUTLOCS {compose.OUTLOCS} not within jobdesc.OUTLOC_VALID {jobdesc.OUTLOC_VALID}")
for got, want in ((naming.cnf_name("u", "T", "e470313"),
                   jc.Mu2eName.build(tier="cnf", owner="u", description="T", dsconf="e470313", sequencer="0", extension="tar")),
                  (naming.dataset("u", "T", "e470313"),
                   jc.Mu2eName.build(tier="nts", owner="u", description="T", dsconf="e470313", extension="root")),
                  (naming.beamfile_name("u", "T", "bm", "e470313"),
                   jc.Mu2eName.build(tier="etc", owner="u", description="TBeam-bm", dsconf="e470313", sequencer="0", extension="txt"))):
    if got != want.filename:
        failures.append(f"naming {got!r} != Mu2eName.build {want.filename!r}")

# The NERSC path runs no prodtools on the node, so beamkit carries the g4bl
# recipe itself. Same inputs, byte-equal script, or the two have drifted.
try:
    import utils.runmu2e as runmu2e            # noqa: E402
    from beamkit import nersc_templates        # noqa: E402
    args = ("Mu2E.in", 11, 10, "/abs/nts.u.T.e470313.00000001.root", {"epsMax": "0.01", "Beam_File": "a b"})
    theirs, ours = runmu2e._g4bl_script(*args[:4], params=args[4]), nersc_templates.g4bl_script(*args)
    if theirs != ours:
        failures.append(f"g4bl recipe drift:\nprodtools: {theirs!r}\nbeamkit:   {ours!r}")
except ImportError as e:
    failures.append(f"utils.runmu2e not importable for the g4bl recipe check: {e}")

print(json.dumps({"failures": failures}))
```

- [ ] **Step 2: Rewrite the contract test**

`tests/test_bridge_contract.py`:

```python
"""The seam between beamkit and prodtools is the prodtools MCP tool
schemas plus a few facts beamkit copies. With BEAMKIT_PRODTOOLS_ROOT
naming a checkout whose mcp/.venv is installed, this spawns the REAL
prodtools servers through their launchers and checks every call bridge
makes against the advertised inputSchema, and runs the copied-facts probe
in a subprocess. Skipped otherwise. Nothing here touches SAM or the grid;
the launchers do run `muse setup ops`, so allow a minute."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from beamkit import bridge
from beamkit.mcpclient import StdioServer

ROOT = os.environ.get("BEAMKIT_PRODTOOLS_ROOT")
pytestmark = pytest.mark.skipif(
    not ROOT or not (Path(ROOT) / "mcp" / ".venv" / "bin" / "python").is_file(),
    reason="BEAMKIT_PRODTOOLS_ROOT does not name a prodtools checkout with mcp/.venv installed")


@pytest.fixture(scope="module")
def servers():
    started = {}
    for kind in ("write", "read"):
        s = StdioServer(f"prodtools-{kind}", str(bridge.launcher(kind)), start_timeout=300)
        s.start()
        started[kind] = s
    yield started
    for s in started.values():
        s.close()


def test_every_bridge_call_matches_the_real_tool_schema(servers):
    problems = []
    for tool, (kind, always, optional) in bridge.CALLS.items():
        s = servers[kind]
        if not s.has_tool(tool):
            problems.append(f"{kind}: no tool {tool!r} (has {sorted(s.tools)})")
            continue
        schema = s.tools[tool].inputSchema or {}
        props = set(schema.get("properties", {}))
        required = set(schema.get("required", []))
        unknown = (always | optional) - props
        if unknown:
            problems.append(f"{tool}: bridge sends {sorted(unknown)} which the schema lacks {sorted(props)}")
        missing = required - always
        if missing:
            problems.append(f"{tool}: schema requires {sorted(missing)} which bridge does not always send")
    assert problems == [], "\n".join(problems)


def test_push_file_is_advertised_by_the_write_server(servers):
    assert servers["write"].has_tool("push_file")


def test_copied_facts_match_prodtools():
    probe = Path(__file__).with_name("_bridge_contract_probe.py")
    r = subprocess.run([sys.executable, str(probe), ROOT], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    assert json.loads(r.stdout)["failures"] == []
```

- [ ] **Step 3: Run it against the dev prodtools checkout**

The prodtools checkout from Task 1 has `mcp/.venv` installed. Run:

```bash
cd /exp/mu2e/app/users/oksuzian/beamkit
BEAMKIT_PRODTOOLS_ROOT=/exp/mu2e/app/users/oksuzian/muse_050125/prodtools \
  env -u PYTHONPATH .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_bridge_contract.py -x
```

Expected: 3 passed (the first spawn takes 30 to 90 s). If the prodtools checkout is not on the Task 1 branch, `locate_file`/`dataset_files` are reported missing: check out that branch and rerun. Then the suite without the variable: `env -u PYTHONPATH -u BEAMKIT_PRODTOOLS_ROOT .venv/bin/python -m pytest -q -p no:cacheprovider`, expected: 3 skipped, rest pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_bridge_contract.py tests/_bridge_contract_probe.py
git commit -m "test: contract test checks the real prodtools tool schemas

Spawns the real servers through their launchers and compares every
argument bridge sends with the advertised inputSchema; the probe keeps
only the facts beamkit copies (g4bl params, outlocs, naming, recipe).

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0138sWPyeSCDFbbn2cB9JBDy"
```

---

### Task 5: `get_server_info` reports availability without spawning

**Files:**
- Modify: `src/beamkit/tools.py` (`get_server_info`, the `try: pt = bridge.prodtools_info()` block)
- Modify: `tests/test_tools.py:41` (fixture) and `:507-512` (the "without prodtools" test)
- Repo: `/exp/mu2e/app/users/oksuzian/beamkit`

**Interfaces:**
- Consumes: `bridge.availability() -> (bool, str)`, `bridge.prodtools_info()` (Task 3).
- Produces: `get_server_info()["backends"]["fermilab"] == {"available": bool, "detail": str}` where `detail` is `availability()`'s text; `["prodtools"]` is `prodtools_info()` when available, else `None`.

- [ ] **Step 1: Adjust the tests**

In `tests/test_tools.py` `fake_bridge` fixture, after the `prodtools_info` monkeypatch line add:

```python
    monkeypatch.setattr(tools.bridge, "availability", lambda: (True, "prodtools at /pt"))
```

Replace `test_get_server_info_without_prodtools_still_answers` with:

```python
def test_get_server_info_without_prodtools_still_answers(fake_bridge, monkeypatch):
    monkeypatch.setattr(tools.bridge, "availability",
                        lambda: (False, "prodtools is not reachable: set BEAMKIT_PRODTOOLS_ROOT"))
    info = tools.get_server_info()
    assert info["backends"]["fermilab"] == {"available": False,
                                            "detail": "prodtools is not reachable: set BEAMKIT_PRODTOOLS_ROOT"}
    assert info["prodtools"] is None


def test_get_server_info_spawns_no_prodtools_child(fake_prodtools_root, beamkit_home):
    tools.get_server_info()
    from beamkit import bridge
    assert bridge._servers == {}
```

- [ ] **Step 2: Run to verify the new test fails**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_tools.py -k get_server_info`
Expected: `test_get_server_info_without_prodtools_still_answers` FAILS (`available` is still True because `prodtools_info` is patched to succeed).

- [ ] **Step 3: Change `get_server_info`**

In `src/beamkit/tools.py` replace

```python
    try:
        pt = bridge.prodtools_info()
        fermilab = {"available": True, "detail": f"prodtools at {pt['root']}"}
    except BeamkitError as e:
        pt, fermilab = None, {"available": False, "detail": str(e)}
```

with

```python
    available, detail = bridge.availability()
    pt = bridge.prodtools_info() if available else None
    fermilab = {"available": available, "detail": detail}
```

- [ ] **Step 4: Run the tests**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_tools.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/beamkit/tools.py tests/test_tools.py
git commit -m "get_server_info: Fermilab availability from the launchers, no child spawned

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0138sWPyeSCDFbbn2cB9JBDy"
```

---

### Task 6: Retire the cvmfs release, one install line, version 0.4.0

**Files:**
- Delete: `bin/install_beamkit.sh`, `scripts/beamkit-mcp-cvmfs`, `scripts/start_mcp.sh`, `docs/cvmfs-release.md`
- Modify: `README.md` (Use it section, Read more list), `docs/developing.md` (rewrite), `docs/architecture.md:47`, `:53`, `:97`, `:120-123`, `docs/tools.md:5` header cell text stays; `.mcp.json`; `pyproject.toml` version; `src/beamkit/__init__.py` `__version__`; `src/beamkit/server.py` TOOLS description of `get_server_info` unchanged
- Repo: `/exp/mu2e/app/users/oksuzian/beamkit`

**Interfaces:**
- Consumes: `bridge.ROOT_VAR`, `ROOT_DEFAULT` (Task 3) for the doc text.
- Produces: version `0.4.0`; no script but `scripts/iri-ping` left in `scripts/`.

- [ ] **Step 1: Delete the cvmfs machinery**

```bash
cd /exp/mu2e/app/users/oksuzian/beamkit
git rm -q bin/install_beamkit.sh scripts/beamkit-mcp-cvmfs scripts/start_mcp.sh docs/cvmfs-release.md
rmdir bin
grep -rn "beamkit-mcp-cvmfs\|install_beamkit\|start_mcp.sh\|cvmfs-release" src tests docs README.md pyproject.toml
```

The grep must come back empty after the edits below; fix every hit.

- [ ] **Step 2: README**

Replace the "Use it" section (from `## Use it` to the line before `Then ask in plain words:`) with:

````markdown
## Use it

One line, every host, if [uv](https://docs.astral.sh/uv/) is installed
(`curl -LsSf https://astral.sh/uv/install.sh | sh`). Add to `.mcp.json`
where you start Claude Code, or to `~/.claude.json`:

```json
{"mcpServers": {"beamkit": {"command": "uvx",
  "args": ["--from", "git+https://github.com/oksuzian/beamkit@v0.4.0", "beamkit-mcp"]}}}
```

Without uv: `pip install git+https://github.com/oksuzian/beamkit.git@v0.4.0`
and `{"mcpServers": {"beamkit": {"command": "beamkit-mcp"}}}`.

**On a Mu2e gpvm**, once: put uv's cache off nashome,
`export UV_CACHE_DIR=/exp/mu2e/app/users/$USER/.uv-cache` in your shell
profile. The Fermilab grid path spawns the prodtools MCP servers from
`/cvmfs/mu2e.opensciencegrid.org/bin/prodtools/current`; set
`BEAMKIT_PRODTOOLS_ROOT` in the server's `env` to use a checkout instead.

**For NERSC jobs:** a Superfacility API client in `~/.sfapi/` and a
`nersc.toml`; five minutes, see [docs/nersc.md](docs/nersc.md).
````

In "Read more", delete the `docs/cvmfs-release.md` bullet and change the developing bullet to `- [docs/developing.md](docs/developing.md): dev venv, test suite, the prodtools contract test.`

- [ ] **Step 3: `docs/developing.md`**

Replace the file with:

````markdown
# Developing beamkit

Users never need this; `uvx` or `pip` from the README is the install.
Dev venv on any host with Python 3.10+ (on a Mu2e host the Mu2e spack
view has one):

```bash
python3 -m venv .venv
env -u PYTHONPATH .venv/bin/pip install -e '.[dev]'
env -u PYTHONPATH .venv/bin/python -m pytest -q -p no:cacheprovider
```

`env -u PYTHONPATH` matters on a host whose shell loads the Mu2e ops
spack environment: its `PYTHONPATH` shadows the venv. Runtime
dependencies are `mcp<2` (2.x renamed FastMCP), `requests`, `authlib`
and, below Python 3.11, `tomli`. beamkit imports no prodtools code: the
Fermilab path spawns prodtools' own MCP servers (`mcp/scripts/start_write_mcp.sh`,
`mcp/scripts/start_mcp.sh`) from `BEAMKIT_PRODTOOLS_ROOT`, default the
cvmfs release `/cvmfs/mu2e.opensciencegrid.org/bin/prodtools/current`.
For a prodtools checkout, install its MCP venv once (`bash mcp/scripts/install.sh`
there) and point the variable at it; `.mcp.json` in this repo does that
for the dev server: `.venv/bin/beamkit-mcp` with `BEAMKIT_PRODTOOLS_ROOT`
in `env`. Edit both paths for another checkout.

The children start on the first Fermilab call (30 to 90 s cold for
`muse setup ops`; `BEAMKIT_PRODTOOLS_START_TIMEOUT`, default 180 s,
bounds it) and stay for the life of the server. A NERSC-only session
never starts them.

## Testing against real prodtools

The suite spawns `tests/fake_prodtools_mcp.py` as the far end, so it
runs anywhere. One test file holds the seam honest against real
prodtools and skips otherwise:

```bash
BEAMKIT_PRODTOOLS_ROOT=/path/to/prodtools env -u PYTHONPATH .venv/bin/python -m pytest -q tests/test_bridge_contract.py
```

It starts the two real servers through their launchers and checks every
argument `bridge.py` sends against the advertised tool schemas, and runs
`tests/_bridge_contract_probe.py` for the facts beamkit copies from
prodtools (g4bl worker params, outloc vocabulary, dot-name grammar, the
g4bl recipe). Run it after pulling prodtools.

## Releasing

Bump `version` in `pyproject.toml` and `__version__` in
`src/beamkit/__init__.py` (a test pins them equal), commit, tag `vX.Y.Z`,
push the tag. Users pin the tag in their `uvx` line. Nothing is built or
published anywhere else.
````

- [ ] **Step 4: `docs/architecture.md`**

- Line 47, the `BRG` node text: `bridge.py<br/>the ONLY module that talks to prodtools:<br/>an MCP client of the two prodtools servers,<br/>spawned from BEAMKIT_PRODTOOLS_ROOT on the first Fermilab call.`
- Line 53, replace `checked<br/>byte-equal by the bridge contract probe` with `checked<br/>byte-equal by the contract probe`.
- Line 97, the `bridge.py` row: `| \`bridge.py\` | MCP client of prodtools' write and read servers (\`mcpclient.StdioServer\` per child, lazy, kept for the process life). Converts every prodtools failure into \`BridgeError\`: isError text from the write server, the \`{"error": ...}\` envelope from the read server. Reads one environment variable, \`BEAMKIT_PRODTOOLS_ROOT\`; the dev checkout to ship arrives as an argument. |`
- Add a row after it: `| \`mcpclient.py\` | A synchronous handle on one MCP server over stdio: private event loop in a daemon thread, one serve task for the session's life, stderr tail for start failures, respawn after a child dies. Knows nothing about prodtools. |`
- Lines 120-123, replace the paragraph with: `The test suite spawns a fake prodtools MCP server as the far end, so one more test holds the seam honest: with \`BEAMKIT_PRODTOOLS_ROOT\` naming a checkout with its MCP venv installed, \`tests/test_bridge_contract.py\` starts the real prodtools servers and checks every argument \`bridge.py\` sends against their tool schemas, plus the facts beamkit copies. It skips otherwise.`
- Line 234 bullet "One import point": change `imports prodtools, and only inside` wording to `talks to prodtools, over MCP, so beamkit's interpreter carries none of prodtools' environment`.

- [ ] **Step 5: `.mcp.json`, version**

`.mcp.json`:

```json
{
  "mcpServers": {
    "beamkit": {
      "command": "/exp/mu2e/app/users/oksuzian/beamkit/.venv/bin/beamkit-mcp",
      "env": {"BEAMKIT_PRODTOOLS_ROOT": "/exp/mu2e/app/users/oksuzian/muse_050125/prodtools"}
    }
  }
}
```

`pyproject.toml`: `version = "0.4.0"`. `src/beamkit/__init__.py`: `__version__ = "0.4.0"`. `README.md` and `docs/developing.md` already say `v0.4.0`.

- [ ] **Step 6: Run the whole suite and the grep**

```bash
env -u PYTHONPATH .venv/bin/python -m pytest -q -p no:cacheprovider
grep -rn "beamkit-mcp-cvmfs\|install_beamkit\|scripts/start_mcp.sh\|cvmfs-release\|0\.3\.1" src tests docs README.md pyproject.toml
```

Expected: suite green (the counts from Task 5 plus nothing new); grep empty except `docs/specs/*` and `docs/plans/*` history, which stay as written.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "release 0.4.0: uvx is the install everywhere; cvmfs installer and launchers retired

beamkit no longer imports prodtools, so nothing needs the ops environment
in beamkit's interpreter and the baked venv on cvmfs has no purpose.
prodtools stays on cvmfs; that is what the bridge spawns.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0138sWPyeSCDFbbn2cB9JBDy"
```

---

### Task 7: Live verification, then the pushes the user must approve

**Files:**
- Modify (prodtools repo, only after the user says so): `.mcp.json` beamkit entry
- Memory: `~/.claude/projects/-exp-mu2e-app-users-oksuzian-muse-050125-prodtools/memory/project_beamkit.md`, `reference_beamkit_cvmfs_release.md` (mark retired), `MEMORY.md`
- Wiki (prodtools repo): `wiki/pages/nersc-iri-feasibility.md`, `wiki/log.md`

This task is run by the controller, not a subagent: it needs the user's Kerberos ticket and their go for every push.

- [ ] **Step 1: Contract test against the Task 1 checkout**

```bash
cd /exp/mu2e/app/users/oksuzian/beamkit
BEAMKIT_PRODTOOLS_ROOT=/exp/mu2e/app/users/oksuzian/muse_050125/prodtools \
  env -u PYTHONPATH .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_bridge_contract.py
```

Expected: 3 passed.

- [ ] **Step 2: Live through Claude Code on the gpvm**

With beamkit's `.mcp.json` from Task 6 (dev server, dev prodtools root), restart Claude Code in the beamkit repo and run, as `run_as="self"`:

1. `get_server_info` → `backends.fermilab.available: true`, `detail: "prodtools at /exp/mu2e/app/users/oksuzian/muse_050125/prodtools"`, `version: "0.4.0"`.
2. `run_beamline(tag="G4blMcp", deck_ref="e470313b49aa9d00924a3f76aea0a00249e7b567", run_as="self", njobs=2, events_per_job=10, params={"epsMax": "0.01"})` → state `submitted`, a `campaign_id`. First call pays the child start.
3. `beamline_status(run_id)` → the prodtools campaign block.
4. When the two jobs land: `beamline_outputs(run_id)` → 2 files with `/pnfs/.../scratch/...` paths (this is `dataset_files` end to end).
5. `make_beamfile(run_id, "bm", "self")` with `publish=False` → a beam file under `beamfiles/`.

Record the run id and job outcome in the wiki log.

- [ ] **Step 3: Ask the user, then push**

Stop and ask before each of these; they are outward-facing:

1. prodtools: `git push origin mcp-locate-and-dataset-files` and `gh pr create --repo Mu2e/prodtools --base main --head oksuzian:mcp-locate-and-dataset-files --title "mcp: locate_file and dataset_files read-only tools" --body "<the Task 1 commit body>` + the PR trailer from the session reminder`.
2. A prodtools cvmfs release once the PR is merged (the user runs it as cvmfsmu2e; `reference_prodtools_cvmfs_releases.md` in memory has the recipe).
3. beamkit: `git push origin v1 && git tag v0.4.0 && git push origin v0.4.0`.
4. prodtools `.mcp.json` beamkit entry → the uvx line from the README, committed only if the user asks.
5. Restart Claude Code with the uvx line and repeat Step 2's `get_server_info` once more against `prodtools/current` after the cvmfs release.

- [ ] **Step 4: Memory and wiki**

- `project_beamkit.md`: beamkit 0.4.0 drives prodtools over MCP; uvx is the install; cvmfs beamkit tree frozen at v0.3.1.
- `reference_beamkit_cvmfs_release.md`: add a first line "RETIRED <release date>: beamkit is installed with uvx; prodtools stays on cvmfs" and keep the rest for history.
- `MEMORY.md`: pointer text updated accordingly.
- Wiki page section "beamkit over MCP (2026-09)" and a log line, with the live run id.
