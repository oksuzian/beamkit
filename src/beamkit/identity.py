"""Who a beamkit call acts as. `run_as` decides the SAM owner in the names,
which ledger prodtools reads, whether `confirm` is required, and whether a
dev prodtools checkout may ship to the workers; every one of those rules is
here and the rest of beamkit asks an Identity instead of a run_as string."""
import getpass
import os
from dataclasses import dataclass
from typing import Optional

from beamkit import BeamkitError
from beamkit.backends import SITES

RUN_AS = ("self", "mu2epro")
DEV_DIR_VAR = "BEAMKIT_PRODTOOLS_DIR"


class IdentityError(BeamkitError):
    pass


def _username() -> str:
    return getpass.getuser()


@dataclass(frozen=True)
class Identity:
    run_as: str
    owner: str
    dev_dir: Optional[str]

    @property
    def production(self) -> bool:
        return self.run_as == "mu2epro"

    @property
    def mine(self) -> bool:
        """prodtools' `mine`: read the personal ledger and queue, not production's."""
        return not self.production

    def dev_dir_for_shipping(self) -> Optional[str]:
        """The prodtools checkout to ship to the workers, or None for the
        cvmfs release. Refused here, before any side effect, because
        prodtools refuses a dev prodtools_dir for mu2epro outright."""
        if self.production and self.dev_dir:
            raise IdentityError(f"{DEV_DIR_VAR} is set, which ships that checkout to the workers; a "
                                f"production run uses a published cvmfs prodtools release only: unset "
                                f"{DEV_DIR_VAR}")
        return self.dev_dir


def resolve(run_as, confirm=False, *, writes=True, site="fermilab", owner=None) -> Identity:
    """The identity a call runs as, refused before any side effect when it
    cannot. `writes=False` is for calls that only read as that account.
    site='nersc' is one person with one sfapi client: run_as='self' only,
    the owner comes from nersc.toml, and no prodtools checkout ships."""
    if site not in SITES:
        raise IdentityError(f"site must be one of {SITES}, got {site!r}")
    if run_as not in RUN_AS:
        raise IdentityError(f"run_as must be one of {RUN_AS}, got {run_as!r}")
    if site == "nersc":
        if run_as != "self":
            raise IdentityError("site='nersc' accepts run_as='self' only: a NERSC run is submitted by "
                                "one person's sfapi client and registers nothing in production SAM")
        if not owner:
            raise IdentityError("site='nersc' needs the owner from nersc.toml")
        return Identity(run_as="self", owner=owner, dev_dir=None)
    ident = Identity(run_as=run_as, owner="mu2e" if run_as == "mu2epro" else _username(),
                     dev_dir=os.environ.get(DEV_DIR_VAR) or None)
    if writes and ident.production and not confirm:
        raise IdentityError("run_as='mu2epro' registers artifacts in production SAM and submits "
                            "production grid jobs; pass confirm=True")
    return ident
