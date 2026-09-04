"""Mu2e names for a beamkit run: desc = tag, dsconf = deck sha[:7]."""
import getpass
import re

RUN_AS = ("self", "mu2epro")
TAG_RE = re.compile(r"^[A-Za-z0-9]+$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
EXPLICIT_DSCONF_RE = re.compile(r"^[A-Za-z0-9-]+$")
MAX_SUFFIX = 999


class NamingError(ValueError):
    pass


def validate_tag(tag) -> str:
    if not isinstance(tag, str) or not TAG_RE.match(tag):
        raise NamingError(f"tag {tag!r} is not a Mu2e description token ([A-Za-z0-9]+)")
    return tag


def dsconf_base(sha) -> str:
    if not isinstance(sha, str) or not SHA_RE.match(sha):
        raise NamingError(f"deck sha {sha!r} is not a 40-hex commit sha")
    return sha[:7]


def cnf_name(owner, desc, dsconf) -> str:
    return f"cnf.{owner}.{desc}.{dsconf}.0.tar"


def owner_for(run_as) -> str:
    if run_as == "self":
        return getpass.getuser()
    if run_as == "mu2epro":
        return "mu2e"
    raise NamingError(f"run_as must be one of {RUN_AS}, got {run_as!r}")


def allocate_dsconf(owner, desc, base, taken, explicit=None) -> str:
    """The dsconf for a new run. `taken(cnf_name) -> bool` is the SAM probe.

    The unsuffixed base is always tried first; `-NNN` starts at -001 and
    is issued only on collision. An explicit dsconf is probed once and a
    taken one is an error: a cnf name is never reused.
    """
    if explicit is not None:
        if not isinstance(explicit, str) or not EXPLICIT_DSCONF_RE.match(explicit):
            raise NamingError(f"dsconf {explicit!r} is not a Mu2e token ([A-Za-z0-9-]+)")
        name = cnf_name(owner, desc, explicit)
        if taken(name):
            raise NamingError(f"dsconf {explicit!r} is taken: {name} exists in SAM and a cnf name is never reused")
        return explicit
    if not taken(cnf_name(owner, desc, base)):
        return base
    for n in range(1, MAX_SUFFIX + 1):
        cand = f"{base}-{n:03d}"
        if not taken(cnf_name(owner, desc, cand)):
            return cand
    raise NamingError(f"every dsconf {base}[-001..-{MAX_SUFFIX}] for {owner}/{desc} is taken")
