"""FastMCP wiring for beamkit: registers tools.py's functions as they are."""
import inspect
import logging
import os
import sys

from beamkit import records, tools

INSTRUCTIONS = """
beamkit: G4beamline production over the Mu2e prodtools g4bl runner.

run_beamline(tag, deck_ref, run_as, ...) pins a Mu2e/G4BeamlineScripts
commit or tag, allocates desc=tag and dsconf=<sha7>, registers the cnf
in SAM and creates its campaign through prodtools, and submits the whole
run in one tick when njobs <= 10000 (slice_size is capped at 10000).
Nothing advances on its own afterwards. make_recoveries(run_id, run_as)
runs one prodtools tick: verify, resubmit missing indices, and feed any
slice not yet submitted. That recovery pass is LEDGER-WIDE: prodtools
scopes only the top-up to this campaign, so under run_as="mu2epro" it
also recovers every other active production campaign.

run_as="self" writes only your own scratch, datasets and ledger.
run_as="mu2epro" writes production SAM and submits production grid jobs;
it is refused unless confirm=true, both here and inside prodtools.

make_beamfile(run_id, flavor, run_as) builds a g4bl BLTrackFile from the
nts files the run has in SAM, whatever their number: pot = n_files *
events_per_job and missing_indices are recorded. flavor "bm" or "ps"
selects a preset cut table; any other flavor needs cuts={keep_pdg,
drop_pdg, min_p_mev}. label names the files and the SAM artifact and
defaults to the flavor. publish=true pushes it to SAM via prodtools
push_file (tape for mu2epro, scratch for self) when prodtools has it.

Records live under BEAMKIT_HOME/runs/<run_id>/: a Fermilab run dir holds
entry.json and run.json; a NERSC run dir holds run.json, the cnf, job.sh,
inner.sh and beamfiles/. beamline_status merges a record with prodtools
campaign_status (site="fermilab") or Slurm job states and CFS output
counts (site="nersc").

site="nersc" (run_beamline, make_beamfile) runs on NERSC Perlmutter
through the IRI Facility API with no Fermilab service in the loop: the
cnf is built here, the run is laid out under base_dir/runs/<run_id>/ on
CFS, and one Slurm job per procs_per_node indices is submitted; outputs
stay on CFS. It needs $BEAMKIT_HOME/nersc.toml and a NERSC Superfacility
API client in sfapi_dir; run_as="self" only. There is no recovery on
nersc: beamline_status reports missing indices, a new run replaces a
short one; submit_run submits a run created with submit=false or the
jobs a partial submit did not reach. make_beamfile on a nersc run is a
second Slurm job that builds the beam file next to the nts files.
fetch_outputs(run_id, dest, kind) copies the nts files or the complete
beam files from CFS into a local directory through the API, at most
5 MB per file; a run with a larger file is refused whole and needs
Globus or scp.

A run's state, which list_beamline_runs also filters on, is one of
@@STATES@@.
""".replace("@@STATES@@", "/".join(records.STATES))

TOOLS = ("run_beamline", "make_recoveries", "submit_run", "beamline_status", "list_beamline_runs",
         "beamline_outputs", "fetch_outputs", "make_beamfile", "get_server_info")


def create_mcp_server():
    """The tools.py functions ARE the MCP tools: FastMCP builds each schema
    from the function's annotations, so tools.py annotates every parameter
    (an unannotated one would reach the client as a string), and takes each
    tool's description from the function's own docstring."""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("beamkit", instructions=INSTRUCTIONS)
    for name in TOOLS:
        fn = getattr(tools, name)
        mcp.tool(name=name, description=inspect.getdoc(fn))(fn)
    return mcp


def main():
    logging.basicConfig(level=os.environ.get("BEAMKIT_LOG_LEVEL", "INFO"), stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    create_mcp_server().run()


if __name__ == "__main__":
    main()
