# The cvmfs release

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
bin/install_beamkit.sh -n -c 25.0 v0.3.1          # dry run: tag exists, path free
bin/install_beamkit.sh -c 25.0 v0.3.1             # transaction, venv, current ->, publish
```

`-c` is the pool's htcondor major.minor (`condor_version` on a gpvm);
`-N` builds a NERSC-only venv without it. Rehearse without cvmfs:
`bin/install_beamkit.sh -t /some/dir -s . -c 25.0 vX.Y.Z` installs the
local checkout into `/some/dir` and `/some/dir/current/scripts/beamkit-mcp-cvmfs --check`
must pass.
