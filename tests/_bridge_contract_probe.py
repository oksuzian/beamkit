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

# Same for the cnf: the NERSC path packs its own tarball, and jobpars.json
# has to stay in prodtools' shape for the outputs to be declarable later.
# Same deck and entry through both builders, compared member by member.
import contextlib                                   # noqa: E402
import io                                           # noqa: E402
import os                                           # noqa: E402
import tarfile                                      # noqa: E402
import tempfile                                     # noqa: E402
from pathlib import Path                            # noqa: E402

from beamkit import nersc_cnf                       # noqa: E402


def _members(path):
    with tarfile.open(path) as t:
        names = sorted(m.name for m in t.getmembers())
        return names, json.loads(t.extractfile("jobpars.json").read())


with tempfile.TemporaryDirectory() as tmp:
    deck = Path(tmp, "deck")
    (deck / ".git").mkdir(parents=True)
    (deck / ".git" / "HEAD").write_text("ref\n")
    (deck / "sub").mkdir()
    (deck / "Mu2E.in").write_text("x\n")
    (deck / "sub" / "geom.txt").write_text("y\n")
    params = {"epsMax": "0.01"}
    for label, p in (("with g4bl_params", params), ("without g4bl_params", None)):
        out = Path(tmp, label.replace(" ", "_"))
        out.mkdir()
        config = {"desc": "T", "dsconf": "e470313", "owner": "u", "g4bl_dir": str(deck), "main_input": "Mu2E.in",
                  "events_per_job": 10, "njobs": 3, **({"g4bl_params": p} if p else {})}
        cwd = os.getcwd()
        os.chdir(out)                               # prodtools writes the cnf into the cwd
        try:
            with contextlib.redirect_stdout(io.StringIO()):     # it prints; stdout here is the JSON report
                j2j._build_g4bl_tarball(config)
        finally:
            os.chdir(cwd)
        theirs = out / j2j.get_parfile_name(config)
        ours = nersc_cnf.build_cnf(deck, nersc_cnf.jobpars(owner="u", tag="T", dsconf="e470313", main_input="Mu2E.in",
                                                           events_per_job=10, njobs=3, params=p), out / "ours.tar")
        (their_names, their_jp), (our_names, our_jp) = _members(theirs), _members(ours)
        if their_jp != our_jp:
            failures.append(f"cnf jobpars drift ({label}):\nprodtools: {their_jp}\nbeamkit:   {our_jp}")
        if their_names != our_names:
            failures.append(f"cnf member drift ({label}):\nprodtools: {their_names}\nbeamkit:   {our_names}")
        if any(".git" in n.split("/") for n in our_names):
            failures.append(f"beamkit cnf carries VCS internals ({label}): {our_names}")

print(json.dumps({"failures": failures}))
