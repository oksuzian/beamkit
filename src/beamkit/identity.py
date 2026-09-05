"""Who a beamkit call acts as.

`run_as` is one concept with several consequences: the SAM owner the
names carry, which ledger prodtools reads and writes, whether `confirm`
is required, whether a dev prodtools checkout may ship to the workers,
where a published file lands by default, and whose campaign_status is
read. Every one of those rules lives here; the rest of beamkit asks an
Identity instead of testing `run_as` strings."""
import getpass
import os
from dataclasses import dataclass
from typing import Optional

from beamkit import BeamkitError

RUN_AS = ("self", "mu2epro")
DEV_DIR_VAR = "BEAMKIT_PRODTOOLS_DIR"


class IdentityError(BeamkitError):
    pass


def _username() -> str:
    return getpass.getuser()


def dev_dir_from_env() -> Optional[str]:
    return os.environ.get(DEV_DIR_VAR) or None


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

    @property
    def default_publish_location(self) -> str:
        return "tape" if self.production else "scratch"

    def dev_dir_for_shipping(self) -> Optional[str]:
        """The prodtools checkout to ship to the workers, or None for the
        cvmfs release. A production run uses a published release only;
        prodtools refuses a dev prodtools_dir for mu2epro outright, so
        refuse it here before any side effect."""
        if self.production and self.dev_dir:
            raise IdentityError(f"{DEV_DIR_VAR} is set, which ships that checkout to the workers; a "
                                f"production run uses a published cvmfs prodtools release only: unset "
                                f"{DEV_DIR_VAR}")
        return self.dev_dir


def resolve(run_as, confirm=False, *, writes=True) -> Identity:
    """The identity a call runs as, refused before any side effect when it
    cannot. `writes=False` is for calls that only read as that account."""
    if run_as not in RUN_AS:
        raise IdentityError(f"run_as must be one of {RUN_AS}, got {run_as!r}")
    ident = Identity(run_as=run_as, owner="mu2e" if run_as == "mu2epro" else _username(),
                     dev_dir=dev_dir_from_env())
    if writes and ident.production and not confirm:
        raise IdentityError("run_as='mu2epro' registers artifacts in production SAM and submits "
                            "production grid jobs; pass confirm=True")
    return ident


def for_record(rec) -> Identity:
    """The identity a run was created as, from its record. No environment
    is consulted: the record is the truth about who owns the run."""
    return Identity(run_as=rec.run_as, owner=rec.owner, dev_dir=None)
