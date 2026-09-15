# beamkit

G4beamline production for Mu2e, driven from Claude (or any MCP client).
One server, eight tools: pin a deck, run it as N jobs on the Fermilab grid
or on NERSC Perlmutter, watch it, collect the outputs, build a beam file.
On Fermilab every write goes through prodtools with its gates intact; on
NERSC nothing but your own NERSC account is involved.

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

Then ask in plain words:

| you say | tool |
|---|---|
| "Is beamkit working?" | `get_server_info` |
| "Run 100 g4bl jobs of 1000 events from deck tag v3 on NERSC" | `run_beamline(tag="G4blBeam", deck_ref="v3", run_as="self", site="nersc", njobs=100, events_per_job=1000, params={"epsMax": "0.01"})` |
| "Same on the grid" | the same call without `site` |
| "How is it doing?" / "Where are the outputs?" | `beamline_status(run_id)`, `beamline_outputs(run_id)` |
| "Copy the NERSC outputs here" | `fetch_outputs(run_id, dest="/exp/mu2e/data/users/$USER/pull")` (API, 5 MB per file; bigger files go by Globus or scp) |
| "Build the bm beam file" | `make_beamfile(run_id, "bm", "self", site="nersc")` |

`run_as="self"` is always safe. `params={"epsMax": "0.01"}` is needed
until G4BeamlineScripts carries that parameter itself.

## Read more

- [docs/nersc.md](docs/nersc.md): NERSC setup, `nersc.toml` keys, the
  two transports (IRI v2, Superfacility v1.2), `scripts/iri-ping`.
- [docs/tools.md](docs/tools.md): every tool's signature, naming,
  records, `run_as` and privilege, recoveries, beam-file cuts.
- [docs/architecture.md](docs/architecture.md): modules and data flow.
- [docs/developing.md](docs/developing.md): dev venv, test suite, the prodtools contract test.
- [docs/specs/](docs/specs/): the design documents.
