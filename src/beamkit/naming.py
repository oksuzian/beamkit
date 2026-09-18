"""Every Mu2e name beamkit produces. desc = tag, dsconf = deck sha[:7];
the run id, the cnf, the nts dataset and the beam-file artifact are all
spelled here and nowhere else."""
import re

from beamkit import BeamkitError

TAG_RE = re.compile(r"^[A-Za-z0-9]+$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
EXPLICIT_DSCONF_RE = re.compile(r"^[A-Za-z0-9-]+$")
NTS_SEQ_RE = re.compile(r"^[0-9]+$")
MAX_SUFFIX = 999


class NamingError(BeamkitError):
    pass


def validate_tag(tag) -> str:
    if not isinstance(tag, str) or not TAG_RE.match(tag):
        raise NamingError(f"tag {tag!r} is not a Mu2e description token ([A-Za-z0-9]+)")
    return tag


def dsconf_base(sha) -> str:
    if not isinstance(sha, str) or not SHA_RE.match(sha):
        raise NamingError(f"deck sha {sha!r} is not a 40-hex commit sha")
    return sha[:7]


def run_id(desc, dsconf) -> str:
    return f"{desc}.{dsconf}"


def cnf_name(owner, desc, dsconf) -> str:
    return f"cnf.{owner}.{desc}.{dsconf}.0.tar"


def dataset(owner, desc, dsconf) -> str:
    """The nts dataset a run writes. prodtools' push_cnf reports the entry's
    outloc key ("nts.*.root"), a glob, so the real name is composed here."""
    return nts_prefix(owner, desc, dsconf) + "root"


def nts_prefix(owner, desc, dsconf) -> str:
    """What every nts file of a run is named before its sequencer."""
    return f"nts.{owner}.{desc}.{dsconf}."


def nts_index(name, owner, desc, dsconf) -> int | None:
    """The job index one of this run's nts files carries, or None when
    `name` (a bare name or a path) is not one of them. g4bl outputs are
    %08d-indexed, so the sequencer is the whole fifth dot field and all
    digits; anything else (a composite sequencer, another run's file) is
    not a job index."""
    base = str(name).rsplit("/", 1)[-1]
    prefix = nts_prefix(owner, desc, dsconf)
    if not (base.startswith(prefix) and base.endswith(".root")):
        return None
    seq = base[len(prefix):-len(".root")]
    return int(seq) if NTS_SEQ_RE.match(seq) else None


def beamfile_name(owner, desc, label, dsconf) -> str:
    """The SAM name of a published beam file. Six fields: pushOutput
    declares FILES, and a five-field name is a dataset. The sequencer is
    `0`, as on the cnf tarball, since a run has one beam file per label."""
    return f"etc.{owner}.{desc}Beam-{label}.{dsconf}.0.txt"


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
