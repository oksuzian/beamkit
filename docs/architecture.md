# beamkit architecture

beamkit is a thin client: it owns no queue, no catalog and no submission
state — prodtools owns all three. It adds the layer prodtools does not have
for G4beamline: a reproducible pin of the deck, a name derived from that pin,
an entry JSON composed from it, and a beam-file builder for the ntuples.

## The layer rule

`server.py` → `tools.py` → `runs.py` / `backends.get(site)` → `bridge.py` or
`iri.py`. Calls go down that list and never back up. `tools.py` knows no site:
it loads the record (or takes the caller's `site`), asks `backends.get(site)`
and delegates. `backends/fermilab.py` and `backends/nersc.py` are peers of one
interface — each supplies `runs.create`'s eight creation hooks
(`resolve_identity`, `validate`, `taken`, `retryable`, `new_block`,
`enqueue`, `after_failure`, `submit`) plus its own body for every other tool,
one through `bridge.py`, the other through `iri.py`.

Three boundaries carry the design: `server.py` *serves* MCP (what Claude Code
talks to), `mcpclient.py`/`bridge.py` are MCP *clients* of prodtools' two
servers over stdio, and `iri.py`/`sfapi.py` are the only modules that know the
Superfacility API. None holds logic, so every other module can be tested with
prodtools and the network absent — how the suite runs, its far end
`tests/fake_prodtools_mcp.py`.

## What each file is for

| File | Purpose |
| --- | --- |
| `server.py` | FastMCP registration: the nine tool names, each `tools.py` function registered as it is, schemas built from its annotations and descriptions from its docstring. The instructions spell the states from `records.STATES`. |
| `tools.py` | The nine tools. Orchestration only: build a `RunRequest` or load the record, then delegate to `runs.create` or `backends.get(site)`. |
| `runs.py` | `RunRequest` and `create(req)`: the one creation ritual, and the only writer of `created` and `enqueue_failed`. |
| `bridge.py` | MCP client of prodtools' write and read servers; every prodtools failure becomes `BridgeError`. Reads one variable, `BEAMKIT_PRODTOOLS_ROOT`. |
| `mcpclient.py` | A synchronous handle on one MCP server over stdio: private event loop in a daemon thread, one serve task for the session's life, stderr tail on a start failure, respawn after a child dies. |
| `identity.py` | What `run_as` means: owner, `mine`, production, the confirm requirement, and whether a dev prodtools checkout may ship. |
| `naming.py` | Every Mu2e name beamkit produces — run id, cnf, nts dataset, beam-file artifact — and the `-NNN` suffix rule on a taken cnf name. |
| `compose.py` | `validate_inputs`, the one rule for every caller-supplied value, and the single-entry JSON `json2jobdef` consumes. |
| `decks.py` | Resolves a tag/branch/sha against the deck repo and materializes that commit once into a content-addressed cache. |
| `beamfile.py` | The cut table (`bm`/`ps` presets or a custom `{keep_pdg, drop_pdg, min_p_mev}`), label and plane validation, the dedupe and structural cuts, and the atomic BLTrackFile writer. |
| `_read_plane.py` | Prints one ntuple plane as TSV. Runs under a *different* interpreter (ana 2.8.0, for uproot) and imports nothing from beamkit. |
| `records.py` | `RunRecord` and its atomic save/load. One typed site block, emitted under `"fermilab"` or `"nersc"`; `from_dict` refuses an older shape by name instead of converting it. |
| `paths.py` | `$BEAMKIT_HOME` and the three directories under it. |
| `iri.py` | Thin client for the IRI Facility API v2: paths in, parsed JSON out, `IriError` on anything else. Takes Superfacility client credentials (v2 rejects Globus tokens). |
| `sfapi.py` | The same surface over Superfacility API v1.2, for the hosts where IRI v2 is not reachable. |
| `nersc_config.py` | Loads and validates `$BEAMKIT_HOME/nersc.toml`, refusing a missing or malformed file with the full key list. |
| `nersc_cnf.py` | Builds the cnf tarball for a NERSC run locally: the deck without VCS internals plus a `jobpars.json` shaped like `json2jobdef`'s, so a later harvest can declare it as the parent unchanged. |
| `nersc_templates.py` | Fills the `@@NAME@@` placeholders of the node-side files; the g4bl lines are prodtools' `utils.runmu2e._g4bl_script` reproduced here, checked byte-equal by the contract probe. |
| `templates/` | `job.sh` (enter the Mu2e EL9 image), `inner.sh` (per-index g4bl run, log opened first so 128 tasks never interleave), `beamfile.sh`, `beamfile_job.py`. |
| `backends/__init__.py` | `SITES`, `get(site)` (imported on first use, unknown site refused) and `block_type(site)`, the late import that keeps `records.py` cycle-free. |
| `backends/fermilab.py` | The Fermilab backend: the eight hooks plus the Fermilab body of every other tool, and the publish step of `make_beamfile` (`_publish_ready`, `_publish`). Imports `bridge.py`; only `tools.get_server_info` does so besides. |
| `backends/nersc.py` | The NERSC backend: a run is a directory on CFS plus one Slurm job per `procs_per_node` indices. Same eight hooks; touches no SAM, dCache, prodtools or ledger. |
| `__init__.py` | Version and `BeamkitError`, the base of every error beamkit raises. |

`tests/test_bridge_contract.py` holds the prodtools seam honest: with
`BEAMKIT_PRODTOOLS_ROOT` naming a checkout with its MCP venv installed it starts
the real servers and checks every argument `bridge.py` sends against their
schemas, plus the copied facts. It skips otherwise.

## Creating a run

`runs.create` does exactly this; the order is the design.

1. `backend.resolve_identity` — who this runs as, `confirm` enforced.
2. `naming.validate_tag`, `compose.validate_inputs`, `backend.validate`.
3. `decks.pin` — materialize the commit; `main_input` must exist in it.
4. `naming.allocate_dsconf` — probe the site, `-NNN` only on collision.
5. `records.claim_run_dir` — a run id is never reused; a dir from an attempt
   that created nothing elsewhere is claimable in place.
6. `records.save` — the record exists before the first remote effect.
7. `backend.enqueue`; on failure the state becomes `enqueue_failed` and
   `backend.after_failure` says what was left behind.
8. `backend.submit`, when `submit=True`.

Everything refusable is refused before the deck is fetched, so a refused call
burns no dsconf and writes no record. A Fermilab push that raised after the cnf
reached SAM and the campaign was created adopts that campaign (state `created`)
so `make_recoveries` submits it; one that never reached SAM leaves the dsconf
free and the run dir retryable in place.

Nothing advances on its own. `make_recoveries` runs one prodtools tick —
verify, resubmit missing indices, feed the next slice — and that pass is
ledger-wide, so under `run_as="mu2epro"` it also advances every other active
production campaign. While the campaign is `active` the tick is scoped to it;
prodtools marks it `complete` the moment its last slice is submitted and
refuses a scoped tick, so from then on the bare tick runs and the result says
which form it was.

A beam file is always built from whatever the run has produced so far, never
waiting for completeness: `pot = n_files × events_per_job` and the missing
indices go in the sidecar rather than raising. Every refusal happens before the
first ROOT file is opened — on a large run that read is hours long.

**States.** `enqueue_failed`, `created`, `submitted`, `needs_attention` on both
paths; `partially_submitted`, `short`, `complete` are NERSC-only, computed from
Slurm and the CFS `out/` listing since there is no ledger there. A tick
returning prodtools' "needs attention" holds the run there until a clean one
returns it to `submitted`.

## The rules that shaped it

- **One ritual.** Run creation exists once, in `runs.create`; a site supplies
  eight hooks and nothing else decides the order.
- **One import point.** Only `bridge.py` talks to prodtools, over MCP, so
  beamkit's interpreter carries none of its environment — the suite runs
  anywhere.
- **No fallbacks.** Validate at the boundary and fail loudly naming the cause.
  The one deliberate exception is `backends/nersc._nts_entries`, which reads a
  missing CFS `out/` as zero files, the way `client.exists()` does.
- **Two identities.** `run_as="self"` touches only your own scratch, datasets
  and ledger; `run_as="mu2epro"` writes production SAM and submits production
  jobs, refused without `confirm=True` — here and inside prodtools.
- **prodtools owns submission state.** The record holds the pin, the composed
  parameters and the beam files; it does not duplicate the ledger.
- **One reader per fact.** `run_as` is interpreted in `identity.py`, names are
  spelled in `naming.py`, caller inputs checked in `compose.validate_inputs`,
  and `BEAMKIT_PRODTOOLS_DIR` named once as `identity.DEV_DIR_VAR`.
- **No recovery at NERSC.** Status reports; a new run replaces a short one.
- **Fermilab services are a plugin.** The NERSC path imports nothing from
  prodtools and touches no SAM, dCache or ledger; harvest is a later step.
