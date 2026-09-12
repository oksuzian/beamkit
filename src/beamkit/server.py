"""FastMCP wiring for beamkit: registers tools.py's functions as they are."""
import logging
import os
import sys

from beamkit import tools

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

Records live under BEAMKIT_HOME/runs/<run_id>/ (entry.json, run.json);
beamline_status merges a record with prodtools campaign_status.

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
"""

TOOLS = {
    "run_beamline": "Pin a deck commit and submit the run: through prodtools (site=\"fermilab\") or as Slurm jobs on Perlmutter through the IRI API (site=\"nersc\").",
    "make_recoveries": "One prodtools tick for this run: verify, resubmit missing indices, feed unsubmitted slices. Ledger-wide recovery pass.",
    "submit_run": "NERSC runs only: submit the Slurm jobs of a run created with submit=false, or the jobs a partial submit did not reach.",
    "beamline_status": "Run record merged with campaign status: prodtools (site=\"fermilab\") or Slurm queue state (site=\"nersc\").",
    "list_beamline_runs": "Run records under this user's beamkit dir, newest first; state in enqueue_failed/created/submitted/needs_attention.",
    "beamline_outputs": "Files of the run's nts dataset with sizes and paths: dCache (site=\"fermilab\") or CFS (site=\"nersc\").",
    "make_beamfile": "Build a BLTrackFile beam file from the run's nts files, through prodtools (site=\"fermilab\") or as a Slurm job on Perlmutter (site=\"nersc\"); preset flavor bm/ps or custom cuts; label names the files (default: the flavor); optional SAM publish.",
    "get_server_info": "beamkit version, prodtools root and commit, directories, limits.",
}
TOOL_NAMES = tuple(TOOLS)


def create_mcp_server():
    """The tools.py functions ARE the MCP tools: FastMCP builds each schema
    from the function's annotations, so tools.py annotates every parameter
    (an unannotated one would reach the client as a string)."""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("beamkit", instructions=INSTRUCTIONS)
    for name, description in TOOLS.items():
        mcp.tool(name=name, description=description)(getattr(tools, name))
    return mcp


def main():
    logging.basicConfig(level=os.environ.get("BEAMKIT_LOG_LEVEL", "INFO"), stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    create_mcp_server().run()


if __name__ == "__main__":
    main()
