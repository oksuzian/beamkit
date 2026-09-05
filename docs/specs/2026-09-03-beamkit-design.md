# beamkit — design

**Status:** approved 2026-09-03 (all section 12 questions resolved, defaults accepted for 1, 2, 4); `make_beamfile` + optional SAM publish added same day on request. Repo `oksuzian/beamkit` (new).
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
| `run_beamline(tag, deck_ref, run_as, params={}, events_per_job=1000, njobs=1, main_input="Mu2E.in", outloc="scratch", dsconf=None, slice_size=None, submit=True, confirm=False, deck_dir=None)` | materialize deck, allocate dsconf, write `entry.json` + `run.json`, create the campaign, and (if `submit`) fire the first tick, which submits the whole run when `njobs <= 10000` | `push_cnf`, then `run_submissions(campaign_id=...)` |
| `make_recoveries(run_id, run_as, confirm=False)` | one prodtools tick: verify finished jobs, resubmit the missing indices, and submit any slice of this run not yet submitted (`njobs > 10000` only). Nothing advances on its own; a run with no failures never needs this call. **Ledger-wide:** prodtools scopes only the top-up to the campaign (`utils/submissions.py:1396`) — the verify/recovery pass covers every active campaign in the caller's ledger, so under `run_as="mu2epro"` it recovers production campaigns too. The tool result and `ticks[].summary` say what else moved. | `run_submissions(campaign_id=...)` |
| `beamline_status(run_id)` | run record merged with live campaign state (queue, outputs); `mine=True` for self runs | `campaign_status` |
| `list_beamline_runs(state=None)` | run records under the caller's beamkit dir, newest first | — |
| `beamline_outputs(run_id)` | files of `nts.<owner>.<desc>.<dsconf>.root` with sizes and dCache paths | `find_datasets`, `dataset_details` |
| `make_beamfile(run_id, flavor, run_as, plane="Z3712", cuts=None, publish=False, location=None, confirm=False)` | build a BLTrackFile beam file from a finished stage-1 run's ntuples with a preset or caller-supplied cut table; optionally publish it to SAM with the ntuples as parents | `find_datasets`/`dataset_details` (inputs); `push_file` when `publish=True` |
| `get_server_info()` | beamkit version, prodtools root and commit, venv python, deck cache dir, records dir, beamfiles dir | — |

Arguments are typed; `params` is a dict of g4bl parameter name to
string or number and is passed through as the entry's `g4bl_params`
(prodtools validates names and refuses the worker-owned
`First_Event`/`Num_Events`/`histoFile`/`viewer`). `outloc` is one of
`scratch`, `disk`, `tape`, applied to `nts.*.root`.

`slice_size` defaults to `min(njobs, SLICE_MAX)` with `SLICE_MAX =
10000`, the per-submission job ceiling; a caller value above 10000 or
below 1 is refused before any prodtools call (prodtools itself only
checks `>= 1`). For `njobs <= 10000` the first tick submits the whole
run and later ticks are recovery only. For `njobs > 10000` the run is
several slices: the first tick feeds slices while the grid queue stays
under prodtools' `--max-queued` cap, and `make_recoveries` submits the
rest. Resubmission is a choice, not a requirement: a run whose failed
indices nobody recovers is still a valid run — `make_beamfile` counts
protons from the files that exist (below).

`run_beamline` is not atomic across its two prodtools calls. If
`push_cnf` succeeds and the tick fails, the run record holds the
`campaign_id` and `make_recoveries` finishes the job; the error message
says so. If `push_cnf` itself fails after the SAM push (prodtools'
documented `_ENQUEUE_RECOVERY` case), the record is written with
`campaign_id: null` and the prodtools error text, and the dsconf is
burned — the next call allocates the next suffix.

### `make_beamfile`

Stage-1 decks record every track crossing the `zntuple BeamFile` plane
(`Basic_Detectors.txt`: `z = $Coll_01_z - 173` = 3712 mm, hence tree
`NTuple/Z3712`) in every nts. `make_beamfile` turns one run's ntuples
into a single g4bl `beam ascii` input — the job `MakeSource.py` did by
hand in 2013 — so a later run can resample from it:

1. Inputs: the run's `nts.<owner>.<desc>.<dsconf>.root` files, dCache
   paths from `beamline_outputs`. Whatever exists is used — there is no
   completeness check and no ledger query. A file in SAM is a complete
   job: the worker pushes outputs only on g4bl exit 0, and g4bl exits 0
   only after all `Num_Events`. So `pot = n_files * events_per_job`
   exactly, and `missing_indices` = the run's index range minus the
   sequencers present. Both go in the sidecar and the record; a stage-2
   run normalizes to `pot`, never to `njobs * events_per_job`. Refused,
   loudly: zero files; a file whose `NTuple/<plane>` is absent or
   unreadable (the merge does not skip it).
