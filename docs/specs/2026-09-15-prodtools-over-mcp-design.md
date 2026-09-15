# beamkit: prodtools over MCP, cvmfs retired

Date: 2026-09-15. Status: approved design, not yet implemented.
Supersedes the in-process bridge described in
`2026-09-03-beamkit-design.md` §7 and §9 and the cvmfs distribution in
`docs/cvmfs-release.md`.

## 1. Goal

beamkit stops importing prodtools. Its Fermilab backend talks to the two
prodtools MCP servers (`prodtools-write`, `prodtools`) as an MCP client
over stdio. Consequences, all wanted:

- beamkit is pure Python everywhere. `uvx --from git+https://github.com/oksuzian/beamkit@<tag> beamkit-mcp`
  is the one install line on a laptop, on a NERSC login node and on a
  Mu2e gpvm.
- The beamkit cvmfs release, its installer and its launcher go away.
  prodtools stays on cvmfs; that is what beamkit spawns.
- The coupling moves from prodtools' Python internals (function
  signatures in `prodtools_mcp_write.tools`, helpers in `utils.*`) to its
  MCP tool schemas, which prodtools already treats as a public contract.

## 2. Non-goals

- No change to what the Fermilab backend does: the same prodtools tools
  run, with the same gates. No new submission path.
- No central or HTTP-hosted server. Identity stays the caller's.
- No change to the NERSC backend, records, naming, beam files.
- prodtools keeps its own cvmfs release and launchers unchanged.

## 3. The coupling today

`src/beamkit/bridge.py` (102 lines) is the only module that imports
prodtools. Nine functions, all called from `tools.py`, `publishing.py`
and `identity.py`:

| bridge function | prodtools code it imports |
|---|---|
| `push_cnf` | `prodtools_mcp_write.tools.push_cnf` |
| `tick` | `prodtools_mcp_write.tools.run_submissions` |
| `push_file_available`, `push_file` | `prodtools_mcp_write.tools.push_file` |
| `campaign_status`, `campaigns` | `prodtools_mcp.tools.status.{campaign_status,list_campaigns}` |
| `cnf_exists` | `utils.samweb_wrapper.locate_file` |
| `dataset_files` | `utils.samweb_wrapper.file_sizes_in_dataset`, `utils.file_resolver.dataset_dir`, `utils.job_common.Mu2eName` |
| `prodtools_info` | `prodtools_mcp_write.runner.REPO_ROOT` |

Importing those in-process means beamkit's interpreter must carry the
`muse setup ops` environment (HTCondor bindings, samweb, jobsub), whose
PYTHONPATH shadows the interpreter's own packages (`typing_extensions`
from the ops-021 view broke `anyio` under uvx on 2026-09-15). Hence the
baked venv and the cvmfs launcher. Remove the import and that whole
apparatus is unnecessary.

## 4. Architecture

```
Claude Code ──stdio──▶ beamkit-mcp (uvx, pure Python)
                          │
                          ├─ NERSC backend ──HTTPS──▶ IRI v2 / Superfacility v1.2
                          │
                          └─ bridge (MCP client)
                               ├─ child "write": $ROOT/mcp/scripts/start_write_mcp.sh
                               │      push_cnf, run_submissions, push_file
                               └─ child "read":  $ROOT/mcp/scripts/start_mcp.sh
                                      campaign_status, list_campaigns,
                                      locate_file, dataset_files
```

`$ROOT` is `BEAMKIT_PRODTOOLS_ROOT`, default
`/cvmfs/mu2e.opensciencegrid.org/bin/prodtools/current`. The prodtools
launchers do `setupmu2e-art.sh`, `muse setup ops` and the PYTHONPATH
dance in the child; beamkit's interpreter never sees any of it.

## 5. The bridge as a client

`bridge.py` keeps its module name, its nine public function names and
their signatures, and `BridgeError`. `tools.py`, `publishing.py`,
`identity.py` and their tests do not change. Everything below is
internal to `bridge.py` plus one new helper module.

### 5.1 `src/beamkit/mcpclient.py`

One class, `StdioServer(name, command, args, env)`:

- Owns a private asyncio event loop running in a daemon thread.
  beamkit's tools are synchronous functions executed inside FastMCP's
  own loop, so a nested `asyncio.run` is impossible; every call is
  `asyncio.run_coroutine_threadsafe(coro, loop).result()`.
- `start()`: opens `mcp.client.stdio.stdio_client(StdioServerParameters(...))`
  and a `ClientSession`, calls `initialize()`, then `list_tools()` and
  caches the tool names and input schemas. Stdin of the child is the
  transport pipe, never beamkit's own stdin. The child's stderr is
  passed through to beamkit's stderr (the launchers already log there).
