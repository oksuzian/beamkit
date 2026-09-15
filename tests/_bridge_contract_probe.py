"""Run by test_bridge_contract in a fresh interpreter: import the REAL
prodtools modules whose facts beamkit copies rather than imports (only
bridge may talk to prodtools, and it does so over MCP) and check the
copies. Prints a JSON report on stdout. Nothing here touches SAM, the
ledger or the grid."""
import json
import sys
from unittest.mock import MagicMock

root = sys.argv[1]
for name in ("samweb_client", "ifdh"):        # Fermilab-only, absent off the gpvms
    sys.modules[name] = MagicMock()
sys.path[:0] = [root, f"{root}/mcp/src"]

import utils.job_common as jc                       # noqa: E402
import utils.jobdesc as jobdesc                     # noqa: E402
import utils.json2jobdef as j2j                     # noqa: E402

from beamkit import compose, naming                 # noqa: E402

failures = []
if tuple(compose.G4BL_WORKER_PARAMS) != tuple(j2j.G4BL_WORKER_PARAMS):
    failures.append(f"compose.G4BL_WORKER_PARAMS {compose.G4BL_WORKER_PARAMS} != json2jobdef {j2j.G4BL_WORKER_PARAMS}")
if not set(compose.OUTLOCS) <= set(jobdesc.OUTLOC_VALID):
    failures.append(f"compose.OUTLOCS {compose.OUTLOCS} not within jobdesc.OUTLOC_VALID {jobdesc.OUTLOC_VALID}")
for got, want in ((naming.cnf_name("u", "T", "e470313"),
                   jc.Mu2eName.build(tier="cnf", owner="u", description="T", dsconf="e470313", sequencer="0", extension="tar")),
                  (naming.dataset("u", "T", "e470313"),
                   jc.Mu2eName.build(tier="nts", owner="u", description="T", dsconf="e470313", extension="root")),
                  (naming.beamfile_name("u", "T", "bm", "e470313"),
                   jc.Mu2eName.build(tier="etc", owner="u", description="TBeam-bm", dsconf="e470313", sequencer="0", extension="txt"))):
    if got != want.filename:
        failures.append(f"naming {got!r} != Mu2eName.build {want.filename!r}")

# The NERSC path runs no prodtools on the node, so beamkit carries the g4bl
# recipe itself. Same inputs, byte-equal script, or the two have drifted.
try:
    import utils.runmu2e as runmu2e            # noqa: E402
    from beamkit import nersc_templates        # noqa: E402
    args = ("Mu2E.in", 11, 10, "/abs/nts.u.T.e470313.00000001.root", {"epsMax": "0.01", "Beam_File": "a b"})
    theirs, ours = runmu2e._g4bl_script(*args[:4], params=args[4]), nersc_templates.g4bl_script(*args)
    if theirs != ours:
        failures.append(f"g4bl recipe drift:\nprodtools: {theirs!r}\nbeamkit:   {ours!r}")
except ImportError as e:
    failures.append(f"utils.runmu2e not importable for the g4bl recipe check: {e}")

print(json.dumps({"failures": failures}))
