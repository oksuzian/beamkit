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


def test_mcp_pinned_to_2():
    """server.py and mcpclient.py use the 2.x names (MCPServer, is_error,
    structured_content); a 1.x install fails at create_mcp_server()."""
    proj = tomllib.loads(PYPROJECT.read_text())["project"]
    mcp = [d for d in proj["dependencies"] if d.startswith("mcp")][0]
    assert ">=2" in mcp and "<3" in mcp


def test_console_script_points_at_server_main():
    proj = tomllib.loads(PYPROJECT.read_text())["project"]
    assert proj["scripts"]["beamkit-mcp"] == "beamkit.server:main"


def test_dependencies_import():
    import authlib      # noqa: F401
    import mcp          # noqa: F401
    import requests     # noqa: F401
