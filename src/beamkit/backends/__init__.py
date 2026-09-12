"""Where a run executes. 'fermilab' is the prodtools path in tools.py;
'nersc' is backends/nersc.py. tools.py asks validate_site once per call."""
from beamkit import BeamkitError

SITES = ("fermilab", "nersc")


def validate_site(site) -> str:
    if site not in SITES:
        raise BeamkitError(f"site must be one of {SITES}, got {site!r}")
    return site