- `call(tool, **args) -> dict`: `session.call_tool(tool, args)`. Result
  handling is in §8.
- `has_tool(name) -> bool` from the cached listing.
- `close()`: exits the session and transport, stops the loop. Registered
  with `atexit`; also called when a call finds the child gone, so the
  next call respawns.
- Spawn and `initialize` are bounded by one timeout,
  `BEAMKIT_PRODTOOLS_START_TIMEOUT` (default 180 s; `muse setup ops` on a
  cold cvmfs takes tens of seconds). Tool calls have no timeout: a tick
  can legitimately run for minutes while jobsub works.

Not shared with `iri.py`/`sfapi.py`: those are HTTP clients with their
own error model. `mcpclient.py` knows nothing about prodtools.

### 5.2 Lifecycle inside bridge

Two module-level lazies, `_write()` and `_read()`, each returning a
started `StdioServer`, created on first use, under a lock. A NERSC-only
session never creates either. A beamkit process keeps them until it
exits. Both children are tied to the beamkit process: when Claude Code
closes the server, the children get EOF on stdin and exit as MCP servers
do.

`BEAMKIT_PRODTOOLS_ROOT` is read at spawn time. `MU2E_SUBMISSION_DB` and
the rest of the environment pass through to the children unchanged, so
prodtools' own knobs keep working.

## 6. Locating prodtools and reporting availability

- Root: `BEAMKIT_PRODTOOLS_ROOT`, default the cvmfs `current` release.
  A dev checkout works when its `mcp/.venv` is installed
  (`bash mcp/scripts/install.sh`), exactly as prodtools' own `.mcp.json`
  expects.
- Launchers: `$ROOT/mcp/scripts/start_write_mcp.sh` and
  `$ROOT/mcp/scripts/start_mcp.sh`.
- `get_server_info().backends.fermilab.available` is true iff both
  launchers exist and are executable. That check spawns nothing, so
  `get_server_info` stays cheap. `detail` names the root and, when
  unavailable, which launcher is missing and how to set the root.
- `prodtools_info()` returns `{"root": <resolved root>, "commit": <git
  HEAD of root, or None for a cvmfs release>}`, computed by beamkit
  itself exactly as today. No prodtools change needed for it.

`BEAMKIT_PRODTOOLS_DIR` (a dev prodtools tree to ship to the workers,
`identity.dev_dir_from_env`) is unrelated to the root and unchanged: it
still rides through `push_cnf(prodtools_dir=...)`.

## 7. Tool mapping

| bridge function | child | tool | arguments sent |
|---|---|---|---|
| `push_cnf(json_path, desc, dsconf, slice_size, run_as, confirm, prodtools_dir)` | write | `push_cnf` | `json, desc, dsconf, slice_size, run_as, confirm`, plus `prodtools_dir` only when set |
| `tick(run_as, campaign_id, confirm)` | write | `run_submissions` | `run_as, campaign_id, confirm` |
| `push_file(path, location, parents, run_as, confirm)` | write | `push_file` | `path, location, parents, run_as, confirm` |
| `push_file_available()` | write | — | `has_tool("push_file")` |
| `campaign_status(campaign_id, mine)` | read | `campaign_status` | `campaign_id, mine` |
| `campaigns(mine)` | read | `list_campaigns` | `mine`; returns the `campaigns` list |
| `cnf_exists(name)` | read | `locate_file` (new) | `name`; returns `exists` |
| `dataset_files(dataset, location)` | read | `dataset_files` (new) | `dataset, location` |
| `prodtools_info()` | — | — | local, §6 |

### 7.1 Two new read-only tools in prodtools

Added to `mcp/src/prodtools_mcp/tools/discovery.py` and registered in
`prodtools_mcp/server.py`; a PR to Mu2e/prodtools. Both are pure reads
of SAM and the location tables, in keeping with that server's no-writes
promise.

`locate_file(name: str) -> {"name": str, "exists": bool, "locations": [str]}`
wraps `utils.samweb_wrapper.locate_file`. A name SAM does not know is
`exists: false`, not an error.

`dataset_files(dataset: str, location: str) -> {"dataset": str, "location": str, "root": str, "n_files": int, "total_size": int, "files": [{"name": str, "size": int, "path": str}]}`
is today's `bridge.dataset_files` moved server-side: `root` from
`utils.file_resolver.dataset_dir(dataset, location)`, sizes from
`file_sizes_in_dataset`, `path` from `Mu2eName.relpathname()`. Files are
sorted by name. An unknown `location` for the dataset is a `ToolError`
of kind `invalid_argument`. It does not interpret the sequencer.

