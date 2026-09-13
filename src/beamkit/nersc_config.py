"""$BEAMKIT_HOME/nersc.toml: everything the NERSC backend needs to know
about the facility and the caller's client. Loaded per tool call; a
missing or malformed file is refused with the full key list."""
import stat
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from beamkit import BeamkitError, naming

REQUIRED = ("api", "sfapi_dir", "account", "base_dir", "qos", "owner")
DEFAULTS = {
    "procs_per_node": 128,
    "shared_qos": "shared",
    "shared_max_procs": 64,
    "transport": "iri",
    "sfapi_api": "https://api.nersc.gov/api/v1.2",
    "machine": "perlmutter",
    "image": "/cvmfs/singularity.opensciencegrid.org/fermilab/fnal-wn-el9:latest",
    "apptainer": "/cvmfs/oasis.opensciencegrid.org/mis/apptainer/current/bin/apptainer",
}
KEY_NAMES = ("priv_key.pem", "priv_key.jwk")
TRANSPORTS = ("iri", "sfapi")


class ConfigError(BeamkitError):
    pass


@dataclass(frozen=True)
class NerscConfig:
    api: str
    sfapi_dir: Path
    account: str
    base_dir: str
    qos: str
    owner: str
    procs_per_node: int
    image: str
    apptainer: str
    # A slice smaller than a node goes to shared_qos, non-exclusive, charged
    # per core, when it fits the shared queue's cap (half a node on
    # Perlmutter). Larger partial slices and full slices take a whole node.
    shared_qos: str = "shared"
    shared_max_procs: int = 64
    # "iri": api (IRI Facility API v2). "sfapi": the legacy Superfacility
    # API v1.2 at sfapi_api, machine-addressed, same client credential.
    transport: str = "iri"
    sfapi_api: str = "https://api.nersc.gov/api/v1.2"
    machine: str = "perlmutter"

    def key_file(self) -> Path:
        """The private key of the sfapi client, refused when readable by
        anyone else: a red client submits jobs as the user."""
        for name in KEY_NAMES:
            p = self.sfapi_dir / name
            if p.is_file():
                mode = stat.S_IMODE(p.stat().st_mode)
                if mode & 0o077:
                    raise ConfigError(f"{p} is readable by others (mode {mode:o}); run: chmod 400 {p}")
                return p
        raise ConfigError(f"no {' or '.join(KEY_NAMES)} in {self.sfapi_dir}; save the client's private key "
                          f"there from Iris (Superfacility API Clients) with mode 400")

    def client_id(self) -> str:
        p = self.sfapi_dir / "client_id"
        if not p.is_file():
            raise ConfigError(f"no client_id in {self.sfapi_dir}; save the 13-character client id from Iris there")
        return p.read_text().strip()

    def as_record(self) -> dict:
        d = asdict(self)
        d["sfapi_dir"] = str(self.sfapi_dir)
        return d


def _posint(raw, key, path) -> int:
    v = raw.get(key, DEFAULTS[key])
    if isinstance(v, bool) or not isinstance(v, int) or v < 1:
        raise ConfigError(f"{path}: {key} must be a positive integer, got {v!r}")
    return v


def config_path(home) -> Path:
    return Path(home) / "nersc.toml"


def _toml():
    """The stdlib tomllib (3.11+) or the tomli backport, imported here
    rather than at module level: tools.py imports nersc_config unconditionally
    (get_server_info reports NERSC availability even without a config), and
    the Fermilab launcher runs under a Python 3.10 venv that has never
    needed tomli installed."""
    try:
        if sys.version_info < (3, 11):
            import tomli as tomllib
        else:
            import tomllib
    except ImportError as e:
        raise ConfigError("reading nersc.toml needs tomli on Python < 3.11: "
                          "pip install beamkit (or pip install tomli)") from e
    return tomllib


def _text(d, key):
    v = d[key]
    if not isinstance(v, str) or not v.strip():
        raise ConfigError(f"nersc.toml: {key} must be a non-empty string, got {v!r}")
    return v.strip()


def _validate_owner(owner, path):
    """owner names every file the run produces; the Fermilab path's owner is
    always a login (naming.TAG_RE), and a dotted or otherwise irregular
    NERSC owner breaks dot-field parsing downstream (the dsconf collision
    probe, Mu2eName parsing at harvest) silently rather than loudly."""
    if not naming.TAG_RE.match(owner):
        raise ConfigError(f"{path}: owner must be a Mu2e name token, your NERSC login (got {owner!r})")
    return owner


def _validate_base_dir(base_dir, path):
    """job.sh binds only /cvmfs and /global/cfs into the container; any
    other base_dir passes config, layout and submit and fails only on the
    node. Quotes/backslashes/whitespace break the double-quoted shell words
    and the beamfile_job.py Python literal that embed it unescaped."""
    if not base_dir.startswith("/") or any(c in base_dir for c in (" ", "\t", "\n", '"', "'", "\\")):
        raise ConfigError(f"{path}: base_dir must be an absolute path with no whitespace, quotes or "
                          f"backslashes, got {base_dir!r}")
    if not base_dir.startswith("/global/cfs/"):
        raise ConfigError(f"{path}: base_dir must start with /global/cfs/ (job.sh binds only /cvmfs and "
                          f"/global/cfs into the container), got {base_dir!r}")
    return base_dir


def load(home) -> NerscConfig:
    path = config_path(home)
    if not path.is_file():
        raise ConfigError(f"no NERSC config at {path}; create it with keys "
                          f"{', '.join(REQUIRED)} (optional: {', '.join(DEFAULTS)})")
    tomllib = _toml()
    try:
        raw = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from e
    unknown = set(raw) - set(REQUIRED) - set(DEFAULTS)
    if unknown:
        raise ConfigError(f"{path}: unknown key {sorted(unknown)[0]!r}; allowed: "
                          f"{', '.join((*REQUIRED, *DEFAULTS))}")
    missing = [k for k in REQUIRED if k not in raw]
    if missing:
        raise ConfigError(f"{path}: missing {', '.join(missing)}; required keys are {', '.join(REQUIRED)}")
    ppn = _posint(raw, "procs_per_node", path)
    transport = _text(raw, "transport") if "transport" in raw else DEFAULTS["transport"]
    if transport not in TRANSPORTS:
        raise ConfigError(f"{path}: transport must be one of {', '.join(TRANSPORTS)}, got {transport!r}")
    smp = _posint(raw, "shared_max_procs", path)
    return NerscConfig(api=_text(raw, "api").rstrip("/"), sfapi_dir=Path(_text(raw, "sfapi_dir")).expanduser(),
                       account=_text(raw, "account"),
                       base_dir=_validate_base_dir(_text(raw, "base_dir").rstrip("/"), path),
                       qos=_text(raw, "qos"), owner=_validate_owner(_text(raw, "owner"), path),
                       procs_per_node=ppn,
                       shared_qos=_text(raw, "shared_qos") if "shared_qos" in raw else DEFAULTS["shared_qos"],
                       shared_max_procs=smp,
                       transport=transport,
                       sfapi_api=(_text(raw, "sfapi_api") if "sfapi_api" in raw else DEFAULTS["sfapi_api"]).rstrip("/"),
                       machine=_text(raw, "machine") if "machine" in raw else DEFAULTS["machine"],
                       image=_text(raw, "image") if "image" in raw else DEFAULTS["image"],
                       apptainer=_text(raw, "apptainer") if "apptainer" in raw else DEFAULTS["apptainer"])
