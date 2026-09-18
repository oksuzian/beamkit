"""Where a run executes, and the module that carries that site's half of
every tool. Two backends, one interface (docs/specs/2026-09-17-backend-
seam-design.md §6); tools.py and runs.py ask get(site) and never import a
backend by name."""
import importlib

from beamkit import BeamkitError

SITES = ("fermilab", "nersc")


def get(site):
    """The backend module for a site. Imported on first use, so records.py
    can name a block type without an import cycle."""
    if site not in SITES:
        raise BeamkitError(f"site must be one of {SITES}, got {site!r}")
    return importlib.import_module(f"beamkit.backends.{site}")


def block_type(site):
    return get(site).Block