2. Read `NTuple/<plane>` with `uproot` in the ana 2.8.0 interpreter
   (subprocess; the prodtools venv has no ROOT or uproot — same
   interpreter rule surrokit documents). Apply two layers of cuts:

   **Structural cuts, always on, not parameters** (they make the file a
   valid g4bl source, not a physics choice): `Pz >= 0` (MakeSource.py skips `Pz < 0`); `PDGid <= 1e6`
   (g4bl cannot source exotics); drop a row whose (EventID, TrackID)
   repeats the previous row; TrackID written as 1 (g4bl warns on large
   TrackIDs), original TrackID kept in the trailing column.

   **Physics cuts — the `cuts` parameter.** A dict with exactly these
   keys, validated before any file is read:

   ```python
   {"keep_pdg": list[int] | None,   # exclusive whitelist; None = all PDG ids
    "drop_pdg": list[int],          # blacklist; must be [] when keep_pdg is set
    "min_p_mev": dict[int, float]}  # per-PDG floor on |p| in MeV/c; rows below are dropped
   ```

   Two presets ship, reproducing MakeSource.py (2013) exactly — its
   thresholds are on momentum magnitude, not kinetic energy:

   ```python
   FLAVORS = {
       "bm": {"keep_pdg": None,   "drop_pdg": [2112], "min_p_mev": {22: 1.0, 11: 10.0, -11: 10.0}},
       "ps": {"keep_pdg": [2112], "drop_pdg": [],     "min_p_mev": {}},
   }
   ```

   Resolution rules, all refusals not fallbacks:
   - `cuts=None`: `flavor` must be a preset name; any other name is
     refused (there is no "no cuts" default — that is a cut table too,
     spelled `{"keep_pdg": None, "drop_pdg": [], "min_p_mev": {}}`).
   - `cuts` given: `flavor` is a free label matching `^[a-z][a-z0-9]{0,15}$`
     and must NOT be a preset name — a preset name with different cuts
     would put two meanings behind one `Beam-<flavor>` SAM name.
   - Unknown keys, a missing key, a non-int PDG id, a negative floor, or
     `keep_pdg` and `drop_pdg` both non-empty are refused with the
     offending key named.

   EventIDs are already unique across jobs (`First_Event =
   index*events_per_job + 1`), so the merge is a concatenation.
3. Write `<beamfiles_dir>/<run_id>.<flavor>.txt` in BLTrackFile format
   (the two `#` header lines MakeSource.py wrote) plus
   `<run_id>.<flavor>.json`: source run, plane, flavor, the resolved
   `cuts` dict (preset or caller-supplied — the sidecar never says
   "bm", it says what bm meant), rows in, rows dropped per structural
   and per physics cut, sha256, size, and — after publish — the SAM
   name. Two beam files with the same flavor label but different cuts
   cannot exist: the label is part of the file name, and an existing
   `<run_id>.<flavor>.txt` is refused, not overwritten.
