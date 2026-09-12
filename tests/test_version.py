from pathlib import Path

import beamkit


def test_package_version_matches_pyproject():
    """get_server_info reports __version__; a release bump must reach it."""
    text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    line = [l for l in text.splitlines() if l.startswith("version = ")][0]
    assert beamkit.__version__ == line.split('"')[1]
