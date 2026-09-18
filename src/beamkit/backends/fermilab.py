"""The Fermilab backend: a run is a prodtools campaign, driven through
bridge.py. This module carries the Fermilab half of every tool (the
interface in docs/specs/2026-09-17-backend-seam-design.md §6)."""
from dataclasses import dataclass, field


@dataclass
class Block:
    """What only the Fermilab backend reads and writes on a run record."""
    prodtools: dict                      # root, commit, dev_dir
    campaign_id: int | None = None
    tarball: str | None = None
    ticks: list = field(default_factory=list)
