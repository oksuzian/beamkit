# tests/test_nersc_live.py
"""Two indices of ten events on Perlmutter, then a beam file. Opt in with
BEAMKIT_NERSC_LIVE=/path/to/nersc.toml (qos = "debug"), from a host whose
IP the sfapi client allows. Takes 10 to 40 minutes, mostly queue wait."""
import os
import shutil
import time

import pytest

from beamkit import iri, nersc_config, paths, tools
from beamkit.backends import nersc

LIVE = os.environ.get("BEAMKIT_NERSC_LIVE")
pytestmark = pytest.mark.skipif(not LIVE or not os.path.isfile(LIVE), reason="BEAMKIT_NERSC_LIVE not set")
DECK = "e470313b49aa9d00924a3f76aea0a00249e7b567"


def _wait(run_id, done, minutes):
    for _ in range(minutes * 2):
        st = tools.beamline_status(run_id)
        if done(st):
            return st
        time.sleep(30)
    pytest.fail(f"{run_id} not done after {minutes} min: {tools.beamline_status(run_id)['status']}")


def test_two_indices_then_a_beam_file(beamkit_home):
    beamkit_home.mkdir(parents=True, exist_ok=True)
    shutil.copy(LIVE, beamkit_home / "nersc.toml")
    cfg = nersc_config.load(beamkit_home)
    who = iri.IriClient(cfg).whoami()
    # A client-credential token answers whoami with the numeric NERSC account id
    # ({"username": "105241"}), never the login: the owner comes from nersc.toml.
    print("whoami:", who)
    assert isinstance(who, dict) and who.get("username"), f"whoami returned no username: {who!r}"
    tag = "G4blLive"
    rec = tools.run_beamline(tag=tag, deck_ref=DECK, run_as="self", site="nersc", events_per_job=10, njobs=2,
                             params={"epsMax": "0.01"}, walltime_s=1800)
    assert rec["state"] == "submitted" and len(rec["nersc"]["jobs"]) == 1
    st = _wait(rec["run_id"], lambda s: s["record"]["state"] in ("complete", "short"), 40)
    assert st["record"]["state"] == "complete", st["status"]
    assert st["status"]["outputs"] == {**st["status"]["outputs"], "expected": 2, "nts": 2, "logs": 2, "missing": []}
    outs = tools.beamline_outputs(rec["run_id"])
    assert outs["n_files"] == 2 and all(f["size"] > 10_000 for f in outs["files"])
    log = iri.IriClient(cfg).download(f"{rec['nersc']['run_dir']}/out/log.{rec['owner']}.{tag}.{rec['dsconf']}.00000001.log")
    assert "BK_DONE idx=1 rc=0" in log and "g4beamline: simulation complete" in log
    bf = tools.make_beamfile(rec["run_id"], "bm", "self", site="nersc")
    st = _wait(rec["run_id"], lambda s: s["record"]["beamfiles"][0]["state"] != "submitted", 30)
    bf = st["record"]["beamfiles"][0]
    assert bf["state"] == "complete", bf
    assert bf["rows"] > 0 and bf["n_files"] == 2 and bf["pot"] == 20 and bf["missing_indices"] == []
