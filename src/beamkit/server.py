"""FastMCP wiring for beamkit: registers tools.py's functions as they are."""
import inspect
import logging
import os
import sys

from beamkit import records, tools

INSTRUCTIONS = """
beamkit: G4beamline production over the Mu2e prodtools g4bl runner.
run_beamline pins a Mu2e/G4BeamlineScripts commit, allocates desc=tag and
dsconf=<sha7>, and submits the run in one tick when njobs <= 10000.
Nothing advances on its own: every further pass is a make_recoveries call.
run_as="self" writes only your own scratch, datasets and ledger;
run_as="mu2epro" writes production SAM and submits production grid jobs and
is refused unless confirm=true. make_recoveries' verify/recovery pass is
LEDGER-WIDE: prodtools scopes only the top-up to this campaign, so under
run_as="mu2epro" it also recovers every other active production campaign.
site="nersc" runs on Perlmutter through the IRI API ($BEAMKIT_HOME/nersc.toml,
run_as="self" only): outputs stay on CFS and there is no recovery, so
submit_run feeds what a partial submit missed and fetch_outputs copies at
most 5 MB per file. Records live under BEAMKIT_HOME/runs/<run_id>/; a run's
state, which list_beamline_runs filters on, is one of @@STATES@@.
""".replace("@@STATES@@", "/".join(records.STATES))

TOOLS = ("run_beamline", "make_recoveries", "submit_run", "beamline_status", "list_beamline_runs",
         "beamline_outputs", "fetch_outputs", "make_beamfile", "get_server_info")


def create_mcp_server():
    """The tools.py functions ARE the MCP tools: FastMCP builds each schema
    from the function's annotations (an unannotated parameter would reach the
    client as a string) and its description from the docstring."""
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
