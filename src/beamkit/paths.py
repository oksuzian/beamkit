"""Where beamkit keeps its own state. Nothing here is a ledger."""
import getpass
import os
from pathlib import Path

FERMILAB_USERS = Path("/exp/mu2e/data/users")


def home() -> Path:
    """BEAMKIT_HOME; else the Fermilab per-user data dir when that tree
    exists on this host (existing runs stay where they are); else
    ~/.beamkit. One directory check, no probing beyond it."""
    env = os.environ.get("BEAMKIT_HOME")
    if env:
        return Path(env)
    if FERMILAB_USERS.is_dir():
        return FERMILAB_USERS / getpass.getuser() / "beamkit"
    return Path.home() / ".beamkit"


def decks_dir() -> Path:
    return home() / "decks"


def runs_dir() -> Path:
    return home() / "runs"


def beamfiles_dir() -> Path:
    return home() / "beamfiles"
