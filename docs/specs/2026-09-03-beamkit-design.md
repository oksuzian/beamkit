# beamkit — design

**Status:** draft for review, 2026-09-03. Repo `oksuzian/beamkit` (new).
Sibling of `oksuzian/surrokit` in naming and shape: a small, focused
package with an MCP server, no production machinery of its own.

## 1. Problem

G4beamline (g4bl) production runs through prodtools as a `runner:
"g4bl"` entry: `json2jobdef` freezes a deck directory into a cnf,
`submissions` slices/recovers/verifies, `runmu2e` runs g4bl on the
worker and pushes `nts.<owner>.<desc>.<dsconf>.<idx>.root` to SAM.
That works (grid smoke 2026-09-02, campaign 5 / cluster 29824782), but
driving it means knowing prodtools vocabulary — `dsconf`, `slice_size`,
`--prodtools-dir`, ledger ticks, `run_as` — and every g4bl-specific
convenience (deck pinning, parameter overrides, sweeps, output lookup)
would otherwise accrete inside prodtools, a production-critical repo
whose worker code ships through a cvmfs release train.

beamkit is the g4bl **front end**: a domain vocabulary over the
prodtools runner. It owns decks pins, campaign composition, run records
and an MCP server. It owns no worker code, no ledger, no jobsub, no
SAM writes of its own.

## 2. Boundary

```
 agent / user
     │  run_beamline(tag, deck_ref, params, events_per_job, njobs, run_as)
     ▼
 beamkit (this repo)             pure Python, runs with prodtools' MCP venv
   decks.materialize   ── git fetch <sha> → /exp/mu2e/data/users/$USER/beamkit/decks/<sha12>/
   naming.allocate     ── desc = tag, dsconf = <sha7>[-NNN]  (SAM probe, never reuse)
   compose.entry       ── {"runner":"g4bl", g4bl_dir, main_input, events_per_job, njobs,
                           g4bl_params, outloc, desc, dsconf}
   records.write       ── runs/<run_id>/{entry.json, run.json}
   bridge.push_cnf     ── prodtools_mcp_write.tools.push_cnf(json, desc, dsconf, slice_size, run_as, confirm)
   bridge.tick         ── prodtools_mcp_write.tools.run_submissions(run_as, campaign_id, confirm)
   bridge.status       ── prodtools_mcp read tools (campaign_status, find_datasets, dataset_details)
     │
     ▼
 prodtools                        unchanged contract: g4bl entry in, nts dataset in SAM out
   json2jobdef → cnf → SAM ; submissions ledger ; jobsub ; runmu2e (worker, cvmfs release)
```

What crosses the boundary is one JSON entry file and prodtools' own
MCP tool functions, called in-process. beamkit never shells out to
`jobsub_submit`, `samweb`, `pushOutput`, or `json2jobdef` directly, and
never opens the ledger. Every privilege gate stays where it is:
`run_as="mu2epro"` is refused by prodtools' `require_confirmed` unless
`confirm=True`, and the operator's PreToolUse hook must also cover
beamkit's tool names (section 8).

### Why in-process import, not MCP-to-MCP or CLI

- MCP-to-MCP needs an MCP client inside a server, two processes, and
  loses the typed return values.
- Shelling out to `bin/json2jobdef` would re-implement the environment
  recipe (`setupmu2e-art.sh && muse setup ops && setup OfflineOps`,
  ksu for mu2epro, the stderr rc sentinel) that `prodtools_mcp_write`
  already gets right.
- Importing `prodtools_mcp_write.tools` and `prodtools_mcp.tools.*`
  gives exactly the surface prodtools already exposes to agents, with
  its gates, and nothing more.

Cost: beamkit runs with prodtools' venv interpreter (`mcp`, `htcondor`
present there) and needs the prodtools checkout on `PYTHONPATH`. It
declares no Python dependencies of its own.

## 3. Decks: pinned by commit

A run names its deck as `deck_ref`: a commit sha or tag in
`Mu2e/G4BeamlineScripts` (repo URL configurable, default that one).
beamkit materializes it once under
`/exp/mu2e/data/users/$USER/beamkit/decks/<sha12>/` with

```
git init && git remote add origin <url> && git fetch --depth 1 origin <sha> && git checkout FETCH_HEAD
```

verifies `git rev-parse HEAD` equals the resolved sha, and passes that
directory as the entry's `g4bl_dir`. prodtools then copies it into the
cnf, so the cnf freezes exactly the pinned commit and the run record
names it. A tag is resolved to its sha at materialize time and both are
recorded. The materialized tree is never modified; a second run with
the same sha reuses it.

`deck_dir` (an absolute local path) is accepted as an explicit
alternative for development, `run_as="self"` only. The run record then
carries `deck: {"dir": ..., "pinned": false, "git": {"commit", "dirty"}}`
from `git -C <dir>`; a dirty tree is recorded, not refused. This is an
opt-in, never a fallback: a missing or unfetchable `deck_ref` is an
error, it does not silently use a local clone.

