import getpass
from pathlib import Path

from beamkit import paths


def test_home_follows_env(beamkit_home):
    assert paths.home() == beamkit_home
    assert paths.decks_dir() == beamkit_home / "decks"
    assert paths.runs_dir() == beamkit_home / "runs"
    assert paths.beamfiles_dir() == beamkit_home / "beamfiles"


def test_home_default_is_user_data_dir_when_the_fermilab_tree_exists(monkeypatch, tmp_path):
    monkeypatch.delenv("BEAMKIT_HOME")
    users = tmp_path / "users"
    users.mkdir()
    monkeypatch.setattr(paths, "FERMILAB_USERS", users)
    assert paths.home() == users / getpass.getuser() / "beamkit"


def test_home_default_is_dot_beamkit_off_fermilab(monkeypatch, tmp_path):
    monkeypatch.delenv("BEAMKIT_HOME")
    monkeypatch.setattr(paths, "FERMILAB_USERS", tmp_path / "absent")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "laptop"))
    assert paths.home() == tmp_path / "laptop" / ".beamkit"
