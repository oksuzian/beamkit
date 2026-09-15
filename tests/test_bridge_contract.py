"""The seam between beamkit and prodtools is the prodtools MCP tool
schemas plus a few facts beamkit copies. With BEAMKIT_PRODTOOLS_ROOT
naming a checkout whose mcp/.venv is installed, this spawns the REAL
prodtools servers through their launchers and checks every call bridge
makes against the advertised inputSchema, and runs the copied-facts probe
in a subprocess. Skipped otherwise. Nothing here touches SAM or the grid;
the launchers do run `muse setup ops`, so allow a minute."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from beamkit import bridge
from beamkit.mcpclient import StdioServer

ROOT = os.environ.get("BEAMKIT_PRODTOOLS_ROOT")
pytestmark = pytest.mark.skipif(
    not ROOT or not (Path(ROOT) / "mcp" / ".venv" / "bin" / "python").is_file(),
    reason="BEAMKIT_PRODTOOLS_ROOT does not name a prodtools checkout with mcp/.venv installed")


def _close_all(started):
    """Close every server independently: one raising close() must not
    skip the others."""
    for s in started.values():
        try:
            s.close()
        except Exception:      # noqa: BLE001 - tearing down regardless
            pass


@pytest.fixture(scope="module")
def servers():
    started = {}
    try:
        for kind in ("write", "read"):
            s = StdioServer(f"prodtools-{kind}", str(bridge.launcher(kind)), start_timeout=300)
            s.start()
            started[kind] = s
    except Exception:
        # a later start() failing must not leak an earlier, already-live child
        _close_all(started)
        raise
    yield started
    _close_all(started)


def test_every_bridge_call_matches_the_real_tool_schema(servers):
    problems = []
    for tool, (kind, always, optional) in bridge.CALLS.items():
        s = servers[kind]
        if not s.has_tool(tool):
            problems.append(f"{kind}: no tool {tool!r} (has {sorted(s.tools)})")
            continue
        schema = s.tools[tool].inputSchema or {}
        props = set(schema.get("properties", {}))
        required = set(schema.get("required", []))
        unknown = (always | optional) - props
        if unknown:
            problems.append(f"{tool}: bridge sends {sorted(unknown)} which the schema lacks {sorted(props)}")
        missing = required - always
        if missing:
            problems.append(f"{tool}: schema requires {sorted(missing)} which bridge does not always send")
    assert problems == [], "\n".join(problems)


def test_push_file_is_advertised_by_the_write_server(servers):
    assert servers["write"].has_tool("push_file")


def test_copied_facts_match_prodtools():
    probe = Path(__file__).with_name("_bridge_contract_probe.py")
    r = subprocess.run([sys.executable, str(probe), ROOT], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    assert json.loads(r.stdout)["failures"] == []