## 4. Naming

- `desc` = `tag` (caller-supplied, e.g. `MuBeam`, `G4blSmoke`).
  Validated as a Mu2e description token: `[A-Za-z0-9]+`.
- `dsconf` = first 7 hex chars of the deck sha, suffixed `-NNN` only on
  collision. Collision test is the cnf name in SAM:
  `cnf.<owner>.<desc>.<dsconf>.0.tar` (prodtools rule: a cnf name is
  never reused, pause does not free it). The unsuffixed name is always
  tried first; `-000` is never issued for a free base. A caller may pass
  `dsconf` explicitly; the same probe applies and a taken name is an
  error.
- `owner` is not a beamkit input. It follows `run_as`: `$USER` for
  self, `mu2e` for mu2epro, exactly as prodtools derives it.
- `run_id` = `<desc>.<dsconf>` — unique by construction.
- Output dataset: `nts.<owner>.<desc>.<dsconf>.root` (prodtools
  convention, reported back by `push_cnf` as `datasets`).

Two runs of the same deck commit with different `params` differ by
`tag`, or by `-NNN` if the caller keeps the tag. The dsconf therefore
answers "which deck commit" without opening the cnf, and the run
record answers "which params".

## 5. MCP tools (v1)

Server name `beamkit`, stdio, FastMCP, same launcher shape as
prodtools (`scripts/start_mcp.sh`, `--check` lists registered tools).

| tool | does | prodtools calls |
|---|---|---|
| `run_beamline(tag, deck_ref, run_as, params={}, events_per_job=1000, njobs=1, main_input="Mu2E.in", outloc="scratch", dsconf=None, slice_size=None, submit=True, confirm=False, deck_dir=None)` | materialize deck, allocate dsconf, write `entry.json` + `run.json`, create the campaign, and (if `submit`) fire the first tick | `push_cnf`, then `run_submissions(campaign_id=...)` |
| `beamline_tick(run_id, run_as, confirm=False)` | one recovery/verify/top-up tick for that campaign (prodtools ticks are manual; nothing advances on its own) | `run_submissions(campaign_id=...)` |
| `beamline_status(run_id)` | run record merged with live campaign state (queue, outputs); `mine=True` for self runs | `campaign_status` |
| `list_beamline_runs(state=None)` | run records under the caller's beamkit dir, newest first | — |
| `beamline_outputs(run_id)` | files of `nts.<owner>.<desc>.<dsconf>.root` with sizes and dCache paths | `find_datasets`, `dataset_details` |
| `get_server_info()` | beamkit version, prodtools root and commit, venv python, deck cache dir, records dir | — |

Arguments are typed; `params` is a dict of g4bl parameter name to
string or number and is passed through as the entry's `g4bl_params`
(prodtools validates names and refuses the worker-owned
`First_Event`/`Num_Events`/`histoFile`/`viewer`). `outloc` is one of
`scratch`, `disk`, `tape`, applied to `nts.*.root`. `slice_size`
defaults to `njobs` (one tick submits everything) capped at 1000.

`run_beamline` is not atomic across its two prodtools calls. If
`push_cnf` succeeds and the tick fails, the run record holds the
`campaign_id` and `beamline_tick` finishes the job; the error message
says so. If `push_cnf` itself fails after the SAM push (prodtools'
documented `_ENQUEUE_RECOVERY` case), the record is written with
`campaign_id: null` and the prodtools error text, and the dsconf is
burned — the next call allocates the next suffix.

Sweeps (list-valued params → N runs) are **not** in v1. The run record
already has a `sweep_id` field (null) so a later `sweep_beamline` can
group its children without a schema change.

## 6. Run records

`/exp/mu2e/data/users/$USER/beamkit/runs/<run_id>/`:

- `entry.json` — the exact one-entry JSON handed to `push_cnf`. Feeding
  it to `json2jobdef --json entry.json --desc <tag> --dsconf <dsconf>`
  rebuilds the same cnf from the same pinned deck.
- `run.json` — `{run_id, tag, dsconf, owner, run_as, deck: {url, ref,
  sha, dir, pinned}, params, events_per_job, njobs, outloc, campaign_id,
  tarball, datasets, created, ticks: [{when, rc, summary}], prodtools:
  {root, commit}, beamkit_version, sweep_id: null}`.

Records are files, not a database: prodtools' ledger is the system of
record for submission state; beamkit records only what prodtools does
not know (deck pin, params as given, which campaign belongs to which
run). Nothing in beamkit reads the ledger sqlite directly.

## 7. Repository layout

