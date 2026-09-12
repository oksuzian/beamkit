# beamkit

## What it is

beamkit is the G4beamline (g4bl) production front end over the Mu2e
prodtools `g4bl` runner: it owns deck pins, campaign composition, run
records, and an MCP server that exposes eight tools to an agent or
user. It owns no worker code, no submission ledger, no jobsub, and no
SAM writes of its own — every mutating call goes through prodtools'
own MCP tool functions, in-process, with their gates intact.

## Quick start

### On a Mu2e gpvm (Fermilab grid or NERSC)

Nothing to install. Add to the `.mcp.json` of the directory you start
Claude Code in (or `~/.claude.json` for every directory):

```json
{"mcpServers": {"beamkit": {"command":
  "/cvmfs/mu2e.opensciencegrid.org/bin/beamkit/current/scripts/beamkit-mcp-cvmfs"}}}
```

Start Claude Code, `/mcp` shows `beamkit` connected. Records, deck pins
and beam files live under `/exp/mu2e/data/users/$USER/beamkit/`.

For NERSC jobs, also do steps 1 and 3 of "NERSC from a laptop" below
(sfapi client in `~/.sfapi`, `nersc.toml` in that beamkit directory).

### On a laptop (NERSC only)

```bash
pip install git+https://github.com/oksuzian/beamkit.git@v0.3.0
```

then steps 1, 3 and 4 of "NERSC from a laptop". Records live in
`~/.beamkit/`.

### A session

Ask in plain words; the agent picks the tool.

| you say | tool called |
|---|---|
| "Is beamkit working?" | `get_server_info` -- both `backends` should say `available: true` |
| "Run 100 g4bl jobs of 1000 events from deck tag v3 on NERSC" | `run_beamline(tag="G4blBeam", deck_ref="v3", run_as="self", site="nersc", njobs=100, events_per_job=1000, params={"epsMax": "0.01"})` |
| "Same on the grid" | `run_beamline(tag="G4blBeam", deck_ref="v3", run_as="self", njobs=100, events_per_job=1000, params={"epsMax": "0.01"})` |
| "How is run G4blBeam.e470313 doing?" | `beamline_status("G4blBeam.e470313")` |
| "List my runs" | `list_beamline_runs()` |
| "Where are the outputs?" | `beamline_outputs(run_id)` -- CFS paths for NERSC, dataset files for Fermilab |
| "Build the bm beam file" | `make_beamfile(run_id, "bm", "self", site="nersc")` or without `site` on Fermilab |
| "Submit the jobs I created with submit=False" | `submit_run(run_id, "self")` (NERSC only) |
| "Recover the missing jobs" | `make_recoveries(run_id, "self")` (Fermilab only; NERSC has no recovery, rerun instead) |

`params` are `key=value` overrides appended to the g4bl command line.
Until G4BeamlineScripts carries `param epsMax=0.01`, every run needs
`params={"epsMax": "0.01"}`: g4bl's built-in default is 0.05 and the
Geant4 in g4beamline 3.08b aborts on it (`G4Exception Geometry001`,
exit 99, two logs and no output file).

`run_as="self"` is always safe: your account, your scratch, your ledger.
`run_as="mu2epro"` (Fermilab only) needs `confirm=True` and a hook
prompt, and is refused on NERSC. `tag` is the Mu2e description token
of the output dataset; `deck_ref` is a git tag or sha of the deck repo.
Default deck repo: `https://github.com/Mu2e/G4BeamlineScripts`.

## Install

Dev venv:

```bash
/cvmfs/mu2e.opensciencegrid.org/spackages/241207/spack/var/spack/environments/ops-019/.spack-env/view/bin/python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest --version
```

Runtime dependencies are `mcp<2` (2.x renamed FastMCP), `requests`,
`authlib` and, below Python 3.11, `tomli`. `htcondor` is not declared:
the Fermilab path reads the grid queue through prodtools, whose MCP venv
(or the cvmfs release venv below) carries the wheel matching the pool.

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

## Install from cvmfs (any host that mounts the Mu2e repo)

One release for every gpvm, no per-user install. A release directory is
the tagged tree plus a `.venv` built at its final path with the ops-019
spack python, so it resolves on any Linux host that mounts
`/cvmfs/mu2e.opensciencegrid.org` (a Mac runs it inside a Linux
container with cvmfs bind-mounted). Register:

```json
{"mcpServers": {"beamkit": {"command":
  "/cvmfs/mu2e.opensciencegrid.org/bin/beamkit/current/scripts/beamkit-mcp-cvmfs"}}}
```

