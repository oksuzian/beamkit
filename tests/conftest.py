import pytest


@pytest.fixture(autouse=True)
def beamkit_home(tmp_path, monkeypatch):
    home = tmp_path / "beamkit_home"
    monkeypatch.setenv("BEAMKIT_HOME", str(home))
    return home
