# beamkit architecture

beamkit is a thin client. It owns no queue, no catalog, and no submission
state — prodtools owns all three. What beamkit adds is the layer prodtools
does not have for G4beamline: a reproducible pin of the deck, a name derived
from that pin, an entry JSON composed from it, and a beam-file builder for
the ntuples the run produces.

Everything in `src/beamkit/` is one of four things: a rule (identity, naming,
compose), a seam (bridge, server), a store (paths, records), or a payload
transformer (decks, beamfile, publishing, `_read_plane`). `tools.py` is the
only module that orchestrates; the rest are leaves it calls.

## The layers

```mermaid
graph TB
    subgraph clients["Client"]
        MCP["MCP client<br/>(Claude, editor, CLI)"]
    end

    subgraph beamkit["beamkit"]
        SRV["<b>server.py</b><br/>FastMCP wiring — registers tools.py's<br/>functions as they are. Imports mcp only<br/>inside create_mcp_server()."]
        TOOLS["<b>tools.py</b><br/>The seven tools. Orchestration only:<br/>validate, then call the leaves in order."]

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
            REC["<b>records.py</b><br/>runs/&lt;run_id&gt;/run.json —<br/>only what prodtools does not know"]
        end

        BRG["<b>bridge.py</b><br/>the ONLY module that imports prodtools.<br/>Every import is inside a function, so beamkit<br/>and its whole test suite run with prodtools absent."]
    end

    subgraph ext["Outside"]
        PT["prodtools<br/>json2jobdef · submissions ledger · runmu2e"]
        SAM["SAM catalog"]
        GRID["HTCondor / jobsub"]
        GIT["github.com/Mu2e/G4BeamlineScripts"]
    end

    MCP --> SRV --> TOOLS
    TOOLS --> IDN & NAM & CMP & DEK & BF & PUB & REC & PTH
    TOOLS --> BRG
    PUB --> BRG
    BF -.->|subprocess| RP
    DEK -.->|git| GIT
    BRG --> PT
    PT --> SAM & GRID

    classDef boundary fill:#fff3cd,stroke:#b8860b,stroke-width:2px
    classDef external fill:#eee,stroke:#888
    class SRV,BRG boundary
    class PT,SAM,GRID,GIT external
```

Two boundaries carry the design (shaded above). `server.py` is the only thing
that knows about MCP; `bridge.py` is the only thing that knows about
prodtools. Neither contains logic. Every other module can be read, tested, and
reasoned about with both prodtools and `mcp` uninstalled — which is exactly
how the test suite runs.

## What each file is for