`scripts/beamkit-mcp-cvmfs` sets up the Mu2e ops environment, puts the
cvmfs prodtools `current` release on `PYTHONPATH` (override with
`BEAMKIT_PRODTOOLS_ROOT`) and starts the server from the release venv;
`--check` prints the same three `OK:` lines as `start_mcp.sh`. The NERSC
path reads `$BEAMKIT_HOME/nersc.toml` exactly as on a laptop.

Publishing a release, as `cvmfsmu2e@oasiscfs.fnal.gov` (about an hour
to propagate):

```bash
bin/install_beamkit.sh -n -c 25.0 v0.3.0          # dry run: tag exists, path free
bin/install_beamkit.sh -c 25.0 v0.3.0             # transaction, venv, current ->, publish
```

`-c` is the pool's htcondor major.minor (`condor_version` on a gpvm);
`-N` builds a NERSC-only venv without it. Rehearse without cvmfs:
`bin/install_beamkit.sh -t /some/dir -s . -c 25.0 vX.Y.Z` installs the
local checkout into `/some/dir` and `/some/dir/current/scripts/beamkit-mcp-cvmfs --check`
must pass.

## Tools

| tool | does | prodtools calls |
|---|---|---|
| `run_beamline(tag, deck_ref, run_as, params=None, events_per_job=1000, njobs=1, main_input="Mu2E.in", outloc="scratch", dsconf=None, slice_size=None, submit=True, confirm=False, deck_dir=None)` | materialize deck, allocate dsconf, write `entry.json` + `run.json`, create the campaign, and (if `submit`) fire the first tick, which submits the whole run when `njobs <= 10000` | `push_cnf`, then `run_submissions(campaign_id=...)` |
| `make_recoveries(run_id, run_as, confirm=False)` | one prodtools tick: verify finished jobs, resubmit the missing indices, and submit any slice of this run not yet submitted (`njobs > 10000` only). Nothing advances on its own; a run with no failures never needs this call. Scoped to the campaign while it is `active`; once prodtools marks it `complete` (every slice submitted, rows still verifying) the **bare tick** runs instead, and the result says so. **Ledger-wide:** the verify/recovery pass always covers every active campaign in the caller's ledger, and the bare tick's top-up does too, so under `run_as="mu2epro"` it advances production campaigns. | `list_campaigns`, then `run_submissions(campaign_id=...)` or `run_submissions()` |
| `beamline_status(run_id)` | run record merged with live campaign state (queue, outputs); `mine=True` for self runs | `campaign_status` |
| `list_beamline_runs(state=None)` | run records under the caller's beamkit dir, newest first | — |
| `beamline_outputs(run_id)` | files of `nts.<owner>.<desc>.<dsconf>.root` with sizes and dCache paths | `utils.samweb_wrapper.file_sizes_in_dataset`, `utils.file_resolver.dataset_dir`, `utils.job_common.Mu2eName` |
| `make_beamfile(run_id, flavor, run_as, plane="Z3712", cuts=None, publish=False, location=None, confirm=False, label=None)` | build a BLTrackFile beam file from a run's ntuples with a preset or caller-supplied cut table; `label` names the files and defaults to the flavor; optionally publish it to SAM with the ntuples as parents | `utils.samweb_wrapper.file_sizes_in_dataset` / `utils.file_resolver.dataset_dir` (inputs); `push_file` when `publish=True` |
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
  run_as, deck pin, params, campaign_id, tarball, datasets (the
  resolved `nts.<owner>.<desc>.<dsconf>.root`), ticks, prodtools
  provenance, and any beam files built from this run.
- `beamfiles/` — beam files built by `make_beamfile`
  (`<run_id>.<label>.txt` + `.json` sidecar; the label defaults to the
  flavor).

Run states: `created` (campaign exists, nothing submitted),
`submitted`, `needs_attention` (the last tick returned prodtools'
rc=2: held rows or exhausted recoveries; a clean tick returns the run
to `submitted`), and `enqueue_failed` (the push never reached
prodtools; the run dir is retried in place). A push that raised after
the cnf reached SAM and the campaign was created adopts that campaign
into the record, so `make_recoveries` submits it instead of a retry
burning the next dsconf.

## Privilege

`run_as` is required on every mutating tool and passed through
unchanged: beamkit adds no gate of its own and removes none. Everything
`run_as` implies — the owner in every name, which ledger, whether
`confirm` is needed, the default publish location, whether a dev
prodtools checkout (`BEAMKIT_PRODTOOLS_DIR`) may ship to the workers —
is decided in one module, `identity.py`.
`outloc="disk"` (`/mu2e/persistent/datasets`) needs
`run_as="mu2epro"`: no other account has `storage.modify` there, so a
self run would finish g4bl on every worker and then 403 in `pushOutput`.
It is refused up front.

