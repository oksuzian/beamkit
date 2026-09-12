import sys
from pathlib import Path

if sys.version_info < (3, 11):
    import tomli as tomllib
else:
    import tomllib

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def test_runtime_dependencies_are_declared():
    proj = tomllib.loads(PYPROJECT.read_text())["project"]
    import re
    names = {re.split(r"[<>=;\s]", d.strip())[0] for d in proj["dependencies"]}
    assert {"mcp", "requests", "authlib", "tomli"} <= names


def test_mcp_pinned_below_2():
    """mcp 2.x renamed FastMCP to MCPServer; server.py imports the 1.x
    name, so an unpinned install fails at create_mcp_server()."""
    proj = tomllib.loads(PYPROJECT.read_text())["project"]
    mcp = [d for d in proj["dependencies"] if d.startswith("mcp")][0]
    assert "<2" in mcp


def test_console_script_points_at_server_main():
    proj = tomllib.loads(PYPROJECT.read_text())["project"]
    assert proj["scripts"]["beamkit-mcp"] == "beamkit.server:main"


def test_dependencies_import():
    import authlib      # noqa: F401
    import mcp          # noqa: F401
    import requests     # noqa: F401
