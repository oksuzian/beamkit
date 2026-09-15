import os
import stat
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def beamkit_home(tmp_path, monkeypatch):
    home = tmp_path / "beamkit_home"
    monkeypatch.setenv("BEAMKIT_HOME", str(home))
    monkeypatch.delenv("BEAMKIT_PRODTOOLS_DIR", raising=False)
    return home


@pytest.fixture
def fake_prodtools_root(tmp_path, monkeypatch):
    """A prodtools tree whose two MCP launchers run tests/fake_prodtools_mcp.py.
    bridge spawns exactly these paths, so the whole client path is real."""
    from beamkit import bridge
    scripts = tmp_path / "prodtools" / "mcp" / "scripts"
    scripts.mkdir(parents=True)
    for script, role in (("start_write_mcp.sh", "write"), ("start_mcp.sh", "read")):
        p = scripts / script
        p.write_text(f"#!/bin/bash\nexport PYTHONPATH={REPO}\n"
                     f"exec {sys.executable} -m tests.fake_prodtools_mcp {role}\n")
        p.chmod(p.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("BEAMKIT_PRODTOOLS_ROOT", str(tmp_path / "prodtools"))
    monkeypatch.setenv("BEAMKIT_PRODTOOLS_START_TIMEOUT", "60")
    monkeypatch.setenv("FAKE_PRODTOOLS_CALLS", str(tmp_path / "calls.jsonl"))
    for var in ("FAKE_PRODTOOLS_FAIL", "FAKE_PRODTOOLS_DIE", "FAKE_PRODTOOLS_OMIT",
                "FAKE_PRODTOOLS_NO_START", "FAKE_PRODTOOLS_HANG_START", "FAKE_PRODTOOLS_FILES"):
        monkeypatch.delenv(var, raising=False)
    bridge.reset()
    yield tmp_path / "prodtools"
    bridge.reset()
