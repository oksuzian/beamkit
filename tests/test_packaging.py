import sys
from pathlib import Path

if sys.version_info < (3, 11):
    import tomli as tomllib
else:
    import tomllib

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def test_runtime_dependencies_are_declared():
    proj = tomllib.loads(PYPROJECT.read_text())["project"]
    names = {d.split(";")[0].strip() for d in proj["dependencies"]}
    assert {"mcp", "requests", "authlib", "tomli"} <= names


def test_console_script_points_at_server_main():
    proj = tomllib.loads(PYPROJECT.read_text())["project"]
    assert proj["scripts"]["beamkit-mcp"] == "beamkit.server:main"


def test_dependencies_import():
    import authlib      # noqa: F401
    import mcp          # noqa: F401
    import requests     # noqa: F401
