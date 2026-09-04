import getpass
from pathlib import Path

from beamkit import paths


def test_home_follows_env(beamkit_home):
    assert paths.home() == beamkit_home
    assert paths.decks_dir() == beamkit_home / "decks"
    assert paths.runs_dir() == beamkit_home / "runs"
    assert paths.beamfiles_dir() == beamkit_home / "beamfiles"


def test_home_default_is_user_data_dir(monkeypatch):
    monkeypatch.delenv("BEAMKIT_HOME")
    assert paths.home() == Path("/exp/mu2e/data/users") / getpass.getuser() / "beamkit"
