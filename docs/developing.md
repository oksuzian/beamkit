# Developing beamkit

Only for working on the code; users on cvmfs or pip never need this.
Dev venv on a Mu2e host:

```bash
/cvmfs/mu2e.opensciencegrid.org/spackages/241207/spack/var/spack/environments/ops-019/.spack-env/view/bin/python3 -m venv .venv
env -u PYTHONPATH .venv/bin/pip install -e '.[dev]'
env -u PYTHONPATH .venv/bin/python -m pytest -q
```

`env -u PYTHONPATH` matters on a host whose shell loads the Mu2e ops
spack environment: its `PYTHONPATH` shadows the venv and breaks the
`ana` python that beam-file tests spawn. Runtime dependencies are
`mcp<2` (2.x renamed FastMCP), `requests`, `authlib` and, below Python
3.11, `tomli`. `htcondor` is not declared: the Fermilab path reads the
grid queue through prodtools, whose MCP venv (or the cvmfs release venv)
carries the wheel matching the pool.

Running the server from a checkout needs `BEAMKIT_PRODTOOLS_ROOT` set to
a prodtools checkout whose `mcp/.venv` is installed (see prodtools'
`mcp/scripts/install.sh`). The launcher refuses to start without it:

```bash
BEAMKIT_PRODTOOLS_ROOT=/path/to/prodtools scripts/start_mcp.sh --check
```

`--check` builds the server, confirms every advertised tool is
registered, and imports prodtools through `bridge.prodtools_info()`,
three `OK:` lines on success. For another checkout, edit the `command`
and `env.BEAMKIT_PRODTOOLS_ROOT` paths in `.mcp.json`; it is checked in
with this personal checkout's absolute paths.

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