`make_beamfile(publish=True)` additionally requires `run_as` to match
the run's own: the beam file is named from the record's owner and pushed
as `run_as`, so a mismatch would publish one identity's name under the
other account.

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

Any other `flavor` needs an explicit `cuts` dict:

```python
{"keep_pdg": list[int] | None,   # exclusive whitelist; None = all PDG ids
 "drop_pdg": list[int],          # blacklist; must be [] when keep_pdg is set
 "min_p_mev": dict[int, float]}  # per-PDG floor on |p| in MeV/c; rows below are dropped
```

`label` names the local files and the SAM artifact
(`etc.<owner>.<tag>Beam-<label>.<dsconf>.0.txt`) and defaults to the
flavor; a second beam file with the same cuts takes a new label, not a
new flavor. `plane` is the `NTuple/<plane>` name and is validated
before anything is read.

There is no completeness check: `pot = n_files * events_per_job` from
whatever nts files exist in SAM, and `missing_indices` records the
gap. `location` (`scratch`/`disk`/`tape`) defaults to `tape` for
`run_as="mu2epro"` and `scratch` for self, and is only used when
`publish=True`; a published file is never copied tape-to-disk
afterwards, so choose the location at publish time. Publish needs
prodtools `push_file` — without it `make_beamfile` works with
`publish=False` only, and says so.

## Testing against real prodtools

The suite fakes every prodtools symbol, so it runs anywhere. One test
holds the bridge seam honest against a real checkout and skips
otherwise:

```bash
BEAMKIT_PRODTOOLS_ROOT=/path/to/prodtools .venv/bin/python -m pytest tests/test_bridge_contract.py -q
```

It imports the real prodtools modules in a subprocess and binds every
call `bridge.py` makes to the real signature. Run it after pulling
prodtools.

## NERSC from a laptop

1. Create a NERSC Superfacility API client in Iris (Superfacility API
   Clients, "+ New Client", red level for job submission, your laptop's
   public IP in the allow list). Save the client id to `~/.sfapi/client_id`
   and the private key (PEM or JWK tab) to `~/.sfapi/priv_key.pem`, then
   `chmod 400 ~/.sfapi/priv_key.pem`.
2. `pip install beamkit` (Python 3.10+, git on PATH).
3. Write `~/.beamkit/nersc.toml`:

   ```toml
   api            = "https://api.iri.nersc.gov/api/v2"
   sfapi_dir      = "~/.sfapi"
   account        = "m4599"
   base_dir       = "/global/cfs/cdirs/m4599/Users/<nersc-login>/beamkit"
   qos            = "regular"     # "debug" for a 30-minute test
   owner          = "<nersc-login>"
   ```

   Optional: `procs_per_node` (128), `shared_qos` ("shared") and
   `shared_max_procs` (64). A full slice of 128 indices takes a whole
   node in `qos`. A smaller slice, up to `shared_max_procs`, runs in
   `shared_qos` non-exclusive and is charged per core, so a 2-job test
   or the last partial slice of a run does not bill a whole node. The
   beam-file job (one process) goes the same way. Slices between 65 and
   127 exceed the shared cap and take a whole node.

4. Register the MCP server (Claude Code `.mcp.json` or Claude Desktop):

   ```json
   {"mcpServers": {"beamkit": {"command": "beamkit-mcp"}}}
   ```

5. `get_server_info` shows `backends.nersc.available: true`. Then
   `run_beamline(tag="G4blBeam", deck_ref="<sha or tag>", run_as="self",
   site="nersc", njobs=10, events_per_job=1000)`, `beamline_status`, and
   `make_beamfile(run_id, "bm", "self", site="nersc")`.

Outputs stay on CFS under `base_dir/runs/<run_id>/out/`; the beam file
under `beamfiles/`. Nothing is declared to SAM. There is no recovery:
`beamline_status` lists missing indices; a new run replaces a short one.

## Not in v1

- Sweeps (list-valued params fanning out into N runs) and any
  surrogate-driven loop.
- Stage-2 resampling from a beam file (`run_beamline(...,
  beamfile=...)`): specified so the record schema and tool signature
  won't change later, but gated on deck and prodtools prerequisites
  that don't exist yet.
- `push_file` for anything beyond `make_beamfile(publish=True)`.
