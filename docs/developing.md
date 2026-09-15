# Developing beamkit

Users never need this; `uvx` or `pip` from the README is the install.
Dev venv on any host with Python 3.10+ (on a Mu2e host the Mu2e spack
view has one):

```bash
python3 -m venv .venv
env -u PYTHONPATH .venv/bin/pip install -e '.[dev]'
env -u PYTHONPATH .venv/bin/python -m pytest -q -p no:cacheprovider
```

`env -u PYTHONPATH` matters on a host whose shell loads the Mu2e ops
spack environment: its `PYTHONPATH` shadows the venv. Runtime
dependencies are `mcp<2` (2.x renamed FastMCP), `requests`, `authlib`
and, below Python 3.11, `tomli`. beamkit imports no prodtools code: the
Fermilab path spawns prodtools' own MCP servers (`mcp/scripts/start_write_mcp.sh`,
`mcp/scripts/start_mcp.sh`) from `BEAMKIT_PRODTOOLS_ROOT`, default the
cvmfs release `/cvmfs/mu2e.opensciencegrid.org/bin/prodtools/current`.
For a prodtools checkout, install its MCP venv once (`bash mcp/scripts/install.sh`
there) and point the variable at it; `.mcp.json` in this repo does that
for the dev server: `.venv/bin/beamkit-mcp` with `BEAMKIT_PRODTOOLS_ROOT`
in `env`. Edit both paths for another checkout.

The children start on the first Fermilab call (30 to 90 s cold for
`muse setup ops`; `BEAMKIT_PRODTOOLS_START_TIMEOUT`, default 180 s,
bounds it) and stay for the life of the server. A NERSC-only session
never starts them.

## Testing against real prodtools

The suite spawns `tests/fake_prodtools_mcp.py` as the far end, so it
runs anywhere. One test file holds the seam honest against real
prodtools and skips otherwise:

```bash
BEAMKIT_PRODTOOLS_ROOT=/path/to/prodtools env -u PYTHONPATH .venv/bin/python -m pytest -q tests/test_bridge_contract.py
```

It starts the two real servers through their launchers and checks every
argument `bridge.py` sends against the advertised tool schemas, and runs
`tests/_bridge_contract_probe.py` for the facts beamkit copies from
prodtools (g4bl worker params, outloc vocabulary, dot-name grammar, the
g4bl recipe). Run it after pulling prodtools.

## Releasing

Bump `version` in `pyproject.toml` and `__version__` in
`src/beamkit/__init__.py` (a test pins them equal), commit, tag `vX.Y.Z`,
push the tag. Users pin the tag in their `uvx` line. Nothing is built or
published anywhere else.
