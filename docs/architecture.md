# beamkit architecture

beamkit is a thin client. It owns no queue, no catalog, and no submission
state — prodtools owns all three. What beamkit adds is the layer prodtools
does not have for G4beamline: a reproducible pin of the deck, a name derived
from that pin, an entry JSON composed from it, and a beam-file builder for
the ntuples the run produces.

Everything in `src/beamkit/` is one of four things: a rule (naming, compose),
a boundary (bridge, server), a store (paths, records), or a payload
transformer (decks, beamfile, `_read_plane`). `tools.py` is the only module
that orchestrates; the rest are leaves it calls.

## The layers

```mermaid
graph TB
    subgraph clients["Client"]
        MCP["MCP client<br/>(Claude, editor, CLI)"]
    end

    subgraph beamkit["beamkit"]
        SRV["<b>server.py</b><br/>FastMCP wiring — seven tool wrappers.<br/>No logic. Imports mcp only inside<br/>create_mcp_server()."]
        TOOLS["<b>tools.py</b><br/>The seven tools. Orchestration only:<br/>validate, then call the leaves in order."]

        subgraph rules["Rules — pure, no I/O"]
            NAM["<b>naming.py</b><br/>desc = tag, dsconf = deck sha[:7],<br/>cnf name, owner, -NNN on collision"]
            CMP["<b>compose.py</b><br/>the one-entry JSON<br/>json2jobdef consumes"]
        end

        subgraph payload["Payload"]
            DEK["<b>decks.py</b><br/>pin + materialize one<br/>G4BeamlineScripts commit"]
            BF["<b>beamfile.py</b><br/>cut table + BLTrackFile writer<br/>(MakeSource.py as a library)"]
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
    TOOLS --> NAM & CMP & DEK & BF & REC & PTH
    TOOLS --> BRG
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
| `server.py` | 103 | FastMCP registration for the seven tools. Hand-written wrappers whose signatures are held to `tools.py`'s by an AST test. |
| `tools.py` | 321 | `run_beamline`, `make_recoveries`, `beamline_status`, `list_beamline_runs`, `beamline_outputs`, `make_beamfile`, `get_server_info`. Validates up front, then delegates. |
| `bridge.py` | 98 | Lazy, in-function imports of prodtools, keyed off `BEAMKIT_PRODTOOLS_ROOT`. Converts every prodtools failure into `BridgeError`. |
| `beamfile.py` | 231 | The cut table (`bm`/`ps` presets or a custom `{keep_pdg, drop_pdg, min_p_mev}`), the dedupe and structural cuts, and the atomic BLTrackFile writer. |
| `decks.py` | 108 | Resolves a tag/branch/sha against the deck repo and materializes that commit once into a content-addressed cache. |
| `records.py` | 86 | The `RunRecord` dataclass and its atomic save/load. Deliberately thin: prodtools' ledger is the system of record for submission state. |
| `naming.py` | 60 | Every Mu2e name beamkit produces, and the `-NNN` suffix rule when a cnf name is already taken in SAM. |
| `compose.py` | 62 | Builds and validates the single-entry JSON `json2jobdef` consumes. |
| `_read_plane.py` | 40 | Prints one ntuple plane as TSV. Runs under a *different* interpreter (ana 2.8.0, for uproot) and imports nothing from beamkit. |
| `paths.py` | 23 | `$BEAMKIT_HOME` and the three directories under it. |
| `__init__.py` | 2 | Version. |

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
    Note over T: validate FIRST — run_as/confirm,<br/>outloc vs run_as, slice cap 10000.<br/>No dsconf burned, no record written.
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
production campaign.

## Building a beam file

```mermaid
graph LR
    A["make_beamfile(run_id, flavor)"] --> B{"validate:<br/>flavor, location,<br/>identity, push_file"}
    B -->|refused| X["ToolError —<br/>nothing read,<br/>nothing written"]
    B -->|ok| C["bridge.dataset_files<br/>→ whatever nts exist"]
    C --> D["iter_plane_rows<br/>subprocess: ana python"]
    D --> E["filter_rows<br/>cuts + dedupe"]
    E --> F["write_rows<br/>atomic .part → rename"]
    F --> G["sidecar .json<br/>+ appended to run record"]
    G --> H{"publish?"}
    H -->|yes| I["hard link as<br/>etc.&lt;owner&gt;.&lt;tag&gt;Beam-&lt;flavor&gt;.&lt;dsconf&gt;.txt<br/>→ bridge.push_file"]
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
