# beamkit

## What it is

beamkit is the G4beamline (g4bl) production front end over the Mu2e
prodtools `g4bl` runner: it owns deck pins, campaign composition, run
records, and an MCP server that exposes seven tools to an agent or
user. It owns no worker code, no submission ledger, no jobsub, and no
SAM writes of its own — every mutating call goes through prodtools'
own MCP tool functions, in-process, with their gates intact.

## Install

Dev venv:

```bash
/cvmfs/mu2e.opensciencegrid.org/spackages/241207/spack/var/spack/environments/ops-019/.spack-env/view/bin/python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest --version
```

beamkit declares no runtime Python dependencies of its own: `mcp` and
`htcondor` come from the prodtools MCP venv at runtime, not from this
package's venv, so the dev venv above is enough to run the test suite
(`.venv/bin/python -m pytest tests -q`) without `mcp` installed.

Running the server needs `BEAMKIT_PRODTOOLS_ROOT` set to a prodtools
checkout whose `mcp/.venv` is installed (see prodtools'
`mcp/scripts/install.sh`). The launcher refuses to start without it:

```bash
BEAMKIT_PRODTOOLS_ROOT=/path/to/prodtools scripts/start_mcp.sh --check
```

`--check` builds the server, confirms every advertised tool is
registered, and imports prodtools through `bridge.prodtools_info()` —
three `OK:` lines on success.

For another checkout, edit the `command` and
`env.BEAMKIT_PRODTOOLS_ROOT` paths in `.mcp.json` — it is checked in
with this personal checkout's absolute paths.

## Tools

| tool | does | prodtools calls |
|---|---|---|
| `run_beamline(tag, deck_ref, run_as, params=None, events_per_job=1000, njobs=1, main_input="Mu2E.in", outloc="scratch", dsconf=None, slice_size=None, submit=True, confirm=False, deck_dir=None)` | materialize deck, allocate dsconf, write `entry.json` + `run.json`, create the campaign, and (if `submit`) fire the first tick, which submits the whole run when `njobs <= 10000` | `push_cnf`, then `run_submissions(campaign_id=...)` |
| `make_recoveries(run_id, run_as, confirm=False)` | one prodtools tick: verify finished jobs, resubmit the missing indices, and submit any slice of this run not yet submitted (`njobs > 10000` only). Nothing advances on its own; a run with no failures never needs this call. **Ledger-wide:** prodtools scopes only the top-up to the campaign — the verify/recovery pass covers every active campaign in the caller's ledger, so under `run_as="mu2epro"` it recovers production campaigns too. | `run_submissions(campaign_id=...)` |
| `beamline_status(run_id)` | run record merged with live campaign state (queue, outputs); `mine=True` for self runs | `campaign_status` |
| `list_beamline_runs(state=None)` | run records under the caller's beamkit dir, newest first | — |
| `beamline_outputs(run_id)` | files of `nts.<owner>.<desc>.<dsconf>.root` with sizes and dCache paths | `find_datasets`, `dataset_details` |
| `make_beamfile(run_id, flavor, run_as, plane="Z3712", cuts=None, publish=False, location=None, confirm=False)` | build a BLTrackFile beam file from a run's ntuples with a preset or caller-supplied cut table; optionally publish it to SAM with the ntuples as parents | `find_datasets`/`dataset_details` (inputs); `push_file` when `publish=True` |
| `get_server_info()` | beamkit version, prodtools root and commit, venv python, deck cache dir, records dir, beamfiles dir | — |

`run_beamline` also takes `deck_url` (default the `Mu2e/G4BeamlineScripts`
repo) to point at a different deck repository.

## Naming

- `desc` = `tag`, the caller-supplied label (e.g. `MuBeam`, `G4blSmoke`),
  validated as a Mu2e description token (`[A-Za-z0-9]+`).
- `dsconf` = the first 7 hex characters of the pinned deck commit sha,
  suffixed `-NNN` only on a SAM collision — the unsuffixed name is
  always tried first, and a cnf name is never reused once taken.
- `run_id` = `<desc>.<dsconf>` — unique by construction.
- Output dataset: `nts.<owner>.<desc>.<dsconf>.root`, `owner` following
  `run_as` (`$USER` for self, `mu2e` for mu2epro).

## Records

Everything lives under `BEAMKIT_HOME` (default
`/exp/mu2e/data/users/$USER/beamkit`):

- `decks/<sha12>/` — a pinned `G4BeamlineScripts` checkout, materialized
  once and reused by any run naming the same sha.
- `runs/<run_id>/entry.json` — the one-entry JSON handed to
  prodtools' `push_cnf`.
- `runs/<run_id>/run.json` — the run record: tag, dsconf, owner,
  run_as, deck pin, params, campaign_id, tarball, datasets, ticks,
  prodtools provenance, and any beam files built from this run.
- `beamfiles/` — beam files built by `make_beamfile`
  (`<run_id>.<flavor>.txt` + `.json` sidecar).

## Privilege

`run_as` is required on every mutating tool and passed through
unchanged: beamkit adds no gate of its own and removes none.
`run_as="self"` writes only your own scratch, datasets and ledger — no
prompt. `run_as="mu2epro"` writes production SAM and submits
production grid jobs; it is refused unless `confirm=true`, both here
and inside prodtools.

Add `mcp__beamkit__run_beamline`, `mcp__beamkit__make_recoveries` and
`mcp__beamkit__make_beamfile` to the PreToolUse hook that prompts on
`mcp__prodtools-write__*` with `run_as=mu2epro`. The in-tool confirm
gate does not depend on it.

## Recoveries are ledger-wide

Prodtools scopes only the top-up to the campaign — the verify/recovery
pass covers every active campaign in the caller's ledger, so under
`run_as="mu2epro"` it recovers production campaigns too.

## Beam files

`make_beamfile` turns one run's `NTuple/<plane>` (default `Z3712`)
crossings into a single g4bl `beam ascii` input. Two presets ship,
reproducing MakeSource.py (2013) exactly:

```python
FLAVORS = {
    "bm": {"keep_pdg": None,   "drop_pdg": [2112], "min_p_mev": {22: 1.0, 11: 10.0, -11: 10.0}},
    "ps": {"keep_pdg": [2112], "drop_pdg": [],     "min_p_mev": {}},
}
```

Any other `flavor` label needs an explicit `cuts` dict:

```python
{"keep_pdg": list[int] | None,   # exclusive whitelist; None = all PDG ids
 "drop_pdg": list[int],          # blacklist; must be [] when keep_pdg is set
 "min_p_mev": dict[int, float]}  # per-PDG floor on |p| in MeV/c; rows below are dropped
```

There is no completeness check: `pot = n_files * events_per_job` from
whatever nts files exist in SAM, and `missing_indices` records the
gap. `location` (`scratch`/`disk`/`tape`) defaults to `tape` for
`run_as="mu2epro"` and `scratch` for self, and is only used when
`publish=True`; a published file is never copied tape-to-disk
afterwards, so choose the location at publish time. Publish needs
prodtools `push_file` — without it `make_beamfile` works with
`publish=False` only, and says so.

## Not in v1

- Sweeps (list-valued params fanning out into N runs) and any
  surrogate-driven loop.
- Stage-2 resampling from a beam file (`run_beamline(...,
  beamfile=...)`): specified so the record schema and tool signature
  won't change later, but gated on deck and prodtools prerequisites
  that don't exist yet.
- `push_file` for anything beyond `make_beamfile(publish=True)`.
