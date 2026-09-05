"""The bridge seam has two adapters: real prodtools and the fakes the rest
of this suite uses. The fakes accept any keywords, so they cannot catch a
prodtools signature change. This test can: with BEAMKIT_PRODTOOLS_ROOT
naming a prodtools checkout it imports the real modules and binds every
call bridge makes to the real signature. Skipped otherwise.

Runs in a subprocess so the real prodtools modules never enter this
interpreter's sys.modules, where test_bridge installs stand-ins.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = os.environ.get("BEAMKIT_PRODTOOLS_ROOT")
pytestmark = pytest.mark.skipif(
    not ROOT or not (Path(ROOT) / "mcp" / "src" / "prodtools_mcp_write" / "tools.py").is_file(),
    reason="BEAMKIT_PRODTOOLS_ROOT does not name a prodtools checkout")


def test_every_bridge_call_binds_to_the_real_prodtools_signature():
    probe = Path(__file__).with_name("_bridge_contract_probe.py")
    r = subprocess.run([sys.executable, str(probe), ROOT], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    report = json.loads(r.stdout)
    assert report["failures"] == [], "\n".join(report["failures"])
