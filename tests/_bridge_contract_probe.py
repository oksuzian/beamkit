"""Run by test_bridge_contract in a fresh interpreter: import the REAL
prodtools modules bridge.py names and check that every call bridge makes
binds to their real signatures. Prints a JSON report on stdout.

Every prodtools function is replaced with a stub that binds the recorded
arguments to the real function's signature and returns a canned value, so
nothing here touches SAM, the ledger or the grid. The kwargs under test are
the ones bridge itself sends; nothing is copied from bridge into this file.
"""
import inspect
import json
import sys
from unittest.mock import MagicMock

root = sys.argv[1]
for name in ("samweb_client", "ifdh"):        # Fermilab-only, absent off the gpvms
    sys.modules[name] = MagicMock()
sys.path[:0] = [root, f"{root}/mcp/src"]

import prodtools_mcp_write.tools as wtools          # noqa: E402
import prodtools_mcp_write.runner as runner         # noqa: E402
import prodtools_mcp.tools.status as status         # noqa: E402
import utils.samweb_wrapper as sw                   # noqa: E402
import utils.file_resolver as fr                    # noqa: E402
import utils.job_common as jc                       # noqa: E402
import utils.jobdesc as jobdesc                     # noqa: E402
import utils.json2jobdef as j2j                     # noqa: E402

failures = []
signatures = {"push_cnf": str(inspect.signature(wtools.push_cnf)),
              "run_submissions": str(inspect.signature(wtools.run_submissions))}


def bind(mod, name, ret):
    real = getattr(mod, name)
    sig = inspect.signature(real)

    def stub(*a, **kw):
        try:
            sig.bind(*a, **kw)
        except TypeError as e:
            failures.append(f"{mod.__name__}.{name}{sig}: {e}")
        return ret
    setattr(mod, name, stub)


bind(wtools, "push_cnf", {"tarball": "t", "datasets": ["nts.*.root"], "campaign_id": 1, "njobs": 1})
bind(wtools, "run_submissions", {"rc": 0, "needs_attention": False, "campaign_id": 1, "output": ""})
bind(status, "campaign_status", {"campaigns": []})
bind(status, "list_campaigns", {"campaigns": []})
bind(sw, "locate_file", "enstore:/x")
bind(sw, "file_sizes_in_dataset", {"nts.u.T.e470313.00000000.root": 10})
bind(fr, "dataset_dir", "/pnfs/x")
# utils.job_common.Mu2eName stays REAL: bridge.dataset_files depends on
# .parse, .sequencer and .relpathname() and this is where that is checked.

from beamkit import bridge, compose, naming         # noqa: E402

bridge.push_cnf("/tmp/entry.json", "T", "e470313", 1, "self", False)
bridge.push_cnf("/tmp/entry.json", "T", "e470313", 1, "self", False, prodtools_dir=root)
bridge.tick("self", 1, False)
bridge.tick("self", None, False)
bridge.campaign_status(1, mine=True)
bridge.campaigns(mine=True)
bridge.cnf_exists("cnf.u.T.e470313.0.tar")
files = bridge.dataset_files("nts.u.T.e470313.root", "scratch")
if files != [{"name": "nts.u.T.e470313.00000000.root", "index": 0, "size": 10,
              "path": "/pnfs/x/" + jc.Mu2eName.parse("nts.u.T.e470313.00000000.root").relpathname()}]:
    failures.append(f"dataset_files shape: {files!r}")
info = bridge.prodtools_info()
if info.get("root") != runner.REPO_ROOT:
    failures.append(f"prodtools_info root {info!r} != runner.REPO_ROOT {runner.REPO_ROOT!r}")
# Facts beamkit copies rather than imports (only bridge may import prodtools):
# the worker-owned g4bl params, the outloc vocabulary, and the dot-name grammar.
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

available = bridge.push_file_available()
if available:
    bind(wtools, "push_file", {})
    bridge.push_file("/tmp/x.txt", "scratch", ["a.root"], "self", False)

print(json.dumps({"failures": failures, "push_file_available": available, "signatures": signatures}))