beamkit then derives `index` itself: the fifth dot-separated field of a
Mu2e file name must be all digits or `dataset_files` raises
`BridgeError` with the same message as today ("sequencer ... is not a
plain job index"). That keeps the "only %08d-indexed g4bl outputs" rule
in beamkit, where it belongs.

### 7.2 Missing tools

If the spawned read server does not list `locate_file` or
`dataset_files` (an older prodtools), the bridge raises `BridgeError`
naming the tool and the prodtools release that carries it, on the call
that needs it. `push_file` keeps today's behaviour: probed up front by
`make_beamfile(publish=True)`.

## 8. Results and errors

- A prodtools tool returns a dict; FastMCP serialises it as JSON in the
  first text content block. The bridge parses that block. If a result
  also carries `structuredContent` (newer `mcp`), it is preferred; the
  two are the same object.
- `isError: true` becomes `BridgeError(text)`. The text is prodtools'
  own message; FastMCP prefixes it with "Error executing tool <name>: "
  and the bridge strips that prefix so the user sees the `ToolError`
  message and remedy as written.
- Child fails to spawn or to `initialize` within the start timeout:
  `BridgeError` carrying the launcher path and the last 20 lines of the
  child's stderr (the launchers print exactly why: no `.venv`, `muse`
  failure, missing `mcp/src`).
- Child dies mid-call: `BridgeError("prodtools <name> server exited
  during <tool>")`; the server object is closed so the next call
  respawns. No automatic retry of a write.
- The `_HINT` string in bridge, shown when prodtools is unavailable,
  changes to: set `BEAMKIT_PRODTOOLS_ROOT` to a prodtools tree with
  `mcp/scripts/start_write_mcp.sh` (the cvmfs release, or a checkout
  after `bash mcp/scripts/install.sh`).

Everything in §8 is inside `bridge.py`/`mcpclient.py`; callers keep
seeing `BridgeError`, a `BeamkitError`, as they do now.

## 9. Identity and gates

Unchanged. `run_as` and `confirm` pass through verbatim; prodtools-write
refuses `run_as="mu2epro"` without `confirm=true` on its side, and
beamkit refuses before calling. The PreToolUse hook that guards
`prodtools-write` in Claude Code does not fire for calls beamkit makes
programmatically; that is true today with the in-process bridge as well,
which is why beamkit carries its own confirm gate (`identity.resolve`).
Nothing new is exposed.

The children run as the same Unix user as beamkit, with the same
Kerberos ticket and tokens. Nothing is delegated.

> Addendum (final review, 2026-09-15). The default root is the cvmfs prodtools release, and `availability()` is satisfied by two executable launchers, which is true on every Mu2e gpvm. So an unconfigured `uvx` beamkit on any Mu2e host can spawn `prodtools-write` and, with `run_as="mu2epro"` and `confirm=true` from the caller, submit production jobs; the beamkit-side `confirm` gate in `identity.resolve` and prodtools-write's own refusal without `confirm=true` are the only gates, exactly as with the in-process bridge before this change. The install no longer acts as an accidental gate. Recommended, outside this spec: a Claude Code PreToolUse hook on beamkit's `run_beamline`, `make_recoveries` and `make_beamfile` that prompts whenever `run_as` is `mu2epro`, mirroring the existing `prodtools-write` guard hook.

## 10. Distribution

Removed from the repo:

