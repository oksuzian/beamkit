"""$BEAMKIT_HOME/nersc.toml: everything the NERSC backend needs to know
about the facility and the caller's client. Loaded per tool call; a
missing or malformed file is refused with the full key list."""
import stat
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from beamkit import BeamkitError

REQUIRED = ("api", "sfapi_dir", "account", "base_dir", "qos", "owner")
DEFAULTS = {
    "procs_per_node": 128,
    "image": "/cvmfs/singularity.opensciencegrid.org/fermilab/fnal-wn-el9:latest",
    "apptainer": "/cvmfs/oasis.opensciencegrid.org/mis/apptainer/current/bin/apptainer",
}
KEY_NAMES = ("priv_key.pem", "priv_key.jwk")


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


def config_path(home) -> Path:
    return Path(home) / "nersc.toml"


def _toml():
    """The stdlib tomllib (3.11+) or the tomli backport, imported here
    rather than at module level: tools.py imports nersc_config unconditionally
    (get_server_info reports NERSC availability even without a config), and
    the Fermilab launcher runs under a Python 3.10 venv that has never
    needed tomli installed."""
    if sys.version_info < (3, 11):
        try:
            import tomli as tomllib
        except ImportError as e:
            raise ConfigError("reading nersc.toml needs tomli on Python < 3.11: "
                              "pip install beamkit (or pip install tomli)") from e
    else:
        import tomllib
    return tomllib


def _text(d, key):
    v = d[key]
    if not isinstance(v, str) or not v.strip():
        raise ConfigError(f"nersc.toml: {key} must be a non-empty string, got {v!r}")
    return v.strip()


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
    ppn = raw.get("procs_per_node", DEFAULTS["procs_per_node"])
    if isinstance(ppn, bool) or not isinstance(ppn, int) or ppn < 1:
        raise ConfigError(f"{path}: procs_per_node must be a positive integer, got {ppn!r}")
    return NerscConfig(api=_text(raw, "api").rstrip("/"), sfapi_dir=Path(_text(raw, "sfapi_dir")).expanduser(),
                       account=_text(raw, "account"), base_dir=_text(raw, "base_dir").rstrip("/"),
                       qos=_text(raw, "qos"), owner=_text(raw, "owner"), procs_per_node=ppn,
                       image=raw.get("image", DEFAULTS["image"]),
                       apptainer=raw.get("apptainer", DEFAULTS["apptainer"]))
