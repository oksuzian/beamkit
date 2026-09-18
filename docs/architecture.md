# beamkit architecture

beamkit is a thin client. It owns no queue, no catalog, and no submission
state — prodtools owns all three. What beamkit adds is the layer prodtools
does not have for G4beamline: a reproducible pin of the deck, a name derived
from that pin, an entry JSON composed from it, and a beam-file builder for
the ntuples the run produces.

Everything in `src/beamkit/` is one of five things: a rule (identity,
naming, compose), a ritual (`runs.py`, run creation), a seam (bridge,
server, iri), a store (paths, records), or a payload transformer (decks,
beamfile, publishing, `_read_plane`, nersc_cnf, nersc_templates).
`tools.py` knows no site: every tool loads the record (or takes the
caller's `site`), asks `backends.get(site)` for the backend, and
delegates. `backends/fermilab.py` and `backends/nersc.py` are two peers
of one interface (`docs/specs/2026-09-17-backend-seam-design.md` §6):
each supplies `runs.create`'s eight hooks plus its own body for every
other tool, the first driven through `bridge.py` against prodtools, the
second through `iri.py` against the Superfacility API.

## The layers

```mermaid
graph TB
    subgraph clients["Client"]
        MCP["MCP client<br/>(Claude, editor, CLI)"]
    end

    subgraph beamkit["beamkit"]
        SRV["<b>server.py</b><br/>FastMCP wiring — registers tools.py's<br/>functions as they are. Imports mcp only<br/>inside create_mcp_server()."]
        TOOLS["<b>tools.py</b><br/>The eight tools. Orchestration only:<br/>validate the site, build a RunRequest or<br/>load the record, delegate to backends.get(site)."]
        RUNS["<b>runs.py</b><br/>Run creation, once (§5.2): validate, allocate<br/>the dsconf, save the record, enqueue, submit.<br/>The backend supplies eight hooks."]

        subgraph rules["Rules — pure, no I/O"]
            IDN["<b>identity.py</b><br/>what run_as means: owner, ledger,<br/>confirm, what may ship"]
            NAM["<b>naming.py</b><br/>every Mu2e name: run id, cnf,<br/>nts dataset, beam-file artifact"]
            CMP["<b>compose.py</b><br/>validate every caller input, then<br/>the one-entry JSON json2jobdef consumes"]
        end

        subgraph payload["Payload"]
            DEK["<b>decks.py</b><br/>pin + materialize one<br/>G4BeamlineScripts commit"]
            BF["<b>beamfile.py</b><br/>cut table + BLTrackFile writer<br/>(MakeSource.py as a library)"]
            PUB["<b>publishing.py</b><br/>link to the SAM name, push,<br/>unwind only what this call made"]
            RP["<b>_read_plane.py</b><br/>standalone; runs under the<br/>ana interpreter, uproot only"]
        end

        subgraph store["State"]
            PTH["<b>paths.py</b><br/>$BEAMKIT_HOME layout"]
            REC["<b>records.py</b><br/>runs/&lt;run_id&gt;/run.json —<br/>site + one typed block, an older<br/>shape refused by name"]
        end

        subgraph fermilab_backend["backends/fermilab.py"]
            FL["<b>backends/fermilab.py</b><br/>runs.create's eight hooks, driven through<br/>bridge.py, plus the Fermilab body of every<br/>other tool (submit_run, make_recoveries,<br/>status, outputs, make_beamfile)"]
            BRG["<b>bridge.py</b><br/>the ONLY module that talks to prodtools:<br/>an MCP client of the two prodtools servers,<br/>spawned from BEAMKIT_PRODTOOLS_ROOT on the first Fermilab call."]
        end

        subgraph nersc_backend["backends/nersc.py"]
            NB["<b>backends/nersc.py</b><br/>runs.create's eight hooks plus the NERSC<br/>body of every other tool (submit_run, status,<br/>outputs, make_beamfile), driven through the<br/>IRI Facility API"]
            NCFG["<b>nersc_config.py</b><br/>loads + validates $BEAMKIT_HOME/nersc.toml"]
            NCNF["<b>nersc_cnf.py</b><br/>builds the cnf tarball locally,<br/>jobpars shaped like json2jobdef's"]
            NTPL["<b>nersc_templates.py</b><br/>fills job.sh / inner.sh / beamfile.sh —<br/>carries prodtools' g4bl recipe, checked<br/>byte-equal by the contract probe"]
            IRI["<b>iri.py</b><br/>thin client for the IRI Facility API:<br/>paths in, parsed JSON out, IriError otherwise"]
        end
    end

    subgraph ext["Outside"]
        PT["prodtools<br/>json2jobdef · submissions ledger · runmu2e"]
        SAM["SAM catalog"]
        GRID["HTCondor / jobsub"]
        GIT["github.com/Mu2e/G4BeamlineScripts"]
        IRIAPI["api.iri.nersc.gov<br/>Superfacility API v2 — compute + CFS"]
    end

    MCP --> SRV --> TOOLS
    TOOLS --> IDN & REC & PTH & DEK
    TOOLS -.->|get_server_info| BRG & NCFG
    TOOLS --> RUNS
    TOOLS -->|backends.get site| FL & NB
    RUNS --> NAM & CMP & DEK & PTH & REC
    RUNS -->|backends.get site| FL & NB
    FL --> BF & PUB & CMP & IDN & NAM & PTH & REC
    FL --> BRG
    NB --> BF & IDN & NAM & PTH & REC
    NB --> NCFG & NCNF & NTPL & IRI
    PUB --> BRG
    BF -.->|subprocess| RP
    DEK -.->|git| GIT
    BRG --> PT
    PT --> SAM & GRID
    IRI --> IRIAPI

    classDef boundary fill:#fff3cd,stroke:#b8860b,stroke-width:2px
    classDef external fill:#eee,stroke:#888
    class SRV,BRG,IRI boundary
    class PT,SAM,GRID,GIT,IRIAPI external
```

Three boundaries carry the design (shaded above). `server.py` is the only
module that *serves* MCP — it is beamkit's own MCP server, the thing a
caller like Claude Code talks to. `mcpclient.py` and `bridge.py` are MCP
*clients*: they spawn and speak to prodtools' own two MCP servers
(`prodtools`, `prodtools-write`) over stdio, the same relationship beamkit's
caller has to `server.py`, one level down. `iri.py` and `sfapi.py` are the
only modules that know the NERSC Superfacility API. None of these contains
logic. Every other module can be read, tested, and reasoned about with
prodtools and the network absent — which is exactly how the test suite
runs; its far end is `tests/fake_prodtools_mcp.py`, a stand-in for both
prodtools servers that needs only `mcp`.

## What each file is for

| File | Purpose |
| --- | --- |
| `server.py` | FastMCP registration: a tuple of the nine tool names, each `tools.py` function registered as it is. tools.py annotates every parameter, because the schema is built from them, and each tool's description is the function's own docstring. The instructions spell the state vocabulary from `records.STATES`. |
| `tools.py` | `run_beamline`, `make_recoveries`, `submit_run`, `beamline_status`, `list_beamline_runs`, `beamline_outputs`, `fetch_outputs`, `make_beamfile`, `get_server_info`. Validates the `site` argument where a tool takes one, builds a `RunRequest` (`run_beamline`, handed whole to `runs.create`) or loads the record from disk, then asks `backends.get(site)` for the backend and delegates. Raises `BeamkitError` for its own refusals and lets each module's subclass through untouched: nothing is caught only to be re-raised. |
| `runs.py` | `RunRequest` and `create(req)`: the one creation ritual (spec §5.2) — resolve identity, validate the tag and the caller's inputs, let the backend validate and fill its own defaults, pin the deck, allocate the dsconf, claim the run dir, save the record, enqueue (annotating and re-raising on failure), check `njobs` against what the site reports, submit if asked. The only writer of `state="created"` and `"enqueue_failed"`; the site's part is the backend's eight hooks. |
| `bridge.py` | MCP client of prodtools' write and read servers (`mcpclient.StdioServer` per child, lazy, kept for the process life). Converts every prodtools failure into `BridgeError`: isError text from the write server, the `{"error": ...}` envelope from the read server. Reads one environment variable, `BEAMKIT_PRODTOOLS_ROOT`; the dev checkout to ship arrives as an argument. |
| `mcpclient.py` | A synchronous handle on one MCP server over stdio: private event loop in a daemon thread, one serve task for the session's life, stderr tail for start failures, respawn after a child dies. Knows nothing about prodtools. |
| `beamfile.py` | The cut table (`bm`/`ps` presets or a custom `{keep_pdg, drop_pdg, min_p_mev}`), label and plane validation, the dedupe and structural cuts, and the atomic BLTrackFile writer. |
| `decks.py` | Resolves a tag/branch/sha against the deck repo and materializes that commit once into a content-addressed cache. |
| `records.py` | The `RunRecord` dataclass — one typed site block (`backends.block_type(site)`) instead of Fermilab fields on every record and an untyped `nersc` dict — and its atomic save/load. `to_dict()` emits the block under the site's own key, `"fermilab"` or `"nersc"`, never both; `from_dict` refuses an older shape (a legacy top-level key, a `nersc` block without `site == "nersc"`, or no block at all), naming the file and the version instead of converting it. States: `enqueue_failed`, `created`, `submitted`, `needs_attention`, `partially_submitted`, `short`, `complete`. prodtools' ledger is the system of record for submission state on the Fermilab path; the last three states are NERSC-only, computed from Slurm and `out/` since there is no ledger there. |
| `identity.py` | What `run_as` means: owner, `mine`, production, confirm requirement, default publish location, and whether a dev prodtools checkout may ship. The only reader of `BEAMKIT_PRODTOOLS_DIR`. |
| `compose.py` | `validate_inputs`, the one rule for every caller-supplied value, and the single-entry JSON `json2jobdef` consumes. |
| `naming.py` | Every Mu2e name beamkit produces: run id, cnf, nts dataset, beam-file artifact, and the `-NNN` suffix rule when a cnf name is already taken in SAM. |
| `publishing.py` | The publish step of `make_beamfile`: knowable-up-front preconditions, hard link to the SAM name, push, and an unwind that discards only what this call created. Tested against a fake push with nothing built. |
| `_read_plane.py` | Prints one ntuple plane as TSV. Runs under a *different* interpreter (ana 2.8.0, for uproot) and imports nothing from beamkit. |
| `paths.py` | `$BEAMKIT_HOME` and the three directories under it. |
| `iri.py` | Thin client for the IRI Facility API v2 as NERSC serves it: paths in, parsed JSON out, `IriError` on anything that is not success. Knows nothing about beamkit runs; takes NERSC Superfacility API client credentials (Globus tokens are rejected by v2). |
| `nersc_config.py` | Loads `$BEAMKIT_HOME/nersc.toml` — everything the NERSC backend needs to know about the facility and the caller's client — refusing a missing or malformed file with the full key list. `api` is required on the `iri` transport only. The backend caches the loaded config and its client per (path, mtime), so one server process reads it once and keeps one authenticated session. |
| `nersc_cnf.py` | Builds the cnf tarball for a NERSC run on the caller's machine: the deck without VCS internals plus a `jobpars.json` in the shape prodtools' `json2jobdef._build_g4bl_tarball` writes, so a later harvest can declare it as the parent of every nts file unchanged. |
| `nersc_templates.py` | Fills in the `@@NAME@@` placeholders of the files beamkit puts on a Perlmutter node; the g4bl lines are prodtools' `utils.runmu2e._g4bl_script` reproduced here because the NERSC path runs no prodtools on the node. |
| `templates/` | The four rendered files: `job.sh` (enter the Mu2e EL9 image via apptainer), `inner.sh` (per-index g4bl run, log opened first so 128 tasks on one node never interleave), `beamfile.sh` (enter the image to build a beam file), `beamfile_job.py` (self-contained BLTrackFile writer, using uproot from the cvmfs ana environment). |
| `backends/__init__.py` | `SITES` (`"fermilab"`, `"nersc"`); `get(site)`, which imports the named backend module on first use and refuses an unknown site; `block_type(site)` (`get(site).Block`), the late import `records.from_dict` uses so `records.py` names a block type without an import cycle. |
| `backends/fermilab.py` | The Fermilab backend: `runs.create`'s eight hooks (`resolve_identity`, `validate`, `taken`, `retryable`, `new_block`, `enqueue`, `after_failure`, `submit`) plus the Fermilab body of every other tool (`submit_run`, `make_recoveries`, `status`, `outputs`, `make_beamfile`) and `available()`. `bridge.py` is reached from here, from `publishing.py`, and from `tools.py`'s own `get_server_info` probe — nowhere else imports it. |
| `backends/nersc.py` | The NERSC backend: a run is a directory on CFS plus one Slurm job per slice of `procs_per_node` indices, driven through the IRI Facility API. Supplies the same eight hooks as `backends/fermilab.py` plus the NERSC body of every other tool. Touches no SAM, dCache, prodtools or ledger. |
| `__init__.py` | Version and `BeamkitError`, the base of every error beamkit raises. |

Line counts are not tracked here: they drift every time a fix lands and a
stale number is worse than none. `git ls-files 'src/beamkit/**/*.py' | xargs
wc -l` gets the current ones.

The test suite spawns a fake prodtools MCP server as the far end, so one more
test holds the seam honest: with `BEAMKIT_PRODTOOLS_ROOT` naming a checkout
with its MCP venv installed, `tests/test_bridge_contract.py` starts the real
prodtools servers and checks every argument `bridge.py` sends against their
tool schemas, plus the facts beamkit copies. It skips otherwise.

## Submitting a run

```mermaid
sequenceDiagram
    participant U as Client
    participant T as tools.run_beamline
    participant D as decks
    participant N as naming
    participant C as compose
    participant B as bridge
    participant P as prodtools

    U->>T: tag, deck_ref, run_as, njobs…
    Note over T: identity.resolve, then compose.validate_inputs,<br/>outloc vs identity, slice cap 10000.<br/>No deck fetched, no dsconf burned, no record written.
    T->>D: resolve_ref + materialize
    D-->>T: DeckPin(sha, dir)
    T->>N: allocate_dsconf(sha[:7])
    N->>B: cnf_exists(cnf name)?
    B->>P: SAM lookup
    N-->>T: dsconf (or -001 on collision)
    T->>C: entry(tag, dsconf, deck_dir, …)
    C-->>T: entry.json
    T->>B: push_cnf(entry.json, …)
    B->>P: json2jobdef --prod --enqueue
    P-->>B: tarball, campaign_id, njobs
    T->>B: tick (run_submissions)
    B->>P: submit slice
    T-->>U: run_id, dsconf, campaign_id, datasets, state
```

The order is deliberate. Everything that can be refused is refused before the
deck is materialized; the dsconf is allocated only once the run is going to
happen; and the record is written before the tick, so a submission that dies
mid-flight still leaves something to recover from.

Nothing advances on its own afterwards. `make_recoveries` runs one prodtools
tick — verify, resubmit missing indices, feed the next slice — and that pass is
ledger-wide, so under `run_as="mu2epro"` it also advances every other active
production campaign. While the campaign is `active` the tick is scoped to it.
prodtools marks a campaign `complete` the moment its last slice is submitted,
rows still verifying, and refuses a scoped tick on it; from then on
`make_recoveries` runs the bare tick, whose top-up also feeds every other
active campaign, and the result says which form ran. A tick that returns
prodtools' "needs attention" puts the run in state `needs_attention` until a
clean one returns it to `submitted`.

`run_beamline` is not atomic across its two prodtools calls, and says what a
failure left behind. A push that raised after the cnf reached SAM and the
campaign was created adopts that campaign into the record (state `created`)
so `make_recoveries` submits it; a push that never reached SAM leaves the
dsconf free and the run dir retryable in place.

## Submitting a run at NERSC

```mermaid
sequenceDiagram
    participant C as Claude (laptop)
    participant B as beamkit tools
    participant N as backends/nersc
    participant I as api.iri.nersc.gov
    participant P as Perlmutter node
    C->>B: run_beamline(tag, deck_ref, run_as="self", site="nersc", njobs)
    B->>N: run_beamline(...)
    N->>N: load nersc.toml, resolve identity, validate, pin deck
    N->>I: ls runs/<tag>.<sha7>  (dsconf collision probe)
    N->>N: build cnf tarball, render job.sh + inner.sh, save record (created)
    N->>I: mkdir run dir, out/, slurm/, beamfiles/; upload cnf, job.sh, inner.sh
    loop one Slurm job per procs_per_node indices
        N->>I: POST /compute/job (BK_OFFSET=k*procs_per_node)
        I-->>N: slurm id  (record: jobs[])
    end
    I->>P: srun job.sh x count
    P->>P: inner.sh: log, tar xf cnf, spack load g4beamline, g4bl, mv nts to out/
    C->>B: beamline_status(run_id)
    B->>N: status(rec)
    N->>I: GET /compute/status per job; ls out/
    N-->>C: jobs, expected/nts/logs/missing, state submitted|short|complete
```

There is no prodtools call anywhere in this path: `iri.py` talks to the
Superfacility API directly, the cnf tarball is built and uploaded from the
caller's machine, and the run directory on CFS — not a SAM dataset, not a
ledger row — is the only record of what ran until `beamline_status` reads it
back. `run_beamline(..., submit=False)` stops after the layout and cnf
upload; `submit_run` fires the Slurm jobs a later, separate call.

## Building a beam file

```mermaid
graph LR
    A["make_beamfile(run_id, flavor, label)"] --> B{"validate:<br/>identity, flavor, label, plane,<br/>location, push_file"}
    B -->|refused| X["BeamkitError —<br/>nothing read,<br/>nothing written"]
    B -->|ok| C["bridge.dataset_files<br/>→ whatever nts exist"]
    C --> D["iter_plane_rows<br/>subprocess: ana python"]
    D --> E["filter_rows<br/>cuts + dedupe"]
    E --> F["write_rows<br/>atomic .part → rename"]
    F --> G["sidecar .json<br/>+ appended to run record"]
    G --> H{"publish?"}
    H -->|yes| I["publishing.publish: hard link as<br/>etc.&lt;owner&gt;.&lt;tag&gt;Beam-&lt;label&gt;.&lt;dsconf&gt;.txt<br/>→ bridge.push_file, unwind on failure"]
```

A beam file is always built from whatever the run has produced so far — it
never waits for completeness. `pot = n_files × events_per_job`, and the
indices that are missing are recorded in the sidecar rather than treated as an
error. Every refusal above happens before the first ROOT file is opened,
because on a large run that read is hours long.

## The rules that shaped it

- **One ritual.** Run creation exists once, in `runs.create`; a site supplies
  eight hooks (`resolve_identity`, `validate`, `taken`, `retryable`,
  `new_block`, `enqueue`, `after_failure`, `submit`) and nothing else decides
  the order. A refused call burns no dsconf and writes no record; the record
  is saved before the first remote effect; an enqueue that fails leaves
  `enqueue_failed` with the backend's own account of what it left behind.
- **One import point.** Only `bridge.py` talks to prodtools, over MCP, so
  beamkit's interpreter carries none of prodtools' environment. This is what
  lets the suite run anywhere. `bridge.py` itself is imported by
  `backends/fermilab.py` (the campaign ritual), `publishing.py` (`push_file`),
  and `tools.py` (`get_server_info`'s own availability probe) — nowhere else
  needs prodtools directly.
- **No fallbacks.** Validate at the boundary and fail loudly naming the cause.
  The one deliberate exception is `backends/nersc._nts_entries`, which reads a
  missing CFS `out/` (a run that failed before its remote layout was ever
  created) as zero files rather than an error, the same way `client.exists()`
  treats it — a ruling, not a silent default. Both `out_counts` and
  `outputs()` read that one listing, so both answer "no outputs yet".
- **Two identities.** `run_as="self"` touches only your own scratch, datasets
  and ledger. `run_as="mu2epro"` writes production SAM and submits production
  jobs, and is refused without `confirm=True` — here, and again inside
  prodtools.
- **prodtools owns submission state.** The run record holds the pin, the
  composed parameters and the beam files; it does not duplicate the ledger.
- **One reader per fact.** `run_as` is interpreted in `identity.py`, every
  Mu2e name is spelled in `naming.py`, every caller input is checked in
  `compose.validate_inputs`, and `BEAMKIT_PRODTOOLS_DIR` is read in one
  function. A rule that needs a second home is a rule that will drift.
- **No recovery on the NERSC path.** Status reports, a new run replaces a
  short one. The Fermilab path keeps prodtools' recovery.
- **Fermilab services are a plugin.** The NERSC path imports nothing from
  prodtools and touches no SAM, dCache or ledger; harvest to them is a
  later, optional step.