```
beamkit/
  README.md
  pyproject.toml                 # name beamkit; no runtime deps (mcp comes from the prodtools venv); [dev] = pytest
  .mcp.json                      # registers "beamkit": scripts/start_mcp.sh for Claude Code in this checkout
  scripts/start_mcp.sh           # sources $BEAMKIT_PRODTOOLS_ROOT/mcp/scripts/_mcp_env.sh, adds src/ to PYTHONPATH, exec python -m beamkit.server ; --check
  src/beamkit/
    __init__.py                  # __version__
    server.py                    # FastMCP registration, TOOL_NAMES
    tools.py                     # the six tools; thin, orchestration only
    decks.py                     # materialize(url, ref) -> DeckPin ; inspect_local(dir)
    naming.py                    # validate_tag, allocate_dsconf(owner, desc, probe_fn)
    compose.py                   # entry(...) -> dict ; write_entry_json
    records.py                   # RunRecord dataclass, save/load/list, records_dir(run_as)
    bridge.py                    # the ONLY module that imports prodtools; push_cnf/tick/status/outputs wrappers
  tests/                         # unit tests; bridge patched, git operations against a temp bare repo
  docs/specs/2026-09-03-beamkit-design.md
```

`bridge.py` is the single import point for prodtools so that the
dependency surface is one file long and a prodtools rename breaks one
module. Everything else is testable without prodtools installed.

Python 3.10+ (prodtools' venv floor). PEP 604 unions allowed here —
beamkit never runs on a worker, so the py3.9 rule for prodtools
`utils/` does not apply.

## 8. Privilege and safety

- `run_as` is required on every mutating tool and is passed through
  unchanged. beamkit adds no gate of its own and removes none:
  `mu2epro` without `confirm=True` is refused inside prodtools.
- The operator's PreToolUse hook that prompts on
  `mcp__prodtools-write__*` with `run_as=mu2epro` must gain matching
  rules for `mcp__beamkit__run_beamline` and `mcp__beamkit__beamline_tick`.
  The README documents this as an install step; the hook is defence in
  depth and the in-tool `confirm` gate does not depend on it.
- Read tools (`beamline_status`, `list_beamline_runs`,
  `beamline_outputs`, `get_server_info`) perform no writes and need no
  privilege.
- No `timeout` wrapping around ticks (prodtools rule: a killed tick
  orphans a cluster).
- beamkit never refreshes or reads a mu2epro token; prodtools' ksu path
  owns that.

## 9. Prodtools prerequisites

- Worker: prodtools release with the g4bl runner (v3.3.1, tag pending
  at `7abdea7`) and `g4bl_params` (v3.3.2 candidate, `3b088aa`).
- `push_cnf` gains an optional `prodtools_dir: str | None = None`
  parameter forwarded as `--prodtools-dir` (~10 lines, prodtools side)
  so beamkit can run a checkout's dev tarball before a release lands.
  beamkit exposes it only through `get_server_info` configuration
  (`BEAMKIT_PRODTOOLS_DIR` env), not as a per-run argument: which
  prodtools runs is deployment, not physics.
- No other prodtools change. The g4bl entry schema is the contract:
  `runner, desc, dsconf, g4bl_dir, main_input, events_per_job, njobs,
  outloc, g4bl_params?`.

## 10. Testing

- Unit: `naming.allocate_dsconf` (free base, one collision, explicit
  taken), `decks.materialize` against a temp bare git repo (sha and tag
  refs, wrong-sha refusal, reuse), `compose.entry` (exact dict, params
  passthrough), `records` round trip, `tools.run_beamline` with
  `bridge` patched (push ok + tick ok; push ok + tick fails leaves a
  recoverable record; push fails burns the dsconf).
- `scripts/start_mcp.sh --check` registration parity with
  `TOOL_NAMES`.
- Smoke: `run_beamline(tag="G4blSmoke", deck_ref=<current
  G4BeamlineScripts sha>, params={}, events_per_job=10, njobs=3,
  run_as="self")` — must reproduce the 2026-09-02 grid smoke through
  beamkit end to end, then `beamline_outputs` lists 3 files.

## 11. Non-goals (v1)

- Sweeps and surrogate-driven loops (surrokit is the ask/tell engine;
  a `sweep_beamline` that fans out runs and a client that feeds
  surrokit are v2, on top of the record schema above).
- Beam-file inputs from SAM (g4bl `input_data`/`inloc`) — a prodtools
  runner feature; beamkit gains one field when it exists.
- Histogram merging or analysis of the nts outputs.
- Running g4bl locally (prodtools `runlocal` covers it).
- Any write to SAM, the ledger, or jobsub outside prodtools' tools.

## 12. Open questions for review

1. Default `events_per_job=1000`: the 2026-09-02 smoke ran 10 events in
   4–6 minutes with field generation dominating; 1000 events per job is
   a guess at a sensible production chunk. Confirm or change.
2. Deck repo default `https://github.com/Mu2e/G4BeamlineScripts` and
   `main_input="Mu2E.in"`: confirm these are the production deck and
   entry point.
3. Records under the caller's `/exp/mu2e/data/users/$USER/beamkit/`
   even for `run_as="mu2epro"` runs (the ledger is mu2epro's, the record
   is the operator's). Acceptable, or should mu2epro runs record under
   `/exp/mu2e/data/users/mu2epro/beamkit/` via the same ksu path?