- `bin/install_beamkit.sh`
- `scripts/beamkit-mcp-cvmfs`
- `scripts/start_mcp.sh` (the dev launcher that sourced prodtools' env)
- `docs/cvmfs-release.md`
- the `htcondor` install-time dependency and the `-c SERIES` machinery
  that existed only for it

Kept: `scripts/iri-ping`. `pyproject.toml` dependencies stay
`mcp>=1.2,<2`, `requests`, `authlib`, `tomli` for 3.10; the `mcp`
package already contains the client.

Install, every host, README's only instruction:

```json
{"mcpServers": {"beamkit": {"command": "uvx",
  "args": ["--from", "git+https://github.com/oksuzian/beamkit@v0.4.0", "beamkit-mcp"]}}}
```

or `pip install git+...@v0.4.0` and `"command": "beamkit-mcp"`. On a
gpvm two one-time notes: install uv (`curl -LsSf https://astral.sh/uv/install.sh | sh`),
and put `UV_CACHE_DIR` under `/exp/mu2e/app/users/$USER` (nashome quota;
its NFS keys expire with a stale ticket). `docs/developing.md` carries
the dev variant: `.venv` plus `BEAMKIT_PRODTOOLS_ROOT` pointing at a
prodtools checkout with `mcp/.venv` installed.

`/cvmfs/mu2e.opensciencegrid.org/bin/beamkit/` stays as published
(v0.3.0, v0.3.1, `current`) and is not updated again; nothing depends on
it once `.mcp.json` files point at uvx. prodtools' own `.mcp.json` entry
for beamkit becomes the uvx line.

`docs/architecture.md` §bridge and the tool docs change accordingly;
`docs/nersc.md` is untouched.

## 11. Testing

- `tests/fake_prodtools_mcp.py`: a runnable FastMCP server (started as
  `python -m tests.fake_prodtools_mcp write|read`) exposing the tool
  names above with canned answers, and env-controlled failures:
  `FAKE_PRODTOOLS_FAIL=<tool>` makes that tool raise, `FAKE_PRODTOOLS_DIE=<tool>`
  makes the process exit during that call, `FAKE_PRODTOOLS_NO_START=1`
  prints a message to stderr and exits 3 before serving,
  `FAKE_PRODTOOLS_OMIT=<tool,...>` hides tools from the listing.
- `tests/test_mcpclient.py`: start, list, call, error result, child
  death and respawn, start timeout, close; all against the fake as a
  real child process.
- `tests/test_bridge.py` (rewritten): every bridge function against the
  fake, asserting the exact arguments received (the fake records them to
  a JSON file named by env), `prodtools_dir` omitted when None, the
  `campaigns` unwrapping, `dataset_files` index parsing and its
  sequencer refusal, `push_file_available` from the listing, the
  missing-tool message, the stripped "Error executing tool" prefix.
- `tests/test_bridge_contract.py` (rewritten): with `BEAMKIT_PRODTOOLS_ROOT`
  set to a prodtools checkout whose `mcp/.venv` is installed, spawn the
  two real servers and check that each tool bridge calls exists and its
  `inputSchema.properties` contains every argument name bridge sends,
  and that `required` is satisfied. Skipped, as today, when the root or
  its venv is absent. This is the seam test; nothing in it touches SAM
  or the grid.
- `tests/test_tools.py` keeps its `fake_bridge` monkeypatching; it is
  agnostic to how the bridge works.
- Suite still runs with `env -u PYTHONPATH .venv/bin/python -m pytest`;
  the fake server needs only `mcp`.
- Live check before release, on a gpvm through Claude Code with the uvx
  line: `get_server_info` (fermilab available), a 2-job `run_beamline`
  as self, `beamline_status`, `beamline_outputs`, `make_beamfile` with
  `publish=False`. Same script as the v0.3 verification.

## 12. Sequencing and versions

1. prodtools: PR to Mu2e/prodtools adding `locate_file` and
   `dataset_files` to the read-only server, with tests in
   `mcp/tests/`. Merge, then a prodtools cvmfs release so
   `prodtools/current` carries them.
2. beamkit 0.4.0: `mcpclient.py`, new `bridge.py`, fakes and tests,
   removals in §10, docs, `__version__`. Developed and verified against
   the prodtools checkout from step 1 via `BEAMKIT_PRODTOOLS_ROOT`
   before the cvmfs prodtools release lands, then re-verified against
   `current`.
3. Tag `v0.4.0`, push. Update prodtools' `.mcp.json` beamkit entry to
   the uvx line.

## 13. Risks and mitigations

- **Child startup cost.** `muse setup ops` in each child, ~10 to 60 s
  cold. Paid once per beamkit process, only on the first Fermilab call,
  and the two children start in parallel when a call needs both. NERSC
  users pay nothing.
- **Two children, one user.** Each child holds its own HTCondor and
  samweb sessions; that is how the prodtools servers run under Claude
  Code already.
- **A prodtools release without the new tools.** Explicit
  `BridgeError` naming the tool (§7.2); status and submission keep
  working, only `run_beamline` (dsconf allocation via `cnf_exists`) and
  `beamline_outputs`/`make_beamfile` on Fermilab need them.
- **Schema drift.** The contract test in §11 catches a renamed or
  newly required parameter before a release; the bridge sends only
  named arguments.
- **uv absent on a gpvm.** pip into a user venv is the documented
  alternative; python 3.10+ is available through the Mu2e spack view,
  or uv brings its own.

## 14. Rulings recorded

- The bridge stays one module with the same nine functions rather than
  a per-child class API: the callers are already written against it and
  the tests for tools rely on monkeypatching those names.
- `prodtools_info()` stays local instead of adding root/commit to
  prodtools' `get_server_info`: one fewer prodtools change, identical
  output.
- `dataset_files` moves the SAM/location logic server-side but not the
  sequencer rule: the rule is beamkit's, and prodtools' tool stays a
  general dataset listing other clients can use.
- The beamkit cvmfs tree is left in place, frozen, rather than deleted:
  deleting needs the cvmfsmu2e account for no benefit.
