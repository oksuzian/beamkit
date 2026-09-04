"""Where beamkit keeps its own state. Nothing here is a ledger."""
import getpass
import os
from pathlib import Path


def home() -> Path:
    env = os.environ.get("BEAMKIT_HOME")
    if env:
        return Path(env)
    return Path("/exp/mu2e/data/users") / getpass.getuser() / "beamkit"


def decks_dir() -> Path:
    return home() / "decks"


def runs_dir() -> Path:
    return home() / "runs"


def beamfiles_dir() -> Path:
    return home() / "beamfiles"
