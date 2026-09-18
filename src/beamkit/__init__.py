"""beamkit: G4beamline production front end over prodtools' g4bl runner."""
__version__ = "0.5.0"


class BeamkitError(Exception):
    """Every error beamkit raises on purpose. Each module subclasses it for
    its own refusals; tools.py raises it directly. The MCP client sees the
    message either way, so nothing re-wraps one into another."""
