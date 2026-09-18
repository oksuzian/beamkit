"""beamkit: G4beamline production front end over prodtools' g4bl runner."""
__version__ = "0.5.0"


class BeamkitError(Exception):
    """Every error beamkit raises on purpose; modules subclass it for their
    own refusals and nothing re-wraps one into another."""