| File | Lines | Purpose |
| --- | --- | --- |
| `server.py` | 80 | FastMCP registration: a name→description table registering all eight tools, each `tools.py` function registered as it is. tools.py annotates every parameter, because the schema is built from them. |
| `tools.py` | 346 | `run_beamline`, `make_recoveries`, `submit_run`, `beamline_status`, `list_beamline_runs`, `beamline_outputs`, `make_beamfile`, `get_server_info`. Resolves the identity and validates every input first, dispatches on `site` (`fermilab` through prodtools, `nersc` through the NERSC backend), then delegates. Raises `BeamkitError` for its own refusals and lets each module's subclass through untouched: nothing is caught only to be re-raised. |
| `bridge.py` | 101 | Lazy, in-function imports of prodtools. Converts every prodtools failure into `BridgeError`. Reads no environment: the dev checkout arrives as an argument. |
| `beamfile.py` | 231 | The cut table (`bm`/`ps` presets or a custom `{keep_pdg, drop_pdg, min_p_mev}`), label and plane validation, the dedupe and structural cuts, and the atomic BLTrackFile writer. |
| `decks.py` | 109 | Resolves a tag/branch/sha against the deck repo and materializes that commit once into a content-addressed cache. |
| `records.py` | 96 | The `RunRecord` dataclass and its atomic save/load. States: `enqueue_failed`, `created`, `submitted`, `needs_attention`, `partially_submitted`, `short`, `complete`. prodtools' ledger is the system of record for submission state on the Fermilab path; the last three states are NERSC-only, computed from Slurm and `out/` since there is no ledger there. |
| `identity.py` | 79 | What `run_as` means: owner, `mine`, production, confirm requirement, default publish location, and whether a dev prodtools checkout may ship. The only reader of `BEAMKIT_PRODTOOLS_DIR`. |
| `compose.py` | 72 | `validate_inputs`, the one rule for every caller-supplied value, and the single-entry JSON `json2jobdef` consumes. |
| `naming.py` | 68 | Every Mu2e name beamkit produces: run id, cnf, nts dataset, beam-file artifact, and the `-NNN` suffix rule when a cnf name is already taken in SAM. |
| `publishing.py` | 49 | The publish step of `make_beamfile`: knowable-up-front preconditions, hard link to the SAM name, push, and an unwind that discards only what this call created. Tested against a fake push with nothing built. |
| `_read_plane.py` | 40 | Prints one ntuple plane as TSV. Runs under a *different* interpreter (ana 2.8.0, for uproot) and imports nothing from beamkit. |
| `paths.py` | 23 | `$BEAMKIT_HOME` and the three directories under it. |
| `iri.py` | 173 | Thin client for the IRI Facility API v2 as NERSC serves it: paths in, parsed JSON out, `IriError` on anything that is not success. Knows nothing about beamkit runs; takes NERSC Superfacility API client credentials (Globus tokens are rejected by v2). |
| `nersc_config.py` | 100 | Loads `$BEAMKIT_HOME/nersc.toml` — everything the NERSC backend needs to know about the facility and the caller's client — per tool call, refusing a missing or malformed file with the full key list. |
| `nersc_cnf.py` | 55 | Builds the cnf tarball for a NERSC run on the caller's machine: the deck without VCS internals plus a `jobpars.json` in the shape prodtools' `json2jobdef._build_g4bl_tarball` writes, so a later harvest can declare it as the parent of every nts file unchanged. |
| `nersc_templates.py` | 95 | Fills in the `@@NAME@@` placeholders of the files beamkit puts on a Perlmutter node; the g4bl lines are prodtools' `utils.runmu2e._g4bl_script` reproduced here because the NERSC path runs no prodtools on the node. |
| `templates/` | 106 | The four rendered files: `job.sh` (enter the Mu2e EL9 image via apptainer), `inner.sh` (per-index g4bl run, log opened first so 128 tasks on one node never interleave), `beamfile.sh` (enter the image to build a beam file), `beamfile_job.py` (self-contained BLTrackFile writer, using uproot from the cvmfs ana environment). |
| `backends/__init__.py` | 11 | `validate_site`: `fermilab` is the prodtools path in `tools.py`, `nersc` is `backends/nersc.py`. `tools.py` calls it once per tool invocation. |
| `backends/nersc.py` | 346 | The NERSC backend: a run is a directory on CFS plus one Slurm job per slice of `procs_per_node` indices, driven through the IRI Facility API. Touches no SAM, dCache, prodtools or ledger. |
| `__init__.py` | 8 | Version and `BeamkitError`, the base of every error beamkit raises. |

The test suite fakes every prodtools symbol, so one more test holds the
bridge seam honest: with `BEAMKIT_PRODTOOLS_ROOT` naming a checkout,
`tests/test_bridge_contract.py` imports the real modules in a subprocess and
binds every call `bridge.py` makes to the real signature. It skips otherwise.

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

- **One import point.** Only `bridge.py` imports prodtools, and only inside
  functions. This is what lets the suite run anywhere.
- **No fallbacks.** Validate at the boundary and fail loudly naming the cause.
  There is no `else:` branch anywhere that substitutes a default for a failed
  lookup.
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