4. `publish=True`: name the file as a Mu2e artifact,
   `etc.<owner>.<desc>Beam-<flavor>.<dsconf>.txt` (the `etc` tier the
   cnf tarballs already use, so pushOutput's location tables apply),
   and call prodtools `push_file(path, location, parents, run_as,
   confirm)` with the nts files as parents. `location` defaults to
   `tape` for `run_as="mu2epro"` and `scratch` for self; `disk` is
   accepted. A published file is never copied tape→disk afterwards —
   choose the location at publish time.
   `publish=False` (default) leaves the file local; the sidecar still
   records everything needed to publish later with the same call.

A beam file at this plane carries ~2.4 tracks per proton before flavor
cuts (smoke: 24 rows from 10 protons), ~110 bytes per row: a 1e7-proton
stage-1 gives ~2.6 GB of ascii. That size is why stage-2 delivery
(below) uses RCDS, not per-job dropbox.

### Stage-2 resampling from a beam file — gated, not v1

`run_beamline(..., beamfile=<run_id>.<flavor>)` is specified here so
the record schema and the tool signature do not change later, but it
ships only when three prerequisites outside beamkit exist:

- **Deck:** `beam ascii filename=$Beam_File` behind `param -unset
  Beam_File=...` in `Mu2E.in` (today's `READ_Beam_File=1|2` branches
  hardcode `/mu2e/data/users/oksuzian/Source_V21_*.txt`, a path that no
  longer exists). A `Mu2e/G4BeamlineScripts` change.
- **prodtools:** a g4bl aux tarball riding `--tar_file_name dropbox://`
  (RCDS, published once, the `code` channel) unpacked at
  `$INPUT_TAR_DIR_LOCAL`, and `{index}` substitution in `g4bl_params`
  values. ~70 lines.
- **beamkit:** pre-split the beam file into `njobs` chunk files, tar
  them, and set `g4bl_params = {"READ_Beam_File": 1, "Beam_File":
  "$INPUT_TAR_DIR_LOCAL/chunks/chunk_{index}.txt"}`. Pre-splitting
  avoids relying on `beam ascii firstEvent`/`nEvents` chunking
  semantics, which are unverified.

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
  {root, commit}, beamkit_version, sweep_id: null, beamfiles: [
  {flavor, cuts, path, sha256, rows, pot, n_files, missing_indices,
  sam_name|null, location|null}],
  beamfile_in: null}` — `beamfiles` is appended by `make_beamfile`;
  `beamfile_in` names the beam file a stage-2 run resampled from.

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
    bridge.py                    # the ONLY module that imports prodtools; push_cnf/tick/status/outputs/push_file wrappers
    beamfile.py                  # cuts table, BLTrackFile writer, chunk splitter; the uproot reader runs as a subprocess
    _read_plane.py               # standalone script run under the ana interpreter: nts paths + plane -> rows (tsv on stdout)
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
  rules for `mcp__beamkit__run_beamline` and `mcp__beamkit__make_recoveries`.
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
- For `make_beamfile(publish=True)`: prodtools-write gains
  `push_file(path, location, parents, run_as, confirm=False)`, ~40
  lines wrapping pushOutput with a `parents_list.txt`, the same
  `run_as`/`confirm` gates as `push_cnf`. Without it `make_beamfile`
  works with `publish=False` only and says so.
- For stage-2 resampling (gated, section 5): the g4bl aux tarball via
  `--tar_file_name` and `{index}` substitution in `g4bl_params`.
- Otherwise the g4bl entry schema is the contract: `runner, desc,
  dsconf, g4bl_dir, main_input, events_per_job, njobs, outloc,
  g4bl_params?`.

## 10. Testing

- Unit: `naming.allocate_dsconf` (free base, one collision, explicit
  taken), `decks.materialize` against a temp bare git repo (sha and tag
  refs, wrong-sha refusal, reuse), `compose.entry` (exact dict, params
  passthrough), `records` round trip, `tools.run_beamline` with
  `bridge` patched (push ok + tick ok; push ok + tick fails leaves a
  recoverable record; push fails burns the dsconf; `slice_size` default
  is `min(njobs, 10000)`; `slice_size=10001` and `slice_size=0` refused
  before `bridge` is touched).
- `beamfile`: cut table on a synthetic row set (each preset, a custom
  `cuts` dict, each structural cut, dedupe order); `cuts` validation
  (unknown key, missing key, keep+drop both set, negative floor, custom
  cuts under a preset flavor name, unknown flavor with `cuts=None` — each
  refused naming the key); preset-vs-custom equivalence (`cuts=FLAVORS["bm"]`
  under flavor `bmcopy` produces byte-identical rows to flavor `bm`);
  BLTrackFile header and column format
  byte-compared against a MakeSource.py-produced fixture, chunk
  splitter (row conservation, `njobs` files, EventID order kept).
  `tools.make_beamfile` with the reader subprocess and `bridge`
  patched: a run with 3 of 5 indices present yields `pot = 3 *
  events_per_job`, `missing_indices = [1, 4]`, and no ledger call; zero
  files refused; a file without the plane refused, not skipped;
  `publish=False` writes file + sidecar and no push; `publish=True` calls `push_file` with the nts
  parents and records the SAM name.
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
- Stage-2 resampling from a beam file is specified but gated on the
  deck and prodtools prerequisites in section 5; it is not in the first
  release. Per-job beam-file inputs resolved from SAM (g4bl
  `input_data`/`inloc`) are not planned at all: RCDS delivery of a
  pre-split file is the chosen route.
- Histogram merging or analysis of the nts outputs.
- Running g4bl locally (prodtools `runlocal` covers it).
- Any write to SAM, the ledger, or jobsub outside prodtools' tools.

## 12. Open questions for review

1. Resolved 2026-09-03: default `events_per_job=1000` accepted. Measured
   2026-09-05 (campaign 8, 10 jobs x 1000 events on prodtools v3.3.2):
   948-2454 s wall per job, mean 1735 s, ~1.4 s per event after the
   ~300 s setup; 1.07 GB RSS; ~550 kB of nts per job; the `bm` beam file
   is 0.81 rows/POT at ~107 bytes/POT. 1000 stays the default; production
   runs should pass 5000-10000 to keep job counts down.
2. Resolved 2026-09-03: deck repo default
   `https://github.com/Mu2e/G4BeamlineScripts` and `main_input="Mu2E.in"`
   accepted.
3. Resolved 2026-09-03: cuts are a parameter (`cuts` dict, section 5
   `make_beamfile`); `bm`/`ps` remain as presets reproducing
   MakeSource.py.
4. Resolved 2026-09-03: records stay under the caller's
   `/exp/mu2e/data/users/$USER/beamkit/` for every `run_as`, mu2epro
   included — the ledger is mu2epro's, the record is the operator's.
