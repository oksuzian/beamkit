"""FastMCP wiring for beamkit. No logic here; tools.py holds it."""
import logging
import os
import sys
from typing import Optional

from beamkit import tools
from beamkit.decks import DEFAULT_DECK_URL

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
selects a preset cut table; any other label needs cuts={keep_pdg,
drop_pdg, min_p_mev}. publish=true pushes it to SAM via prodtools
push_file (tape for mu2epro, scratch for self) when prodtools has it.

Records live under BEAMKIT_HOME/runs/<run_id>/ (entry.json, run.json);
beamline_status merges a record with prodtools campaign_status.
"""

TOOL_FUNCTIONS = {
    "run_beamline": tools.run_beamline,
    "make_recoveries": tools.make_recoveries,
    "beamline_status": tools.beamline_status,
    "list_beamline_runs": tools.list_beamline_runs,
    "beamline_outputs": tools.beamline_outputs,
    "make_beamfile": tools.make_beamfile,
    "get_server_info": tools.get_server_info,
}
TOOL_NAMES = tuple(TOOL_FUNCTIONS)


def create_mcp_server():
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("beamkit", instructions=INSTRUCTIONS)

    @mcp.tool(name="run_beamline", description="Pin a deck commit, register the cnf, create the campaign and submit the run through prodtools.")
    def run_beamline(tag: str, run_as: str, deck_ref: Optional[str] = None, params: Optional[dict] = None,
                     events_per_job: int = 1000, njobs: int = 1, main_input: str = "Mu2E.in",
                     outloc: str = "scratch", dsconf: Optional[str] = None, slice_size: Optional[int] = None,
                     submit: bool = True, confirm: bool = False, deck_dir: Optional[str] = None,
                     deck_url: str = DEFAULT_DECK_URL) -> dict:
        return tools.run_beamline(tag=tag, deck_ref=deck_ref, run_as=run_as, params=params,
                                  events_per_job=events_per_job, njobs=njobs, main_input=main_input,
                                  outloc=outloc, dsconf=dsconf, slice_size=slice_size, submit=submit,
                                  confirm=confirm, deck_dir=deck_dir, deck_url=deck_url)

    @mcp.tool(name="make_recoveries", description="One prodtools tick for this run: verify, resubmit missing indices, feed unsubmitted slices. Ledger-wide recovery pass.")
    def make_recoveries(run_id: str, run_as: str, confirm: bool = False) -> dict:
        return tools.make_recoveries(run_id=run_id, run_as=run_as, confirm=confirm)

    @mcp.tool(name="beamline_status", description="Run record merged with prodtools campaign status.")
    def beamline_status(run_id: str) -> dict:
        return tools.beamline_status(run_id=run_id)

    @mcp.tool(name="list_beamline_runs", description="Run records under this user's beamkit dir, newest first; state in enqueue_failed/created/submitted.")
    def list_beamline_runs(state: Optional[str] = None) -> dict:
        return tools.list_beamline_runs(state=state)

    @mcp.tool(name="beamline_outputs", description="Files of the run's nts dataset with sizes and dCache paths.")
    def beamline_outputs(run_id: str) -> dict:
        return tools.beamline_outputs(run_id=run_id)

    @mcp.tool(name="make_beamfile", description="Build a BLTrackFile beam file from the run's nts files; preset flavor bm/ps or custom cuts; optional SAM publish.")
    def make_beamfile(run_id: str, flavor: str, run_as: str, plane: str = "Z3712",
                      cuts: Optional[dict] = None, publish: bool = False,
                      location: Optional[str] = None, confirm: bool = False) -> dict:
        return tools.make_beamfile(run_id=run_id, flavor=flavor, run_as=run_as, plane=plane, cuts=cuts,
                                   publish=publish, location=location, confirm=confirm)

    @mcp.tool(name="get_server_info", description="beamkit version, prodtools root and commit, directories, limits.")
    def get_server_info() -> dict:
        return tools.get_server_info()

    return mcp


def main():
    logging.basicConfig(level=os.environ.get("BEAMKIT_LOG_LEVEL", "INFO"), stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    create_mcp_server().run()


if __name__ == "__main__":
    main()
