"""$BEAMKIT_HOME/nersc.toml: everything the NERSC backend needs to know
about the facility and the caller's client. Loaded per tool call; a
missing or malformed file is refused with the full key list."""
import stat
from dataclasses import asdict, dataclass
from pathlib import Path

try:
    import tomllib
except ImportError:                     # tomli is a declared dependency below 3.11
    import tomli as tomllib

from beamkit import BeamkitError, naming

REQUIRED = ("sfapi_dir", "account", "base_dir", "qos", "owner")
# the IRI Facility API base; the sfapi transport addresses sfapi_api instead
# and never reads it, so it is required only for transport = "iri"
IRI_REQUIRED = ("api",)
DEFAULTS = {"procs_per_node": 128, "shared_qos": "shared", "shared_max_procs": 64, "transport": "iri",
            "sfapi_api": "https://api.nersc.gov/api/v1.2", "machine": "perlmutter",
            "image": "/cvmfs/singularity.opensciencegrid.org/fermilab/fnal-wn-el9:latest",
            "apptainer": "/cvmfs/oasis.opensciencegrid.org/mis/apptainer/current/bin/apptainer"}
KEY_NAMES = ("priv_key.pem", "priv_key.jwk")
TRANSPORTS = ("iri", "sfapi")
INTS = ("procs_per_node", "shared_max_procs")
STRINGS = ("api", "sfapi_dir", "account", "base_dir", "qos", "owner", "shared_qos", "transport",
           "sfapi_api", "machine", "image", "apptainer")


class ConfigError(BeamkitError):
    pass


@dataclass(frozen=True)
class NerscConfig:
    sfapi_dir: Path
    account: str
    base_dir: str
    qos: str
    owner: str
    procs_per_node: int
    image: str
    apptainer: str
    api: str = ""               # required for transport = "iri" only
    # a slice smaller than a node goes to shared_qos, non-exclusive and charged
    # per core, when it fits the cap (half a node on Perlmutter); anything else
    # takes a whole node
    shared_qos: str = "shared"
    shared_max_procs: int = 64
    # "sfapi" is the legacy Superfacility API v1.2 at sfapi_api,
    # machine-addressed, same client credential as the IRI v2 api
    transport: str = "iri"
    sfapi_api: str = "https://api.nersc.gov/api/v1.2"
    machine: str = "perlmutter"

    def key_file(self) -> Path:
        """The client's private key, refused when readable by anyone else."""
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
        return {**asdict(self), "sfapi_dir": str(self.sfapi_dir)}


def config_path(home) -> Path:
    return Path(home) / "nersc.toml"


def load(home) -> NerscConfig:
    path = config_path(home)
    if not path.is_file():
        raise ConfigError(f"no NERSC config at {path}; create it with keys "
                          f"{', '.join(REQUIRED)} (plus {', '.join(IRI_REQUIRED)} on the iri "
                          f"transport; optional: {', '.join(DEFAULTS)})")
    try:
        raw = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from e
    unknown = set(raw) - set(REQUIRED) - set(IRI_REQUIRED) - set(DEFAULTS)
    if unknown:
        raise ConfigError(f"{path}: unknown key {sorted(unknown)[0]!r}; allowed: "
                          f"{', '.join((*REQUIRED, *IRI_REQUIRED, *DEFAULTS))}")
    d = {"api": "", **DEFAULTS, **raw}
    if d["transport"] not in TRANSPORTS:
        raise ConfigError(f"{path}: transport must be one of {', '.join(TRANSPORTS)}, got {d['transport']!r}")
    required = REQUIRED + (IRI_REQUIRED if d["transport"] == "iri" else ())
    missing = [k for k in required if k not in raw]
    if missing:
        raise ConfigError(f"{path}: missing {', '.join(missing)}; transport {d['transport']!r} requires "
                          f"{', '.join(required)}")
    for k in INTS:
        if isinstance(d[k], bool) or not isinstance(d[k], int) or d[k] < 1:
            raise ConfigError(f"{path}: {k} must be a positive integer, got {d[k]!r}")
    for k in (STRINGS if "api" in raw else STRINGS[1:]):        # api: only when given
        if not isinstance(d[k], str) or not d[k].strip():
            raise ConfigError(f"{path}: {k} must be a non-empty string, got {d[k]!r}")
        d[k] = d[k].strip()
    d["sfapi_dir"] = Path(d["sfapi_dir"]).expanduser()
    for k in ("api", "sfapi_api", "base_dir"):
        d[k] = d[k].rstrip("/")
    # owner names every file the run produces; a dotted or otherwise irregular
    # one breaks dot-field parsing downstream (the dsconf collision probe,
    # Mu2eName parsing at harvest) silently rather than loudly
    if not naming.TAG_RE.match(d["owner"]):
        raise ConfigError(f"{path}: owner must be a Mu2e name token, your NERSC login (got {d['owner']!r})")
    # job.sh binds only /cvmfs and /global/cfs into the container; quotes,
    # backslashes and whitespace break the double-quoted shell words and the
    # beamfile_job.py Python literal that embed base_dir unescaped
    if not d["base_dir"].startswith("/") or any(c in d["base_dir"] for c in (" ", "\t", "\n", '"', "'", "\\")):
        raise ConfigError(f"{path}: base_dir must be an absolute path with no whitespace, quotes or "
                          f"backslashes, got {d['base_dir']!r}")
    if not d["base_dir"].startswith("/global/cfs/"):
        raise ConfigError(f"{path}: base_dir must start with /global/cfs/ (job.sh binds only /cvmfs and "
                          f"/global/cfs into the container), got {d['base_dir']!r}")
    return NerscConfig(**d)
