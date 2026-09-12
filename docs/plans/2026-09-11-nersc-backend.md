# beamkit NERSC backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `run_beamline(..., site="nersc")` submits a g4bl run to NERSC Perlmutter through the IRI Facility API from any host with Python, git and a NERSC Superfacility API client; outputs stay on CFS; `make_beamfile(..., site="nersc")` builds the beam file there; no prodtools, SAM or dCache is touched on that path.

**Architecture:** A second backend inside beamkit. `iri.py` is a thin HTTP client for `https://api.iri.nersc.gov/api/v2` (token, resources, mkdir, ls, upload, download, submit, status, task polling). `nersc_config.py` reads `$BEAMKIT_HOME/nersc.toml`. `nersc_cnf.py` builds the cnf tarball locally. `nersc_templates.py` renders the node scripts from `templates/` with `@@NAME@@` placeholders. `backends/nersc.py` orchestrates: remote layout, one Slurm job per slice of `procs_per_node` indices, status from one `ls`, beam-file job. `tools.py` dispatches on `site` once at the top of each tool; the Fermilab path stays where it is.

**Tech Stack:** Python 3.10 (`/exp/mu2e/app/users/oksuzian/beamkit/.venv`, 3.10.14), `requests`, `authlib` (PrivateKeyJWT client-credentials), `tomli` on 3.10, `mcp` (FastMCP), pytest 9. Node side: apptainer and the fnal-wn-el9 image from cvmfs, g4beamline 3.08b via `spack load`, uproot under `/cvmfs/mu2e.opensciencegrid.org/env/ana/2.8.0/bin/python`.

**Spec:** `docs/specs/2026-09-11-nersc-backend-design.md` (commit 31050af). Repo: `/exp/mu2e/app/users/oksuzian/beamkit`, branch `v1`.

## Global Constraints

- Python `>=3.10`. `pyproject.toml` `dependencies = ["mcp", "requests", "authlib", "tomli; python_version < '3.11'"]`, `[dev] = ["pytest>=7"]`, console script `beamkit-mcp = beamkit.server:main`.
- `src/beamkit/bridge.py` stays the ONLY module that imports prodtools, and only inside function bodies. No new module imports `prodtools_mcp_write`, `prodtools_mcp`, or `utils`.
- The NERSC path never calls samweb, jobsub, pushOutput, `ifdh`, or opens a ledger. Its only network is `https://oidc.nersc.gov/c2id/token` and the API base from `nersc.toml`.
- Upload cap `UPLOAD_MAX = 5_242_880` bytes per file; a larger file is refused before any request.
- `site` in `("fermilab", "nersc")`. Default `"fermilab"` everywhere. `site="nersc"` with `run_as="mu2epro"` is refused before any network call.
- `procs_per_node` default `128`. `walltime_s` default `172800`. Job duration `min(walltime_s, events_per_job * 2 + 900)`.
- Template placeholders are `@@NAME@@`; no `str.format`, no `string.Template` (bash `$` and `{}` collide).
- The four g4bl lines (`unset ...`, `source ...`, `eval "$(spack load --sh g4beamline)"`, `g4bl ...`) are produced by `nersc_templates.g4bl_command` and `G4BL_RECIPE`, and the contract probe compares them to prodtools' `utils.runmu2e._g4bl_script`.
- No fallbacks: a missing config key, unreadable key file, unknown site, missing run dir, a task that fails, a job that fails to submit, is a raised `BeamkitError` subclass naming the thing and the next action.
- No recovery on the NERSC path. `make_recoveries` on a NERSC run raises.
- Records, decks, beam files under `paths.home()`. Tests set `BEAMKIT_HOME` to `tmp_path` (autouse fixture in `tests/conftest.py`).
- Remote layout for a run: `<base_dir>/runs/<run_id>/{cnf.<owner>.<tag>.<dsconf>.0.tar, job.sh, inner.sh, out/, slurm/, beamfiles/}`.
- Commit messages: `feat:`/`fix:`/`docs:`/`test:` prefix, subject at most 72 chars, body in normal prose, ending with
  `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_0138sWPyeSCDFbbn2cB9JBDy`.
- Never `git push`; the user pushes. Git on this host: if `git` fails with `unable to access '/nashome/...': Key has expired`, run it as `GIT_CONFIG_GLOBAL=/dev/null git -c user.name="Iuri Oksuzian" -c user.email="oksuzian@gmail.com" ...`.
- Run the suite with `.venv/bin/python -m pytest -q` from the repo root.

## Rulings recorded against the spec

1. Spec §4.4 derives the owner from the API's whoami. The whoami response shape was not recorded before the home directory became unreadable, so `nersc.toml` gains a required `owner` key and the live test asserts the API's whoami response contains it. `get_server_info` returns the raw whoami dict when the config loads.
2. Spec §10 names `backends/fermilab.py`. The Fermilab path's orchestration stays in `tools.py` with its 40 tests untouched; `backends/__init__.py` holds `SITES` and `validate_site`, and `backends/nersc.py` holds the NERSC orchestration. A `fermilab.py` extraction is a follow-up for a third site, not this plan.
3. Spec §6 shows `inner.sh` quoting `histoFile` with a literal path. prodtools' `_g4bl_script` shell-quotes its arguments; on the node the histo path is a shell variable, so the template renders the same command through `g4bl_command(..., quote=str)` and the contract test compares the default (`shlex.quote`) rendering. Same argument order and names, two quoting policies, one function.
4. Response shapes used by the fake IRI server (recorded 2026-09-10/11): filesystem operations return `{"task_id": ..., "task_uri": ...}`; `GET task_uri` returns `{"status": "queued"|"running"|"completed"|"failed"|"canceled", "result": {...}}`; `mkdir` result `{"output": null}`; `upload` result `{"output": "Uploaded <path>"}`; `ls` result `{"output": [{"name", "type" ("f"|"d"), "size" (string), "user", "group", "permissions", "last_modified", "link_target"}]}`; `download` result `{"output": "<file text verbatim>"}`; `POST /compute/job/{id}` returns `{"id": "58197742"}`; `GET /compute/status/{cid}/{jid}?include_spec=false` returns `{"status": {"state", "exit_code", "time", "message", "meta_data": {"elapsed", "nodelist", "state", "exitcode", ...}}}`; `GET /compute/resources` returns a list of `{"id", "name", "resource_type", "current_status", "description"}` with the Perlmutter entry named `"compute"`; `POST /filesystem/resources` with body `{}` returns a list with the CFS entry named `"cfs"`; errors are problem+json `{"type", "status", "title", "detail", "instance"}`.

## File structure

| File | Responsibility |
| --- | --- |
| `pyproject.toml` | dependencies and the `beamkit-mcp` entry point |
| `src/beamkit/paths.py` | three-way home resolution |
| `src/beamkit/nersc_config.py` | `NerscConfig` dataclass, `load(home)`, key-file permission check |
| `src/beamkit/iri.py` | `IriClient`, `IriError`, `sfapi_token`, `UPLOAD_MAX` |
| `src/beamkit/nersc_templates.py` | `G4BL_RECIPE`, `g4bl_command`, `render_*` for the four node files |
| `src/beamkit/templates/job.sh`, `inner.sh`, `beamfile.sh`, `beamfile_job.py` | node scripts with `@@NAME@@` placeholders |
| `src/beamkit/nersc_cnf.py` | `jobpars(...)`, `build_cnf(deck_dir, jobpars, out_path)` |
| `src/beamkit/backends/__init__.py` | `SITES`, `validate_site` |
| `src/beamkit/backends/nersc.py` | `slices`, `duration_s`, `job_spec`, `run_beamline`, `submit_run`, `status`, `outputs`, `make_beamfile` |
| `src/beamkit/records.py` | `site`, `nersc`, new states |
| `src/beamkit/identity.py` | `site` on `Identity`, `resolve(..., site, owner)` |
| `src/beamkit/tools.py` | `site` dispatch, `submit_run` tool |
| `src/beamkit/server.py` | tool table, instructions |
| `tests/fake_iri.py` | `FakeSession` + `FakeResponse` for `IriClient` |
| `tests/fixtures/nersc/*.golden` | rendered node scripts |
| `tests/test_nersc_live.py` | opt-in live smoke |

---

### Task 0: Packaging: dependencies and the `beamkit-mcp` entry point

**Files:**
- Modify: `pyproject.toml`
- Test: `tests/test_packaging.py`

**Interfaces:**
- Produces: console script `beamkit-mcp`; `mcp`, `requests`, `authlib`, `tomli` importable in `.venv`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_packaging.py
import sys
from pathlib import Path

if sys.version_info < (3, 11):
    import tomli as tomllib
else:
    import tomllib

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def test_runtime_dependencies_are_declared():
    proj = tomllib.loads(PYPROJECT.read_text())["project"]
    names = {d.split(";")[0].strip() for d in proj["dependencies"]}
    assert {"mcp", "requests", "authlib", "tomli"} <= names


def test_console_script_points_at_server_main():
    proj = tomllib.loads(PYPROJECT.read_text())["project"]
    assert proj["scripts"]["beamkit-mcp"] == "beamkit.server:main"


def test_dependencies_import():
    import authlib      # noqa: F401
    import mcp          # noqa: F401
    import requests     # noqa: F401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_packaging.py -q`
Expected: FAIL, `KeyError: 'scripts'` and `ModuleNotFoundError: No module named 'authlib'`.

- [ ] **Step 3: Edit `pyproject.toml`**

Replace the `[project]` block's `dependencies = []` and add a `[project.scripts]` table:

```toml
dependencies = ["mcp", "requests", "authlib", "tomli; python_version < '3.11'"]

[project.optional-dependencies]
dev = ["pytest>=7"]

[project.scripts]
beamkit-mcp = "beamkit.server:main"
```

Then install: `.venv/bin/python -m pip install -e ".[dev]"`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_packaging.py -q`
Expected: 3 passed. Then `.venv/bin/beamkit-mcp --help` is not expected to work (FastMCP has no `--help`); check the script exists: `ls .venv/bin/beamkit-mcp`.

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (the existing 222 plus 3).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml tests/test_packaging.py
git commit -m "feat(packaging): declare runtime deps and the beamkit-mcp entry point"
```

---

### Task 1: `paths.home()` resolves for laptops

**Files:**
- Modify: `src/beamkit/paths.py`
- Test: `tests/test_paths.py`

**Interfaces:**
- Produces: `paths.home() -> Path` with the three-way rule; `paths.FERMILAB_USERS = Path("/exp/mu2e/data/users")`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_paths.py`)

```python
def test_home_default_is_user_data_dir_when_the_fermilab_tree_exists(monkeypatch, tmp_path):
    monkeypatch.delenv("BEAMKIT_HOME")
    users = tmp_path / "users"
    users.mkdir()
    monkeypatch.setattr(paths, "FERMILAB_USERS", users)
    assert paths.home() == users / getpass.getuser() / "beamkit"


def test_home_default_is_dot_beamkit_off_fermilab(monkeypatch, tmp_path):
    monkeypatch.delenv("BEAMKIT_HOME")
    monkeypatch.setattr(paths, "FERMILAB_USERS", tmp_path / "absent")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "laptop"))
    assert paths.home() == tmp_path / "laptop" / ".beamkit"
```

Delete the existing `test_home_default_is_user_data_dir` (it assumes the Fermilab tree exists on the test host).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_paths.py -q`
Expected: FAIL, `AttributeError: module 'beamkit.paths' has no attribute 'FERMILAB_USERS'`.

- [ ] **Step 3: Implement**

Replace `home()` in `src/beamkit/paths.py`:

```python
FERMILAB_USERS = Path("/exp/mu2e/data/users")


def home() -> Path:
    """BEAMKIT_HOME; else the Fermilab per-user data dir when that tree
    exists on this host (existing runs stay where they are); else
    ~/.beamkit. One directory check, no probing beyond it."""
    env = os.environ.get("BEAMKIT_HOME")
    if env:
        return Path(env)
    if FERMILAB_USERS.is_dir():
        return FERMILAB_USERS / getpass.getuser() / "beamkit"
    return Path.home() / ".beamkit"
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_paths.py -q`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/beamkit/paths.py tests/test_paths.py
git commit -m "feat(paths): home resolves to ~/.beamkit off the Fermilab tree"
```

---

### Task 2: `nersc_config.py`

**Files:**
- Create: `src/beamkit/nersc_config.py`
- Test: `tests/test_nersc_config.py`

**Interfaces:**
- Produces:
  ```python
  REQUIRED = ("api", "sfapi_dir", "account", "base_dir", "qos", "owner")
  DEFAULTS = {"procs_per_node": 128,
              "image": "/cvmfs/singularity.opensciencegrid.org/fermilab/fnal-wn-el9:latest",
              "apptainer": "/cvmfs/oasis.opensciencegrid.org/mis/apptainer/current/bin/apptainer"}
  class ConfigError(BeamkitError)
  @dataclass(frozen=True) class NerscConfig: api: str; sfapi_dir: Path; account: str; base_dir: str; qos: str; owner: str; procs_per_node: int; image: str; apptainer: str
      def key_file(self) -> Path        # priv_key.pem or priv_key.jwk, mode-checked
      def client_id(self) -> str
      def as_record(self) -> dict       # every field, sfapi_dir as str
  def config_path(home) -> Path         # home / "nersc.toml"
  def load(home) -> NerscConfig
  ```

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_nersc_config.py
import os
from pathlib import Path

import pytest

from beamkit import nersc_config as nc

GOOD = """
api = "https://api.iri.nersc.gov/api/v2"
sfapi_dir = "{sfapi}"
account = "m4599"
base_dir = "/global/cfs/cdirs/m4599/Users/u/beamkit"
qos = "regular"
owner = "u"
"""


@pytest.fixture
def sfapi(tmp_path):
    d = tmp_path / "sfapi"
    d.mkdir()
    (d / "client_id").write_text("abcdefghijklm\n")
    key = d / "priv_key.pem"
    key.write_text("-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----\n")
    key.chmod(0o400)
    return d


def _write(home, text):
    home.mkdir(parents=True, exist_ok=True)
    (home / "nersc.toml").write_text(text)


def test_load_good(beamkit_home, sfapi):
    _write(beamkit_home, GOOD.format(sfapi=sfapi))
    cfg = nc.load(beamkit_home)
    assert cfg.account == "m4599" and cfg.owner == "u" and cfg.procs_per_node == 128
    assert cfg.image.endswith("fnal-wn-el9:latest") and cfg.apptainer.endswith("/bin/apptainer")
    assert cfg.key_file() == sfapi / "priv_key.pem" and cfg.client_id() == "abcdefghijklm"
    assert cfg.as_record()["sfapi_dir"] == str(sfapi)


def test_missing_file_lists_every_required_key(beamkit_home):
    with pytest.raises(nc.ConfigError) as e:
        nc.load(beamkit_home)
    for k in nc.REQUIRED:
        assert k in str(e.value)
    assert str(beamkit_home / "nersc.toml") in str(e.value)


def test_missing_key_is_named(beamkit_home, sfapi):
    _write(beamkit_home, GOOD.format(sfapi=sfapi).replace('qos = "regular"\n', ""))
    with pytest.raises(nc.ConfigError, match="qos"):
        nc.load(beamkit_home)


def test_unknown_key_is_refused(beamkit_home, sfapi):
    _write(beamkit_home, GOOD.format(sfapi=sfapi) + 'queue = "debug"\n')
    with pytest.raises(nc.ConfigError, match="unknown key 'queue'"):
        nc.load(beamkit_home)


def test_procs_per_node_must_be_a_positive_int(beamkit_home, sfapi):
    _write(beamkit_home, GOOD.format(sfapi=sfapi) + "procs_per_node = 0\n")
    with pytest.raises(nc.ConfigError, match="procs_per_node"):
        nc.load(beamkit_home)


def test_sfapi_dir_expands_tilde(beamkit_home, sfapi, monkeypatch):
    monkeypatch.setenv("HOME", str(sfapi.parent))
    _write(beamkit_home, GOOD.format(sfapi="~/sfapi"))
    assert nc.load(beamkit_home).sfapi_dir == sfapi


def test_key_file_readable_by_others_is_refused(beamkit_home, sfapi):
    (sfapi / "priv_key.pem").chmod(0o644)
    _write(beamkit_home, GOOD.format(sfapi=sfapi))
    with pytest.raises(nc.ConfigError, match="chmod 400"):
        nc.load(beamkit_home).key_file()


def test_jwk_key_is_accepted_and_pem_preferred(beamkit_home, sfapi):
    jwk = sfapi / "priv_key.jwk"
    jwk.write_text('{"kty": "RSA", "d": "x"}')
    jwk.chmod(0o400)
    _write(beamkit_home, GOOD.format(sfapi=sfapi))
    assert nc.load(beamkit_home).key_file() == sfapi / "priv_key.pem"
    (sfapi / "priv_key.pem").unlink()
    assert nc.load(beamkit_home).key_file() == jwk


def test_no_key_file_is_refused(beamkit_home, sfapi):
    (sfapi / "priv_key.pem").unlink()
    _write(beamkit_home, GOOD.format(sfapi=sfapi))
    with pytest.raises(nc.ConfigError, match="priv_key.pem or priv_key.jwk"):
        nc.load(beamkit_home).key_file()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_nersc_config.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'beamkit.nersc_config'`.

- [ ] **Step 3: Implement**

```python
# src/beamkit/nersc_config.py
"""$BEAMKIT_HOME/nersc.toml: everything the NERSC backend needs to know
about the facility and the caller's client. Loaded per tool call; a
missing or malformed file is refused with the full key list."""
import stat
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from beamkit import BeamkitError

if sys.version_info < (3, 11):
    import tomli as tomllib
else:
    import tomllib

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
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_nersc_config.py -q`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add src/beamkit/nersc_config.py tests/test_nersc_config.py
git commit -m "feat(nersc): nersc.toml loader with key-file permission check"
```

---

### Task 3: `iri.py`, the API client

**Files:**
- Create: `src/beamkit/iri.py`
- Create: `tests/fake_iri.py`
- Test: `tests/test_iri.py`

**Interfaces:**
- Consumes: `NerscConfig` (Task 2).
- Produces:
  ```python
  UPLOAD_MAX = 5_242_880
  TOKEN_URL = "https://oidc.nersc.gov/c2id/token"
  class IriError(BeamkitError): status: int | None; detail: str
  def sfapi_token(cfg) -> str                       # authlib client-credentials, PEM or JWK
  class IriClient:
      def __init__(self, cfg, token_provider=None, session=None)   # defaults: sfapi_token, requests.Session()
      def whoami(self) -> dict
      def resource_id(self, kind, name) -> str      # kind "compute" (GET) | "filesystem" (POST {})
      def mkdir(self, path) -> None
      def ls(self, path) -> list[dict]
      def exists(self, path) -> bool
      def upload(self, local, remote) -> None
      def download(self, remote) -> str
      def submit(self, spec, idem_key) -> str
      def status(self, job_id) -> dict              # the "status" sub-dict
      def wait_task(self, resp, timeout_s=600, poll_s=3) -> dict
  ```
- `tests/fake_iri.py`:
  ```python
  class FakeResponse: status_code, _json, text; ok; json()
  class FakeSession:
      def __init__(self): self.calls = []; self.files = {}; self.dirs = set(); self.jobs = {}; self.tasks = {}
      def request(self, method, url, **kw) -> FakeResponse
  ```
  `FakeSession` implements `/account/whoami`, `/compute/resources`, `/filesystem/resources`, `/filesystem/{mkdir,ls,upload,download}/{id}`, `/compute/job/{id}`, `/compute/status/{id}/{job}`, `/task/{id}`. Every filesystem op creates a task that completes on its first poll. `self.jobs[job_id] = {"state": "queued", "exit_code": 0, "spec": spec}`; tests mutate it to drive status.

- [ ] **Step 1: Write the fake**

```python
# tests/fake_iri.py
"""An in-memory stand-in for api.iri.nersc.gov/api/v2, shaped by the
responses recorded 2026-09-10/11 (plan ruling 4). Enough surface for
IriClient; nothing else."""
import json
import re
import uuid

CFS = "59e80c79-4dfd-4c53-9c07-7405685fcd37"
COMPUTE = "94351904-6dba-4c16-b5cd-fbd280d8615b"
BASE = "https://api.iri.nersc.gov/api/v2"


class FakeResponse:
    def __init__(self, status_code, body=None, text=None):
        self.status_code = status_code
        self._json = body
        self.text = text if text is not None else (json.dumps(body) if body is not None else "")

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


class FakeSession:
    def __init__(self):
        self.calls = []
        self.files = {}        # remote path -> bytes
        self.dirs = set()
        self.jobs = {}         # job id -> {"state", "exit_code", "spec"}
        self.tasks = {}        # task id -> result dict or ("failed", detail)
        self.next_job = 58197742
        self.fail_submit_at = None    # k-th submit (0-based) returns 500

    def _task(self, result):
        tid = str(uuid.uuid4())
        self.tasks[tid] = result
        return FakeResponse(200, {"task_id": tid, "task_uri": f"{BASE}/task/{tid}"})

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        path = url[len(BASE):]
        m = re.match(r"^/task/([^/]+)$", path)
        if m:
            r = self.tasks[m.group(1)]
            if isinstance(r, tuple):
                return FakeResponse(200, {"status": r[0], "result": {"error": r[1]}})
            return FakeResponse(200, {"status": "completed", "result": r})
        if path == "/account/whoami":
            return FakeResponse(200, {"name": "u", "uid": 1})
        if path == "/compute/resources":
            return FakeResponse(200, [{"id": "3cf3c048-855e-4dd8-a189-065a483954bb", "name": "jobs",
                                      "resource_type": "urn:doe-iri:resource:unknown", "current_status": "up",
                                      "description": "Slurm Commands"},
                                     {"id": COMPUTE, "name": "compute", "resource_type": "urn:doe-iri:resource:compute",
                                      "current_status": "up", "description": "Compute Nodes"}])
        if path == "/filesystem/resources":
            return FakeResponse(200, [{"id": CFS, "name": "cfs", "resource_type": None, "current_status": None}])
        m = re.match(rf"^/filesystem/(mkdir|ls|upload|download)/{CFS}$", path)
        if m:
            op = m.group(1)
            if op == "upload":
                remote = kw["params"]["path"]
                parent = remote.rsplit("/", 1)[0]
                if parent not in self.dirs:
                    return self._task(("failed", "Exception: Error: 400: Error downloading: No such file"))
                name, fh = kw["files"]["file"]
                self.files[remote] = fh.read()
                return self._task({"output": f"Uploaded {remote}"})
            p = kw["json"]["path"]
            if op == "mkdir":
                self.dirs.add(p)
                return self._task({"output": None})
            if op == "ls":
                if p in self.files:
                    return self._task({"output": [self._entry(p, "f")]})
                if p not in self.dirs:
                    return self._task(("failed", f"Exception: Error: 400: ls: cannot access '{p}': No such file or directory"))
                kids = [self._entry(d, "d") for d in self.dirs if d.rsplit("/", 1)[0] == p]
                kids += [self._entry(f, "f") for f in self.files if f.rsplit("/", 1)[0] == p]
                return self._task({"output": kids})
            if op == "download":
                if p not in self.files:
                    return self._task(("failed", "Exception: Error: 400: No such file"))
                return self._task({"output": self.files[p].decode()})
        if path == f"/compute/job/{COMPUTE}" and method == "POST":
            k = sum(1 for c in self.calls if c[1] == url and c[0] == "POST") - 1
            if self.fail_submit_at == k:
                return FakeResponse(500, {"type": "about:blank", "status": 500, "title": "Internal Server Error",
                                          "detail": "sbatch: error: Batch job submission failed"})
            jid = str(self.next_job)
            self.next_job += 1
            self.jobs[jid] = {"state": "queued", "exit_code": 0, "spec": kw["json"],
                              "idem": kw["headers"].get("Idempotency-Key")}
            return FakeResponse(200, {"id": jid})
        m = re.match(rf"^/compute/status/{COMPUTE}/(\d+)$", path)
        if m:
            j = self.jobs.get(m.group(1))
            if j is None:
                return FakeResponse(404, {"type": "about:blank", "status": 404, "title": "Not Found",
                                          "detail": f"job {m.group(1)} not found"})
            return FakeResponse(200, {"status": {"state": j["state"], "exit_code": j["exit_code"], "time": 0.0,
                                                 "message": None,
                                                 "meta_data": {"elapsed": "00:05:13", "nodelist": "nid004381",
                                                               "state": j["state"].upper(), "exitcode": f"{j['exit_code']}:0"}}})
        return FakeResponse(404, {"type": "about:blank", "status": 404, "title": "Not Found", "detail": path})

    def _entry(self, p, kind):
        size = len(self.files[p]) if kind == "f" else 4096
        return {"name": p, "type": kind, "link_target": "", "user": "u", "group": "m4599",
                "permissions": "644", "last_modified": "2026-09-11 07:03:50", "size": str(size)}
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_iri.py
import io
from pathlib import Path

import pytest

from beamkit import iri
from beamkit.nersc_config import NerscConfig
from tests.fake_iri import CFS, COMPUTE, FakeSession


@pytest.fixture
def cfg(tmp_path):
    return NerscConfig(api="https://api.iri.nersc.gov/api/v2", sfapi_dir=tmp_path, account="m4599",
                       base_dir="/global/cfs/cdirs/m4599/Users/u/beamkit", qos="regular", owner="u",
                       procs_per_node=128, image="/cvmfs/img", apptainer="/cvmfs/apptainer")


@pytest.fixture
def client(cfg):
    s = FakeSession()
    c = iri.IriClient(cfg, token_provider=lambda: "tok", session=s)
    c.fake = s
    return c


def test_bearer_header_on_every_call(client):
    client.whoami()
    method, url, kw = client.fake.calls[-1]
    assert kw["headers"]["Authorization"] == "Bearer tok" and url.endswith("/account/whoami")


def test_resource_ids_by_name(client):
    assert client.resource_id("compute", "compute") == COMPUTE
    assert client.resource_id("filesystem", "cfs") == CFS
    assert client.fake.calls[-1][0] == "POST"      # filesystem listing is POST


def test_unknown_resource_name_lists_what_exists(client):
    with pytest.raises(iri.IriError, match="jobs, compute"):
        client.resource_id("compute", "gpu")


def test_mkdir_ls_upload_download_round_trip(client, tmp_path):
    d = "/global/cfs/cdirs/m4599/Users/u/beamkit/runs/T.e470313"
    client.mkdir(d)
    assert client.exists(d) and not client.exists(d + "/nope")
    local = tmp_path / "job.sh"
    local.write_text("#!/bin/bash\necho hi\n")
    client.upload(local, d + "/job.sh")
    names = [e["name"] for e in client.ls(d)]
    assert names == [d + "/job.sh"]
    assert client.download(d + "/job.sh") == "#!/bin/bash\necho hi\n"


def test_upload_over_cap_is_refused_before_any_request(client, tmp_path):
    big = tmp_path / "big.tar"
    big.write_bytes(b"x" * (iri.UPLOAD_MAX + 1))
    n = len(client.fake.calls)
    with pytest.raises(iri.IriError, match="5242881 bytes exceeds the 5242880-byte upload cap"):
        client.upload(big, "/global/cfs/x/big.tar")
    assert len(client.fake.calls) == n


def test_upload_into_missing_dir_surfaces_the_task_error(client, tmp_path):
    local = tmp_path / "a"
    local.write_text("a")
    with pytest.raises(iri.IriError, match="No such file"):
        client.upload(local, "/global/cfs/absent/a")


def test_submit_and_status(client):
    jid = client.submit({"name": "x"}, idem_key="T.e470313/0")
    assert jid == "58197742"
    assert client.fake.jobs[jid]["idem"] == "T.e470313/0"
    assert client.status(jid)["state"] == "queued"
    client.fake.jobs[jid]["state"] = "completed"
    st = client.status(jid)
    assert st["state"] == "completed" and st["meta_data"]["nodelist"] == "nid004381"


def test_http_error_carries_status_and_detail(client):
    client.fake.fail_submit_at = 0
    with pytest.raises(iri.IriError) as e:
        client.submit({"name": "x"}, idem_key="k")
    assert e.value.status == 500 and "sbatch: error" in e.value.detail


def test_401_names_ip_pinning_and_client_lifetime(client, monkeypatch):
    def unauthorized(method, url, **kw):
        from tests.fake_iri import FakeResponse
        return FakeResponse(401, {"detail": "Facility Specific authentication failed: 403: Invalid token"})
    monkeypatch.setattr(client.fake, "request", unauthorized)
    with pytest.raises(iri.IriError, match="source IP.*48 h"):
        client.whoami()


def test_wait_task_times_out_with_the_task_id(client, monkeypatch):
    tid = "t-1"
    client.fake.tasks[tid] = None
    def running(method, url, **kw):
        from tests.fake_iri import FakeResponse
        return FakeResponse(200, {"status": "running", "result": None})
    monkeypatch.setattr(client.fake, "request", running)
    monkeypatch.setattr(iri.time, "sleep", lambda s: None)
    with pytest.raises(iri.IriError, match="task t-1 still running after 0 s"):
        client.wait_task({"task_id": tid, "task_uri": f"{client.cfg.api}/task/{tid}"}, timeout_s=0)


def test_sfapi_token_refuses_a_public_jwk(cfg, tmp_path):
    (tmp_path / "client_id").write_text("abcdefghijklm")
    jwk = tmp_path / "priv_key.jwk"
    jwk.write_text('{"kty": "RSA", "n": "x", "e": "AQAB"}')
    jwk.chmod(0o400)
    with pytest.raises(iri.IriError, match="PUBLIC JWK"):
        iri.sfapi_token(cfg)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_iri.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'beamkit.iri'`.

- [ ] **Step 4: Implement**

```python
# src/beamkit/iri.py
"""Thin client for the IRI Facility API v2 as NERSC serves it. Knows
nothing about beamkit runs: paths in, parsed JSON out, IriError on
anything that is not success. Token: NERSC Superfacility API client
credentials (Globus tokens are rejected by v2)."""
import json
import time
from pathlib import Path

from beamkit import BeamkitError

UPLOAD_MAX = 5_242_880
TOKEN_URL = "https://oidc.nersc.gov/c2id/token"
TERMINAL = ("completed", "failed", "canceled")
_AUTH_HINT = ("the API refused the token. Two usual causes: this host's source IP is not in the "
              "client's allow list (a red client is pinned to at most two IPs), or the client "
              "expired (48 h until NERSC's security review extends it to 30 d). Check the client in "
              "Iris -> Superfacility API Clients")


class IriError(BeamkitError):
    def __init__(self, msg, status=None, detail=""):
        super().__init__(msg)
        self.status = status
        self.detail = detail


def sfapi_token(cfg) -> str:
    """A 600 s access token from the client in cfg.sfapi_dir."""
    key_path = cfg.key_file()
    raw = key_path.read_text().strip()
    if raw.startswith("{"):
        key = json.loads(raw)
        if "d" not in key:
            raise IriError(f"{key_path} is a PUBLIC JWK (no 'd'); copy the private key from Iris")
    elif "PRIVATE KEY" in raw:
        key = raw
    else:
        raise IriError(f"{key_path} is neither a JWK nor a PEM private key")
    from authlib.integrations.requests_client import OAuth2Session
    from authlib.oauth2.rfc7523 import PrivateKeyJWT
    try:
        s = OAuth2Session(cfg.client_id(), key, PrivateKeyJWT(TOKEN_URL),
                          grant_type="client_credentials", token_endpoint=TOKEN_URL)
        tok = s.fetch_token()
    except Exception as e:
        raise IriError(f"token request to {TOKEN_URL} failed: {e}; {_AUTH_HINT}") from e
    return tok["access_token"]


def _detail(resp) -> str:
    try:
        body = resp.json()
    except Exception:
        return (resp.text or "")[:500]
    if isinstance(body, dict):
        return str(body.get("detail") or body.get("title") or body)[:500]
    return str(body)[:500]


class IriClient:
    def __init__(self, cfg, token_provider=None, session=None):
        self.cfg = cfg
        self._token_provider = token_provider or (lambda: sfapi_token(cfg))
        if session is None:
            import requests
            session = requests.Session()
        self._session = session
        self._token = None

    def _headers(self, extra=None):
        if self._token is None:
            self._token = self._token_provider()
        h = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        if extra:
            h.update(extra)
        return h

    def _req(self, method, path, headers=None, **kw):
        url = path if path.startswith("http") else f"{self.cfg.api}{path}"
        resp = self._session.request(method, url, headers=self._headers(headers), timeout=120, **kw)
        if not resp.ok:
            detail = _detail(resp)
            hint = f"; {_AUTH_HINT}" if resp.status_code in (401, 403) else ""
            raise IriError(f"{method} {path} -> {resp.status_code}: {detail}{hint}",
                           status=resp.status_code, detail=detail)
        return resp.json() if resp.text else {}

    # --- account and resources
    def whoami(self) -> dict:
        return self._req("GET", "/account/whoami")

    def resource_id(self, kind, name) -> str:
        if kind == "compute":
            rs = self._req("GET", "/compute/resources")
        elif kind == "filesystem":
            rs = self._req("POST", "/filesystem/resources", json={})
        else:
            raise IriError(f"resource kind must be 'compute' or 'filesystem', got {kind!r}")
        rs = rs if isinstance(rs, list) else rs.get("items", [])
        for r in rs:
            if r.get("name") == name:
                return r["id"]
        raise IriError(f"no {kind} resource named {name!r}; the facility lists: "
                       f"{', '.join(str(r.get('name')) for r in rs) or 'nothing'}")

    # --- tasks
    def wait_task(self, resp, timeout_s=600, poll_s=3) -> dict:
        tid, uri = resp["task_id"], resp["task_uri"]
        deadline = time.monotonic() + timeout_s
        while True:
            t = self._req("GET", uri)
            st = t.get("status")
            if st == "completed":
                return t.get("result") or {}
            if st in ("failed", "canceled"):
                err = (t.get("result") or {}).get("error", "")
                raise IriError(f"task {tid} {st}: {err}", detail=err)
            if time.monotonic() >= deadline:
                raise IriError(f"task {tid} still {st} after {timeout_s} s")
            time.sleep(poll_s)

    # --- filesystem
    def _fs(self):
        return self.resource_id("filesystem", "cfs")

    def mkdir(self, path) -> None:
        self.wait_task(self._req("POST", f"/filesystem/mkdir/{self._fs()}", json={"path": path}))

    def ls(self, path) -> list[dict]:
        return list(self.wait_task(self._req("POST", f"/filesystem/ls/{self._fs()}", json={"path": path}))
                    .get("output") or [])

    def exists(self, path) -> bool:
        try:
            self.ls(path)
            return True
        except IriError as e:
            if "No such file" in (e.detail or str(e)):
                return False
            raise

    def upload(self, local, remote) -> None:
        local = Path(local)
        size = local.stat().st_size
        if size > UPLOAD_MAX:
            raise IriError(f"{local}: {size} bytes exceeds the {UPLOAD_MAX}-byte upload cap of the API")
        with open(local, "rb") as fh:
            resp = self._req("POST", f"/filesystem/upload/{self._fs()}", params={"path": remote},
                             files={"file": (local.name, fh)})
        self.wait_task(resp)

    def download(self, remote) -> str:
        res = self.wait_task(self._req("POST", f"/filesystem/download/{self._fs()}", json={"path": remote}))
        out = res.get("output") if isinstance(res, dict) else res
        return out if isinstance(out, str) else json.dumps(out)

    # --- compute
    def _compute(self):
        return self.resource_id("compute", "compute")

    def submit(self, spec, idem_key) -> str:
        r = self._req("POST", f"/compute/job/{self._compute()}", headers={"Idempotency-Key": idem_key}, json=spec)
        jid = r.get("id") if isinstance(r, dict) else None
        if not jid:
            raise IriError(f"submit returned no job id: {r!r}")
        return str(jid)

    def status(self, job_id) -> dict:
        r = self._req("GET", f"/compute/status/{self._compute()}/{job_id}", params={"include_spec": "false"})
        st = r.get("status") if isinstance(r, dict) else None
        if not isinstance(st, dict) or "state" not in st:
            raise IriError(f"status of job {job_id} has no state: {r!r}")
        return st
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_iri.py -q`
Expected: 11 passed.

- [ ] **Step 6: Commit**

```bash
git add src/beamkit/iri.py tests/fake_iri.py tests/test_iri.py
git commit -m "feat(nersc): IRI Facility API client with sfapi client-credentials auth"
```

---

### Task 4: Node script templates and `g4bl_command`

**Files:**
- Create: `src/beamkit/nersc_templates.py`
- Create: `src/beamkit/templates/job.sh`, `src/beamkit/templates/inner.sh`, `src/beamkit/templates/beamfile.sh`
- Create: `tests/fixtures/nersc/job.sh.golden`, `tests/fixtures/nersc/inner.sh.golden`, `tests/fixtures/nersc/beamfile.sh.golden`
- Modify: `pyproject.toml` (package data)
- Test: `tests/test_nersc_templates.py`

**Interfaces:**
- Produces:
  ```python
  G4BL_RECIPE = ("unset SPACK_ENV PYTHONHOME PYTHONPATH PYTHONNOUSERSITE",
                 "source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh > /dev/null 2>&1",
                 'eval "$(spack load --sh g4beamline)"')
  def g4bl_command(main_input, first_event, num_events, histo_path, params=None, quote=shlex.quote) -> str
  def g4bl_script(main_input, first_event, num_events, histo_path, params=None) -> str   # == prodtools _g4bl_script
  def render(name, subs: dict) -> str          # templates/<name>, every @@KEY@@ replaced, unreplaced -> TemplateError
  def render_job(cfg, run_dir) -> str
  def render_inner(cfg, *, run_id, run_dir, owner, tag, dsconf, events_per_job, main_input, params) -> str
  def render_beamfile_sh(cfg, *, job_py) -> str
  class TemplateError(BeamkitError)
  ```

- [ ] **Step 1: Write the templates**

`src/beamkit/templates/job.sh`:

```bash
#!/bin/bash
# beamkit NERSC backend: one srun task = one g4bl index. Runs native on the
# SLES node and enters the Mu2e EL9 image itself (the API's container block
# cannot bind two paths). Rendered by beamkit; nothing is looked up at run time.
IDX=$((BK_OFFSET + SLURM_PROCID))
exec "@@APPTAINER@@" exec -B /cvmfs -B /global/cfs --env IDX=$IDX "@@IMAGE@@" /bin/bash "@@RUN_DIR@@/inner.sh"
```

`src/beamkit/templates/inner.sh`:

```bash
#!/bin/bash
# beamkit NERSC backend, inside fnal-wn-el9. Per-index log first, so 128
# tasks on one node never interleave; it lands whether or not g4bl succeeds.
SEQ=$(printf %08d "$IDX")
exec > "@@RUN_DIR@@/out/log.@@OWNER@@.@@TAG@@.@@DSCONF@@.$SEQ.log" 2>&1
set -x
W=/tmp/bk.@@RUN_ID@@.$IDX; mkdir -p "$W"; cd "$W" || exit 2
tar xf "@@RUN_DIR@@"/cnf.*.tar || exit 2
FIRST=$((IDX*@@EVENTS_PER_JOB@@+1))
HISTO="$W/nts.@@OWNER@@.@@TAG@@.@@DSCONF@@.$SEQ.root"
@@G4BL_RECIPE@@
cd work
@@G4BL_COMMAND@@
rc=$?
[ $rc -eq 0 ] && mv "$HISTO" "@@RUN_DIR@@/out/"
sha256sum "@@RUN_DIR@@/out/nts.@@OWNER@@.@@TAG@@.@@DSCONF@@.$SEQ.root" 2>/dev/null
echo "BK_DONE idx=$IDX rc=$rc"
rm -rf "$W"
exit $rc
```

`src/beamkit/templates/beamfile.sh`:

```bash
#!/bin/bash
# beamkit NERSC backend: build one beam file from the run's nts files, on
# the node, with uproot from the cvmfs ana environment. One process.
exec "@@APPTAINER@@" exec -B /cvmfs -B /global/cfs "@@IMAGE@@" \
  /cvmfs/mu2e.opensciencegrid.org/env/ana/2.8.0/bin/python "@@JOB_PY@@"
```

Add to `pyproject.toml` under `[tool.setuptools]`:

```toml
[tool.setuptools.package-data]
beamkit = ["templates/*"]
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_nersc_templates.py
from pathlib import Path

import pytest

from beamkit import nersc_templates as nt
from beamkit.nersc_config import NerscConfig

GOLD = Path(__file__).parent / "fixtures" / "nersc"
RUN_DIR = "/global/cfs/cdirs/m4599/Users/u/beamkit/runs/T.e470313"


@pytest.fixture
def cfg(tmp_path):
    return NerscConfig(api="https://api.iri.nersc.gov/api/v2", sfapi_dir=tmp_path, account="m4599",
                       base_dir="/global/cfs/cdirs/m4599/Users/u/beamkit", qos="regular", owner="u",
                       procs_per_node=128,
                       image="/cvmfs/singularity.opensciencegrid.org/fermilab/fnal-wn-el9:latest",
                       apptainer="/cvmfs/oasis.opensciencegrid.org/mis/apptainer/current/bin/apptainer")


def test_g4bl_script_matches_the_prodtools_shape():
    s = nt.g4bl_script("Mu2E.in", 11, 10, "/x/nts.root", {"epsMax": "0.01", "Beam_File": "a b"})
    assert s == ("unset SPACK_ENV PYTHONHOME PYTHONPATH PYTHONNOUSERSITE\n"
                 "source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh > /dev/null 2>&1\n"
                 'eval "$(spack load --sh g4beamline)"\n'
                 "cd work\n"
                 "g4bl Mu2E.in viewer=none First_Event=11 Num_Events=10 histoFile=/x/nts.root "
                 "Beam_File='a b' epsMax=0.01")


def test_g4bl_command_unquoted_keeps_shell_variables():
    c = nt.g4bl_command("Mu2E.in", "$FIRST", 10, "$HISTO", {"epsMax": "0.01"}, quote=str)
    assert c == "g4bl Mu2E.in viewer=none First_Event=$FIRST Num_Events=10 histoFile=$HISTO epsMax=0.01"


def test_render_refuses_a_leftover_placeholder():
    with pytest.raises(nt.TemplateError, match="@@IMAGE@@"):
        nt.render("job.sh", {"APPTAINER": "/a", "RUN_DIR": "/r"})


def test_render_refuses_an_unknown_key():
    with pytest.raises(nt.TemplateError, match="EXTRA"):
        nt.render("job.sh", {"APPTAINER": "/a", "RUN_DIR": "/r", "IMAGE": "/i", "EXTRA": "x"})


def test_job_sh_golden(cfg):
    assert nt.render_job(cfg, RUN_DIR) == (GOLD / "job.sh.golden").read_text()


def test_inner_sh_golden(cfg):
    got = nt.render_inner(cfg, run_id="T.e470313", run_dir=RUN_DIR, owner="u", tag="T", dsconf="e470313",
                          events_per_job=10, main_input="Mu2E.in", params={"epsMax": "0.01"})
    assert got == (GOLD / "inner.sh.golden").read_text()


def test_beamfile_sh_golden(cfg):
    got = nt.render_beamfile_sh(cfg, job_py=f"{RUN_DIR}/beamfiles/beamfile_job.bm.py")
    assert got == (GOLD / "beamfile.sh.golden").read_text()


def test_inner_sh_has_no_braces_that_bash_would_misread(cfg):
    got = nt.render_inner(cfg, run_id="T.e470313", run_dir=RUN_DIR, owner="u", tag="T", dsconf="e470313",
                          events_per_job=10, main_input="Mu2E.in", params=None)
    assert "@@" not in got and "${" not in got
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_nersc_templates.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'beamkit.nersc_templates'`.

- [ ] **Step 4: Implement**

```python
# src/beamkit/nersc_templates.py
"""The files beamkit puts on a Perlmutter node. Placeholders are @@NAME@@
(bash owns `$` and `{}`); every placeholder must be supplied and every
supplied key must be used. The g4bl lines are prodtools'
utils/runmu2e._g4bl_script, reproduced here because the NERSC path runs
no prodtools on the node; tests/_bridge_contract_probe.py holds the two
equal."""
import re
import shlex
from pathlib import Path

from beamkit import BeamkitError

TEMPLATES = Path(__file__).with_name("templates")
PLACEHOLDER = re.compile(r"@@([A-Z_]+)@@")
G4BL_RECIPE = (
    "unset SPACK_ENV PYTHONHOME PYTHONPATH PYTHONNOUSERSITE",
    "source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh > /dev/null 2>&1",
    'eval "$(spack load --sh g4beamline)"',
)


class TemplateError(BeamkitError):
    pass


def g4bl_command(main_input, first_event, num_events, histo_path, params=None, quote=shlex.quote) -> str:
    """prodtools' g4bl line. `quote` is shlex.quote for a literal path (the
    prodtools shape the contract test checks) or str when the template
    passes shell variables."""
    extra = "".join(f" {k}={quote(str(v))}" for k, v in sorted((params or {}).items()))
    return (f"g4bl {quote(main_input)} viewer=none First_Event={first_event} Num_Events={num_events} "
            f"histoFile={quote(histo_path)}" + extra)


def g4bl_script(main_input, first_event, num_events, histo_path, params=None) -> str:
    """Byte-equal to prodtools utils.runmu2e._g4bl_script for the same arguments."""
    return "\n".join((*G4BL_RECIPE, "cd work", g4bl_command(main_input, first_event, num_events, histo_path, params)))


def render(name, subs: dict) -> str:
    text = (TEMPLATES / name).read_text()
    wanted = set(PLACEHOLDER.findall(text))
    unknown = set(subs) - wanted
    if unknown:
        raise TemplateError(f"{name}: no placeholder for {sorted(unknown)[0]}")
    missing = wanted - set(subs)
    if missing:
        raise TemplateError(f"{name}: @@{sorted(missing)[0]}@@ not supplied")
    for k, v in subs.items():
        text = text.replace(f"@@{k}@@", str(v))
    return text


def render_job(cfg, run_dir) -> str:
    return render("job.sh", {"APPTAINER": cfg.apptainer, "IMAGE": cfg.image, "RUN_DIR": run_dir})


def render_inner(cfg, *, run_id, run_dir, owner, tag, dsconf, events_per_job, main_input, params) -> str:
    return render("inner.sh", {
        "RUN_ID": run_id, "RUN_DIR": run_dir, "OWNER": owner, "TAG": tag, "DSCONF": dsconf,
        "EVENTS_PER_JOB": int(events_per_job),
        "G4BL_RECIPE": "\n".join(G4BL_RECIPE),
        "G4BL_COMMAND": g4bl_command(main_input, "$FIRST", int(events_per_job), "$HISTO", params, quote=str),
    })


def render_beamfile_sh(cfg, *, job_py) -> str:
    return render("beamfile.sh", {"APPTAINER": cfg.apptainer, "IMAGE": cfg.image, "JOB_PY": job_py})
```

- [ ] **Step 5: Generate the golden files, then read them by eye**

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
from beamkit import nersc_templates as nt
from beamkit.nersc_config import NerscConfig
cfg = NerscConfig(api="https://api.iri.nersc.gov/api/v2", sfapi_dir=Path("/x"), account="m4599",
                  base_dir="/global/cfs/cdirs/m4599/Users/u/beamkit", qos="regular", owner="u", procs_per_node=128,
                  image="/cvmfs/singularity.opensciencegrid.org/fermilab/fnal-wn-el9:latest",
                  apptainer="/cvmfs/oasis.opensciencegrid.org/mis/apptainer/current/bin/apptainer")
R = "/global/cfs/cdirs/m4599/Users/u/beamkit/runs/T.e470313"
g = Path("tests/fixtures/nersc"); g.mkdir(parents=True, exist_ok=True)
(g / "job.sh.golden").write_text(nt.render_job(cfg, R))
(g / "inner.sh.golden").write_text(nt.render_inner(cfg, run_id="T.e470313", run_dir=R, owner="u", tag="T",
                                   dsconf="e470313", events_per_job=10, main_input="Mu2E.in", params={"epsMax": "0.01"}))
(g / "beamfile.sh.golden").write_text(nt.render_beamfile_sh(cfg, job_py=f"{R}/beamfiles/beamfile_job.bm.py"))
PY
cat tests/fixtures/nersc/inner.sh.golden
```

Expected `inner.sh.golden` g4bl line, exactly:
`g4bl Mu2E.in viewer=none First_Event=$FIRST Num_Events=10 histoFile=$HISTO epsMax=0.01`
and no `@@` anywhere. Run `bash -n tests/fixtures/nersc/inner.sh.golden` and `bash -n tests/fixtures/nersc/job.sh.golden`: both exit 0.

- [ ] **Step 6: Run tests**

Run: `.venv/bin/python -m pytest tests/test_nersc_templates.py -q`
Expected: 8 passed.

- [ ] **Step 7: Commit**

```bash
git add src/beamkit/nersc_templates.py src/beamkit/templates tests/fixtures/nersc tests/test_nersc_templates.py pyproject.toml
git commit -m "feat(nersc): node script templates and the g4bl command line"
```

---

### Task 5: Contract probe compares the g4bl recipe with prodtools

**Files:**
- Modify: `tests/_bridge_contract_probe.py`

**Interfaces:**
- Consumes: `nersc_templates.g4bl_script` (Task 4), prodtools `utils.runmu2e._g4bl_script(main_input, first_event, num_events, histo_path, params=None)`.

- [ ] **Step 1: Extend the probe**

Add after the `naming` comparisons in `tests/_bridge_contract_probe.py`:

```python
# The NERSC path runs no prodtools on the node, so beamkit carries the g4bl
# recipe itself. Same inputs, byte-equal script, or the two have drifted.
try:
    import utils.runmu2e as runmu2e            # noqa: E402
    from beamkit import nersc_templates        # noqa: E402
    args = ("Mu2E.in", 11, 10, "/abs/nts.u.T.e470313.00000001.root", {"epsMax": "0.01", "Beam_File": "a b"})
    theirs, ours = runmu2e._g4bl_script(*args[:4], params=args[4]), nersc_templates.g4bl_script(*args)
    if theirs != ours:
        failures.append(f"g4bl recipe drift:\nprodtools: {theirs!r}\nbeamkit:   {ours!r}")
except ImportError as e:
    failures.append(f"utils.runmu2e not importable for the g4bl recipe check: {e}")
```

- [ ] **Step 2: Run the contract test against the real prodtools**

Run: `BEAMKIT_PRODTOOLS_ROOT=/exp/mu2e/app/users/oksuzian/muse_050125/prodtools .venv/bin/python -m pytest tests/test_bridge_contract.py -q`
Expected: 1 passed. If it reports `utils.runmu2e not importable`, the probe's `MagicMock` list at the top needs the module the traceback names added (`sys.modules[name] = MagicMock()`), then rerun. If it reports `g4bl recipe drift`, fix `nersc_templates.g4bl_command` to match prodtools; never the other way round.

- [ ] **Step 3: Commit**

```bash
git add tests/_bridge_contract_probe.py
git commit -m "test(contract): beamkit's g4bl recipe must equal prodtools' _g4bl_script"
```

---

### Task 6: `site` on records and identity

**Files:**
- Modify: `src/beamkit/records.py`
- Modify: `src/beamkit/identity.py`
- Test: `tests/test_records.py`, `tests/test_identity.py`

**Interfaces:**
- Produces:
  ```python
  records.STATES = ("enqueue_failed", "created", "submitted", "needs_attention", "partially_submitted", "short", "complete")
  RunRecord.site: str = "fermilab"
  RunRecord.nersc: dict = field(default_factory=dict)
  identity.SITES = ("fermilab", "nersc")
  Identity.site: str = "fermilab"
  identity.resolve(run_as, confirm=False, *, writes=True, site="fermilab", owner=None) -> Identity
  ```
  For `site="nersc"`: `run_as` must be `"self"`, `owner` must be given (from `NerscConfig.owner`) and becomes `Identity.owner`; `dev_dir` is `None`. `for_record(rec)` carries `rec.site`.

- [ ] **Step 1: Write the failing tests** (append)

`tests/test_records.py`:

```python
def test_record_without_site_reads_as_fermilab(tmp_path):
    rec = _rec()
    d = rec.to_dict()
    del d["site"]
    del d["nersc"]
    (tmp_path / "T.e470313").mkdir()
    (tmp_path / "T.e470313" / "run.json").write_text(json.dumps(d))
    loaded = records.load("T.e470313", tmp_path)
    assert loaded.site == "fermilab" and loaded.nersc == {}


def test_nersc_block_round_trips(tmp_path):
    rec = _rec(state="partially_submitted")
    rec.site = "nersc"
    rec.nersc = {"run_dir": "/global/cfs/x", "jobs": [{"slurm_id": "1", "offset": 0, "count": 3}]}
    records.save(rec, tmp_path)
    assert records.load("T.e470313", tmp_path) == rec


def test_new_states_are_listable(tmp_path):
    for st in ("partially_submitted", "short", "complete"):
        records.save(_rec(run_id=f"T.{st}", state=st), tmp_path)
        assert [r.run_id for r in records.list_runs(tmp_path, state=st)] == [f"T.{st}"]
```

`tests/test_identity.py`:

```python
def test_nersc_site_takes_the_configured_owner():
    i = identity.resolve("self", site="nersc", owner="nersc_login")
    assert i.site == "nersc" and i.owner == "nersc_login" and i.dev_dir is None and i.mine is True


def test_nersc_site_refuses_mu2epro_even_confirmed():
    with pytest.raises(identity.IdentityError, match="site='nersc' accepts run_as='self' only"):
        identity.resolve("mu2epro", confirm=True, site="nersc", owner="x")


def test_nersc_site_needs_an_owner():
    with pytest.raises(identity.IdentityError, match="owner"):
        identity.resolve("self", site="nersc")


def test_unknown_site_refused():
    with pytest.raises(identity.IdentityError, match="site must be one of"):
        identity.resolve("self", site="ornl")


def test_fermilab_site_ignores_owner_argument():
    assert identity.resolve("self", owner="ignored").owner == "u"


def test_for_record_carries_site():
    class Rec:
        run_as, owner, site = "self", "n", "nersc"
    assert identity.for_record(Rec).site == "nersc"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_records.py tests/test_identity.py -q`
Expected: FAIL, `TypeError: __init__() got an unexpected keyword argument 'site'` and `resolve() got an unexpected keyword argument 'site'`.

- [ ] **Step 3: Implement records**

In `src/beamkit/records.py`: extend `STATES`, add two fields to `RunRecord` after `error`, and make `from_dict` tolerant:

```python
STATES = ("enqueue_failed", "created", "submitted", "needs_attention",
          "partially_submitted", "short", "complete")
```

```python
    error: str | None = None
    site: str = "fermilab"
    nersc: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "RunRecord":
        d = dict(d)
        d.setdefault("site", "fermilab")
        d.setdefault("nersc", {})
        return cls(**d)
```

Update the `STATES` comment above to say: `partially_submitted`, `short`, `complete` are NERSC-path states (a submit that stopped part way; every job terminal with fewer nts files than njobs; every job terminal with all nts files).

- [ ] **Step 4: Implement identity**

In `src/beamkit/identity.py`:

```python
RUN_AS = ("self", "mu2epro")
SITES = ("fermilab", "nersc")
```

```python
@dataclass(frozen=True)
class Identity:
    run_as: str
    owner: str
    dev_dir: Optional[str]
    site: str = "fermilab"
```

```python
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
        return Identity(run_as="self", owner=owner, dev_dir=None, site="nersc")
    ident = Identity(run_as=run_as, owner="mu2e" if run_as == "mu2epro" else _username(),
                     dev_dir=dev_dir_from_env())
    if writes and ident.production and not confirm:
        raise IdentityError("run_as='mu2epro' registers artifacts in production SAM and submits "
                            "production grid jobs; pass confirm=True")
    return ident


def for_record(rec) -> Identity:
    """The identity a run was created as, from its record. No environment
    is consulted: the record is the truth about who owns the run."""
    return Identity(run_as=rec.run_as, owner=rec.owner, dev_dir=None, site=getattr(rec, "site", "fermilab"))
```

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/beamkit/records.py src/beamkit/identity.py tests/test_records.py tests/test_identity.py
git commit -m "feat: site on run records and identities; NERSC states"
```

---

### Task 7: `nersc_cnf.py`, the cnf tarball built locally

**Files:**
- Create: `src/beamkit/nersc_cnf.py`
- Test: `tests/test_nersc_cnf.py`

**Interfaces:**
- Consumes: `naming.cnf_name`, `naming.dataset`, `compose.validate_params`.
- Produces:
  ```python
  VCS_DIRS = (".git", ".svn", ".hg")
  class CnfError(BeamkitError)
  def jobpars(*, owner, tag, dsconf, main_input, events_per_job, njobs, params) -> dict
  def build_cnf(deck_dir, jobpars: dict, out_path) -> Path      # tar: work/<deck files> + jobpars.json; refused > iri.UPLOAD_MAX
  ```
  `jobpars` keys, in this order: `runner="g4bl"`, `desc`, `dsconf`, `main_input`, `events_per_job`, `njobs`, `owner`, `g4bl_params` (only when params is non-empty), `tbs={"njobs": njobs, "outfiles": {"g4bl": f"nts.owner.{tag}.version.sequencer.root"}}` — the same shape prodtools' `_build_g4bl_tarball` writes, so a later harvest reads it as a cnf.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_nersc_cnf.py
import json
import tarfile

import pytest

from beamkit import iri, nersc_cnf


@pytest.fixture
def deck(tmp_path):
    d = tmp_path / "deck"
    (d / "Geometry").mkdir(parents=True)
    (d / ".git").mkdir()
    (d / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (d / "Mu2E.in").write_text("param -unset First_Event=1\n")
    (d / "Geometry" / "PS.txt").write_text("box\n")
    return d


def test_jobpars_shape():
    jp = nersc_cnf.jobpars(owner="u", tag="T", dsconf="e470313", main_input="Mu2E.in",
                           events_per_job=10, njobs=3, params={"epsMax": "0.01"})
    assert list(jp) == ["runner", "desc", "dsconf", "main_input", "events_per_job", "njobs", "owner",
                        "g4bl_params", "tbs"]
    assert jp["tbs"] == {"njobs": 3, "outfiles": {"g4bl": "nts.owner.T.version.sequencer.root"}}
    assert "g4bl_params" not in nersc_cnf.jobpars(owner="u", tag="T", dsconf="e", main_input="M",
                                                   events_per_job=1, njobs=1, params={})


def test_build_cnf_layout_and_vcs_exclusion(deck, tmp_path):
    jp = nersc_cnf.jobpars(owner="u", tag="T", dsconf="e470313", main_input="Mu2E.in",
                           events_per_job=10, njobs=3, params={})
    out = nersc_cnf.build_cnf(deck, jp, tmp_path / "cnf.u.T.e470313.0.tar")
    with tarfile.open(out) as t:
        names = sorted(t.getnames())
        assert names == ["jobpars.json", "work", "work/Geometry", "work/Geometry/PS.txt", "work/Mu2E.in"]
        assert json.loads(t.extractfile("jobpars.json").read()) == jp


def test_build_cnf_refuses_missing_main_input(deck, tmp_path):
    jp = nersc_cnf.jobpars(owner="u", tag="T", dsconf="e", main_input="Nope.in",
                           events_per_job=1, njobs=1, params={})
    with pytest.raises(nersc_cnf.CnfError, match="Nope.in"):
        nersc_cnf.build_cnf(deck, jp, tmp_path / "c.tar")


def test_build_cnf_refuses_over_the_upload_cap(deck, tmp_path, monkeypatch):
    (deck / "big.bin").write_bytes(b"x" * 100)
    monkeypatch.setattr(iri, "UPLOAD_MAX", 50)
    jp = nersc_cnf.jobpars(owner="u", tag="T", dsconf="e", main_input="Mu2E.in",
                           events_per_job=1, njobs=1, params={})
    with pytest.raises(nersc_cnf.CnfError, match="exceeds the 50-byte upload cap"):
        nersc_cnf.build_cnf(deck, jp, tmp_path / "c.tar")
    assert not (tmp_path / "c.tar").exists()


def test_build_cnf_never_overwrites(deck, tmp_path):
    jp = nersc_cnf.jobpars(owner="u", tag="T", dsconf="e", main_input="Mu2E.in",
                           events_per_job=1, njobs=1, params={})
    out = nersc_cnf.build_cnf(deck, jp, tmp_path / "c.tar")
    with pytest.raises(nersc_cnf.CnfError, match="exists"):
        nersc_cnf.build_cnf(deck, jp, out)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_nersc_cnf.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'beamkit.nersc_cnf'`.

- [ ] **Step 3: Implement**

```python
# src/beamkit/nersc_cnf.py
"""The cnf tarball for a NERSC run, built on the caller's machine: work/
(the deck without VCS internals) plus jobpars.json in the shape prodtools'
json2jobdef._build_g4bl_tarball writes, so a later harvest can declare it
as the parent of every nts file unchanged."""
import json
import os
import tarfile
from pathlib import Path

from beamkit import BeamkitError, iri

VCS_DIRS = (".git", ".svn", ".hg")


class CnfError(BeamkitError):
    pass


def jobpars(*, owner, tag, dsconf, main_input, events_per_job, njobs, params) -> dict:
    jp = {"runner": "g4bl", "desc": tag, "dsconf": dsconf, "main_input": main_input,
          "events_per_job": int(events_per_job), "njobs": int(njobs), "owner": owner}
    if params:
        jp["g4bl_params"] = dict(params)
    jp["tbs"] = {"njobs": int(njobs), "outfiles": {"g4bl": f"nts.owner.{tag}.version.sequencer.root"}}
    return jp


def _skip_vcs(ti):
    return None if any(part in VCS_DIRS for part in ti.name.split("/")) else ti


def build_cnf(deck_dir, jobpars: dict, out_path) -> Path:
    deck_dir, out_path = Path(deck_dir), Path(out_path)
    if not (deck_dir / jobpars["main_input"]).is_file():
        raise CnfError(f"main_input {jobpars['main_input']!r} not found in deck dir {deck_dir}")
    if out_path.exists():
        raise CnfError(f"{out_path} exists; a cnf is never overwritten")
    part = out_path.with_name(out_path.name + ".part")
    jp_path = out_path.with_name("jobpars.json")
    jp_path.write_text(json.dumps(jobpars, indent=2) + "\n")
    try:
        with tarfile.open(part, "w") as t:
            t.add(deck_dir, arcname="work", filter=_skip_vcs)
            t.add(jp_path, arcname="jobpars.json")
        size = part.stat().st_size
        if size > iri.UPLOAD_MAX:
            raise CnfError(f"{out_path.name}: {size} bytes exceeds the {iri.UPLOAD_MAX}-byte upload cap of the "
                           f"API; shrink the deck (large geometry or beam files do not belong in the cnf)")
        os.replace(part, out_path)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    finally:
        jp_path.unlink(missing_ok=True)
    return out_path
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_nersc_cnf.py -q`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/beamkit/nersc_cnf.py tests/test_nersc_cnf.py
git commit -m "feat(nersc): build the cnf tarball locally in prodtools' shape"
```

---

### Task 8: `backends/nersc.py` arithmetic: slices, duration, JobSpec

**Files:**
- Create: `src/beamkit/backends/__init__.py`
- Create: `src/beamkit/backends/nersc.py` (first part)
- Test: `tests/test_backends_nersc_spec.py`

**Interfaces:**
- Produces:
  ```python
  # backends/__init__.py
  SITES = ("fermilab", "nersc")
  def validate_site(site) -> str            # BeamkitError otherwise
  # backends/nersc.py
  WALLTIME_DEFAULT = 172800
  CUSTOM_ATTRIBUTES = {"constraint": "cpu", "licenses": "cvmfs", "module": "cvmfs"}
  def slices(njobs, procs_per_node) -> list[tuple[int, int]]     # [(offset, count), ...]
  def duration_s(events_per_job, walltime_s) -> int              # min(walltime_s, events_per_job*2 + 900)
  def validate_walltime(walltime_s) -> int                       # positive int
  def job_spec(cfg, *, run_id, run_dir, offset, count, duration) -> dict
  ```

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_backends_nersc_spec.py
import pytest

from beamkit import BeamkitError, backends
from beamkit.backends import nersc
from beamkit.nersc_config import NerscConfig

RUN_DIR = "/global/cfs/cdirs/m4599/Users/u/beamkit/runs/T.e470313"


@pytest.fixture
def cfg(tmp_path):
    return NerscConfig(api="https://api.iri.nersc.gov/api/v2", sfapi_dir=tmp_path, account="m4599",
                       base_dir="/global/cfs/cdirs/m4599/Users/u/beamkit", qos="debug", owner="u",
                       procs_per_node=128, image="/cvmfs/img", apptainer="/cvmfs/apptainer")


@pytest.mark.parametrize("njobs,ppn,want", [
    (1, 128, [(0, 1)]),
    (128, 128, [(0, 128)]),
    (129, 128, [(0, 128), (128, 1)]),
    (300, 128, [(0, 128), (128, 128), (256, 44)]),
    (5, 2, [(0, 2), (2, 2), (4, 1)]),
])
def test_slices(njobs, ppn, want):
    assert nersc.slices(njobs, ppn) == want


def test_slices_cover_every_index_once():
    s = nersc.slices(10000, 128)
    assert sum(c for _, c in s) == 10000 and len(s) == 79 and s[-1] == (9984, 16)


def test_duration_formula_and_cap():
    assert nersc.duration_s(1000, 172800) == 2900
    assert nersc.duration_s(1000, 1800) == 1800


@pytest.mark.parametrize("bad", [0, -1, 1.5, "3600", True])
def test_walltime_refused(bad):
    with pytest.raises(BeamkitError, match="walltime_s"):
        nersc.validate_walltime(bad)


def test_job_spec_shape(cfg):
    spec = nersc.job_spec(cfg, run_id="T.e470313", run_dir=RUN_DIR, offset=128, count=44, duration=2900)
    assert spec == {
        "name": "beamkit.T.e470313.128",
        "executable": "/bin/bash",
        "arguments": [f"{RUN_DIR}/job.sh"],
        "directory": RUN_DIR,
        "stdout_path": f"{RUN_DIR}/slurm/128.out",
        "stderr_path": f"{RUN_DIR}/slurm/128.err",
        "inherit_environment": False,
        "environment": {"BK_OFFSET": "128"},
        "resources": {"node_count": 1, "process_count": 44, "processes_per_node": 44,
                      "cpu_cores_per_process": 1, "exclusive_node_use": True},
        "attributes": {"duration": 2900, "queue_name": "debug", "account": "m4599",
                       "custom_attributes": {"constraint": "cpu", "licenses": "cvmfs", "module": "cvmfs"}},
    }


def test_validate_site():
    assert backends.validate_site("nersc") == "nersc"
    with pytest.raises(BeamkitError, match="site must be one of"):
        backends.validate_site("ornl")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_backends_nersc_spec.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'beamkit.backends'`.

- [ ] **Step 3: Implement**

```python
# src/beamkit/backends/__init__.py
"""Where a run executes. 'fermilab' is the prodtools path in tools.py;
'nersc' is backends/nersc.py. tools.py asks validate_site once per call."""
from beamkit import BeamkitError

SITES = ("fermilab", "nersc")


def validate_site(site) -> str:
    if site not in SITES:
        raise BeamkitError(f"site must be one of {SITES}, got {site!r}")
    return site
```

```python
# src/beamkit/backends/nersc.py
"""The NERSC backend: a run is a directory on CFS plus one Slurm job per
slice of procs_per_node indices, driven through the IRI Facility API.
Nothing here touches SAM, dCache, prodtools or a ledger."""
import math

from beamkit import BeamkitError

WALLTIME_DEFAULT = 172800
CUSTOM_ATTRIBUTES = {"constraint": "cpu", "licenses": "cvmfs", "module": "cvmfs"}


def slices(njobs, procs_per_node) -> list[tuple[int, int]]:
    """(offset, count) per Slurm job; index = offset + SLURM_PROCID."""
    return [(k * procs_per_node, min(procs_per_node, njobs - k * procs_per_node))
            for k in range(math.ceil(njobs / procs_per_node))]


def validate_walltime(walltime_s) -> int:
    if isinstance(walltime_s, bool) or not isinstance(walltime_s, int) or walltime_s < 1:
        raise BeamkitError(f"walltime_s must be a positive integer number of seconds, got {walltime_s!r}")
    return walltime_s


def duration_s(events_per_job, walltime_s) -> int:
    """About 1.4 s/event on the e470313 deck, doubled, plus 15 min for cold
    cvmfs and the apptainer start; capped by the caller's walltime."""
    return min(validate_walltime(walltime_s), int(events_per_job) * 2 + 900)


def job_spec(cfg, *, run_id, run_dir, offset, count, duration) -> dict:
    return {
        "name": f"beamkit.{run_id}.{offset}",
        "executable": "/bin/bash",
        "arguments": [f"{run_dir}/job.sh"],
        "directory": run_dir,
        "stdout_path": f"{run_dir}/slurm/{offset}.out",
        "stderr_path": f"{run_dir}/slurm/{offset}.err",
        "inherit_environment": False,
        "environment": {"BK_OFFSET": str(offset)},
        "resources": {"node_count": 1, "process_count": count, "processes_per_node": count,
                      "cpu_cores_per_process": 1, "exclusive_node_use": True},
        "attributes": {"duration": int(duration), "queue_name": cfg.qos, "account": cfg.account,
                       "custom_attributes": dict(CUSTOM_ATTRIBUTES)},
    }
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_backends_nersc_spec.py -q`
Expected: 13 passed.

- [ ] **Step 5: Commit**

```bash
git add src/beamkit/backends tests/test_backends_nersc_spec.py
git commit -m "feat(nersc): slice arithmetic, duration rule and the Slurm JobSpec"
```

---

### Task 9: `run_beamline` and `submit_run` on the NERSC backend, and the `site` dispatch in tools.py

**Files:**
- Modify: `src/beamkit/decks.py` (add `pin`)
- Modify: `src/beamkit/backends/nersc.py`
- Modify: `src/beamkit/tools.py`
- Create: `tests/test_backends_nersc_run.py`
- Modify: `tests/test_tools.py` (one test)

**Interfaces:**
- Consumes: Tasks 2 to 8; `decks.materialize`, `decks.inspect_local`, `naming.*`, `compose.validate_inputs`, `records.*`, `paths.*`.
- Produces:
  ```python
  # decks.py
  def pin(deck_ref, deck_dir, deck_url, cache_dir, production) -> DeckPin   # the body of tools._pin today
  # backends/nersc.py
  make_client = iri.IriClient                      # tests monkeypatch this
  def run_dir(cfg, run_id) -> str                  # f"{cfg.base_dir}/runs/{run_id}"
  def run_beamline(*, tag, run_as, deck_ref, params, events_per_job, njobs, main_input, outloc, dsconf,
                   submit, deck_dir, deck_url, walltime_s) -> dict
  def submit_run(run_id, run_as) -> dict
  # tools.py
  def run_beamline(..., site: str = "fermilab", walltime_s: int = 172800) -> dict
  def submit_run(run_id: str, run_as: str) -> dict
  ```
  Record for a NERSC run: `site="nersc"`, `campaign_id=None`, `tarball=None`, `datasets=[nts.<owner>.<tag>.<dsconf>.root]`, `nersc={"run_dir", "cnf", "walltime_s", "jobs": [{"slurm_id", "offset", "count", "submitted"}], "config": cfg.as_record()}`, `prodtools={}`.

- [ ] **Step 1: Move `_pin` into decks**

In `src/beamkit/decks.py` add:

```python
def pin(deck_ref, deck_dir, deck_url, cache_dir, production) -> DeckPin:
    """Exactly one of deck_ref (materialized into cache_dir) or deck_dir (a
    local git checkout, development only)."""
    if (deck_ref is None) == (deck_dir is None):
        raise DeckError("pass exactly one of deck_ref (a commit sha or tag) or deck_dir (a local checkout)")
    if deck_dir is None:
        return materialize(deck_url, deck_ref, cache_dir)
    if production:
        raise DeckError("deck_dir is a development option: run_as='self' only")
    p = inspect_local(deck_dir)
    if p.sha is None:
        raise DeckError(f"deck_dir {deck_dir} is not a git checkout; the dsconf is derived from the commit")
    return p
```

In `src/beamkit/tools.py` replace the body of `_pin` with:

```python
def _pin(deck_ref, deck_dir, deck_url, ident):
    return decks.pin(deck_ref, deck_dir, deck_url, paths.decks_dir(), ident.production)
```

Run: `.venv/bin/python -m pytest tests/test_tools.py -q`. Expected: all pass (the messages are unchanged; `DeckError` is a `BeamkitError`).

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_backends_nersc_run.py
import json
import tarfile

import pytest

from beamkit import BeamkitError, decks, iri, tools
from beamkit.backends import nersc
from tests.fake_iri import FakeSession

SHA = "e470313" + "0" * 33
BASE = "/global/cfs/cdirs/m4599/Users/u/beamkit"


@pytest.fixture
def deck(tmp_path):
    d = tmp_path / "deckcache" / SHA[:12]
    d.mkdir(parents=True)
    (d / "Mu2E.in").write_text("param -unset First_Event=1\n")
    return d


@pytest.fixture
def nersc_home(beamkit_home, tmp_path):
    sfapi = tmp_path / "sfapi"
    sfapi.mkdir()
    (sfapi / "client_id").write_text("abcdefghijklm")
    key = sfapi / "priv_key.pem"
    key.write_text("-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----\n")
    key.chmod(0o400)
    beamkit_home.mkdir(parents=True)
    (beamkit_home / "nersc.toml").write_text(
        f'api = "https://api.iri.nersc.gov/api/v2"\nsfapi_dir = "{sfapi}"\naccount = "m4599"\n'
        f'base_dir = "{BASE}"\nqos = "debug"\nowner = "u"\nprocs_per_node = 128\n')
    return beamkit_home


@pytest.fixture
def fake(monkeypatch, deck, nersc_home):
    s = FakeSession()
    s.dirs.update({BASE, BASE + "/runs"})
    made = []
    def make_client(cfg):
        made.append(cfg)
        return iri.IriClient(cfg, token_provider=lambda: "tok", session=s)
    monkeypatch.setattr(nersc, "make_client", make_client)
    monkeypatch.setattr(decks, "materialize", lambda url, ref, cache: decks.DeckPin(url, ref, SHA, str(deck), True))
    s.made = made
    return s


def _run(**kw):
    base = dict(tag="T", deck_ref=SHA, run_as="self", events_per_job=10, njobs=300, site="nersc")
    base.update(kw)
    return tools.run_beamline(**base)


def test_happy_path_layout_record_and_jobs(fake, nersc_home):
    rec = _run()
    rd = f"{BASE}/runs/T.e470313"
    assert rec["site"] == "nersc" and rec["state"] == "submitted" and rec["campaign_id"] is None
    assert rec["owner"] == "u" and rec["datasets"] == ["nts.u.T.e470313.root"]
    assert rec["nersc"]["run_dir"] == rd and rec["nersc"]["cnf"] == "cnf.u.T.e470313.0.tar"
    assert {rd, rd + "/out", rd + "/slurm", rd + "/beamfiles"} <= fake.dirs
    assert set(fake.files) == {rd + "/cnf.u.T.e470313.0.tar", rd + "/job.sh", rd + "/inner.sh"}
    assert b"BK_DONE" in fake.files[rd + "/inner.sh"]
    jobs = rec["nersc"]["jobs"]
    assert [(j["offset"], j["count"]) for j in jobs] == [(0, 128), (128, 128), (256, 44)]
    assert [j["slurm_id"] for j in jobs] == ["58197742", "58197743", "58197744"]
    spec = fake.jobs["58197743"]["spec"]
    assert spec["environment"] == {"BK_OFFSET": "128"} and spec["resources"]["process_count"] == 128
    assert spec["attributes"]["duration"] == 920 and spec["attributes"]["queue_name"] == "debug"
    assert fake.jobs["58197743"]["idem"] == "T.e470313/128"
    # the local copy of the cnf is kept next to the record
    local = nersc_home / "runs" / "T.e470313" / "cnf.u.T.e470313.0.tar"
    with tarfile.open(local) as t:
        assert "jobpars.json" in t.getnames() and "work/Mu2E.in" in t.getnames()
    assert json.loads((nersc_home / "runs" / "T.e470313" / "run.json").read_text())["nersc"]["walltime_s"] == 172800


def test_dsconf_collision_probes_the_remote_run_dir(fake):
    fake.dirs.add(f"{BASE}/runs/T.e470313")
    rec = _run(njobs=1)
    assert rec["dsconf"] == "e470313-001" and rec["nersc"]["run_dir"] == f"{BASE}/runs/T.e470313-001"


def test_submit_false_then_submit_run(fake):
    rec = _run(njobs=5, submit=False)
    assert rec["state"] == "created" and rec["nersc"]["jobs"] == [] and fake.jobs == {}
    rec = tools.submit_run("T.e470313", "self")
    assert rec["state"] == "submitted" and len(rec["nersc"]["jobs"]) == 1


def test_partial_submit_is_recorded_and_resumable(fake):
    fake.fail_submit_at = 1
    with pytest.raises(BeamkitError, match="job 1 of 3.*sbatch: error"):
        _run()
    rec = tools.beamline_status("T.e470313")["record"]
    assert rec["state"] == "partially_submitted" and [j["offset"] for j in rec["nersc"]["jobs"]] == [0]
    fake.fail_submit_at = None
    rec = tools.submit_run("T.e470313", "self")
    assert rec["state"] == "submitted" and [j["offset"] for j in rec["nersc"]["jobs"]] == [0, 128, 256]


def test_submit_run_refuses_a_submitted_run(fake):
    _run(njobs=1)
    with pytest.raises(BeamkitError, match="state 'submitted'"):
        tools.submit_run("T.e470313", "self")


def test_mu2epro_refused_before_any_client(fake):
    with pytest.raises(BeamkitError, match="run_as='self' only"):
        _run(run_as="mu2epro", confirm=True)
    assert fake.made == []


def test_outloc_other_than_scratch_refused(fake):
    with pytest.raises(BeamkitError, match="outputs of a NERSC run stay on CFS"):
        _run(outloc="disk")
    assert fake.made == []


def test_bad_walltime_refused_before_any_client(fake):
    with pytest.raises(BeamkitError, match="walltime_s"):
        _run(walltime_s=0)
    assert fake.made == []


def test_missing_config_is_named(beamkit_home, fake):
    (beamkit_home / "nersc.toml").unlink()
    with pytest.raises(BeamkitError, match="nersc.toml"):
        _run()


def test_upload_failure_leaves_a_retryable_record(fake, monkeypatch):
    monkeypatch.setattr(iri, "UPLOAD_MAX", 10)
    with pytest.raises(BeamkitError, match="upload cap"):
        _run(njobs=1)
    rec = tools.beamline_status("T.e470313")["record"]
    assert rec["state"] == "enqueue_failed" and rec["nersc"]["jobs"] == []
    monkeypatch.setattr(iri, "UPLOAD_MAX", 5_242_880)
    assert _run(njobs=1)["state"] == "submitted"


def test_local_run_dir_from_a_submitted_run_is_never_reused(fake):
    _run(njobs=1)
    fake.dirs.discard(f"{BASE}/runs/T.e470313")      # remote gone, local record remains
    with pytest.raises(BeamkitError, match="already exists; a run id is never reused"):
        _run(njobs=1)
```

Append to `tests/test_tools.py`:

```python
def test_fermilab_site_refuses_a_walltime(fake_bridge):
    with pytest.raises(BeamkitError, match="walltime_s applies to site='nersc' only"):
        _run(walltime_s=3600)
    assert fake_bridge["push_cnf"] == []
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_backends_nersc_run.py tests/test_tools.py::test_fermilab_site_refuses_a_walltime -q`
Expected: FAIL, `TypeError: run_beamline() got an unexpected keyword argument 'site'`.

- [ ] **Step 4: Implement the backend orchestration**

Append to `src/beamkit/backends/nersc.py`:

```python
from beamkit import (__version__, compose, decks, identity, iri, naming, nersc_cnf, nersc_config,
                     nersc_templates, paths, records)
from beamkit.decks import DEFAULT_DECK_URL   # noqa: F401  (kept for callers that pass the default)

make_client = iri.IriClient
SUBMITTABLE = ("created", "partially_submitted")


def run_dir(cfg, run_id) -> str:
    return f"{cfg.base_dir}/runs/{run_id}"


def _taken(cfg, client, tag):
    """allocate_dsconf's probe: a cnf name is taken when its run directory
    exists on CFS. The dsconf is the fourth dot-field of the cnf name."""
    def taken(cnf_name):
        dsconf = cnf_name.split(".")[3]
        return client.exists(run_dir(cfg, naming.run_id(tag, dsconf)))
    return taken


def _retryable(run_id, runs_dir) -> bool:
    try:
        rec = records.load(run_id, runs_dir)
    except records.RecordError:
        return False
    return rec.site == "nersc" and rec.state == "enqueue_failed" and not rec.nersc.get("jobs")


def _remote_layout(client, rd, uploads):
    for d in (rd, f"{rd}/out", f"{rd}/slurm", f"{rd}/beamfiles"):
        client.mkdir(d)
    for local, remote in uploads:
        client.upload(local, remote)


def _submit_missing(rec, cfg, client, runs_dir):
    """Submit every slice whose offset the record does not carry, in order.
    A failure part way is saved as partially_submitted and raised; the next
    submit_run continues from there."""
    have = {j["offset"] for j in rec.nersc["jobs"]}
    todo = [(o, c) for o, c in slices(rec.njobs, cfg.procs_per_node) if o not in have]
    total = len(slices(rec.njobs, cfg.procs_per_node))
    rd = rec.nersc["run_dir"]
    dur = duration_s(rec.events_per_job, rec.nersc["walltime_s"])
    for k, (offset, count) in enumerate(todo, start=len(have)):
        spec = job_spec(cfg, run_id=rec.run_id, run_dir=rd, offset=offset, count=count, duration=dur)
        try:
            jid = client.submit(spec, idem_key=f"{rec.run_id}/{offset}")
        except iri.IriError as e:
            rec.state, rec.error = "partially_submitted", f"job {k} of {total} (offset {offset}) failed to submit: {e}"
            records.save(rec, runs_dir)
            raise BeamkitError(f"run {rec.run_id}: job {k} of {total} (offset {offset}) failed to submit: {e}; "
                               f"{len(rec.nersc['jobs'])} job(s) are running; submit_run({rec.run_id!r}, "
                               f"'self') submits the rest") from e
        rec.nersc["jobs"].append({"slurm_id": jid, "offset": offset, "count": count,
                                  "submitted": records.now_utc()})
        records.save(rec, runs_dir)
    rec.state, rec.error = "submitted", None
    records.save(rec, runs_dir)


def run_beamline(*, tag, run_as, deck_ref, params, events_per_job, njobs, main_input, outloc, dsconf,
                 submit, deck_dir, deck_url, walltime_s) -> dict:
    cfg = nersc_config.load(paths.home())
    ident = identity.resolve(run_as, site="nersc", owner=cfg.owner)
    naming.validate_tag(tag)
    params = compose.validate_inputs(events_per_job=events_per_job, njobs=njobs, outloc="scratch", params=params)
    if outloc != "scratch":
        raise BeamkitError(f"outloc={outloc!r}: outputs of a NERSC run stay on CFS under {cfg.base_dir}; "
                           f"pass outloc='scratch' (the default) or omit it")
    validate_walltime(walltime_s)
    pin = decks.pin(deck_ref, deck_dir, deck_url, paths.decks_dir(), ident.production)
    if not (Path(pin.dir) / main_input).is_file():
        raise BeamkitError(f"main_input {main_input!r} not found in deck dir {pin.dir}")
    client = make_client(cfg)
    dsconf = naming.allocate_dsconf(ident.owner, tag, naming.dsconf_base(pin.sha), _taken(cfg, client, tag),
                                    explicit=dsconf)
    run_id = naming.run_id(tag, dsconf)
    runs_dir = paths.runs_dir()
    rdir = records.run_dir(runs_dir, run_id)
    if rdir.exists() and not _retryable(run_id, runs_dir):
        raise BeamkitError(f"run dir {rdir} already exists; a run id is never reused")
    rdir.mkdir(parents=True, exist_ok=True)
    rd = run_dir(cfg, run_id)
    cnf_name = naming.cnf_name(ident.owner, tag, dsconf)
    rec = records.RunRecord(run_id=run_id, tag=tag, dsconf=dsconf, owner=ident.owner, run_as=run_as,
                            deck=pin.as_record(), params=params, events_per_job=events_per_job, njobs=njobs,
                            outloc="scratch", slice_size=cfg.procs_per_node, state="created",
                            created=records.now_utc(), beamkit_version=__version__, site="nersc",
                            datasets=[naming.dataset(ident.owner, tag, dsconf)],
                            nersc={"run_dir": rd, "cnf": cnf_name, "walltime_s": walltime_s, "jobs": [],
                                   "config": cfg.as_record()})
    records.save(rec, runs_dir)
    try:
        local_cnf = rdir / cnf_name
        local_cnf.unlink(missing_ok=True)
        nersc_cnf.build_cnf(pin.dir, nersc_cnf.jobpars(owner=ident.owner, tag=tag, dsconf=dsconf,
                                                        main_input=main_input, events_per_job=events_per_job,
                                                        njobs=njobs, params=params), local_cnf)
        (rdir / "job.sh").write_text(nersc_templates.render_job(cfg, rd))
        (rdir / "inner.sh").write_text(nersc_templates.render_inner(
            cfg, run_id=run_id, run_dir=rd, owner=ident.owner, tag=tag, dsconf=dsconf,
            events_per_job=events_per_job, main_input=main_input, params=params))
        _remote_layout(client, rd, [(local_cnf, f"{rd}/{cnf_name}"), (rdir / "job.sh", f"{rd}/job.sh"),
                                    (rdir / "inner.sh", f"{rd}/inner.sh")])
    except BeamkitError as e:
        rec.state, rec.error = "enqueue_failed", f"{type(e).__name__}: {e}"
        records.save(rec, runs_dir)
        raise BeamkitError(f"run {run_id}: nothing was submitted ({e}); fix the cause and call again, "
                           f"the run dir is reused") from e
    if submit:
        _submit_missing(rec, cfg, client, runs_dir)
    return rec.to_dict()


def submit_run(run_id, run_as) -> dict:
    cfg = nersc_config.load(paths.home())
    identity.resolve(run_as, site="nersc", owner=cfg.owner)
    runs_dir = paths.runs_dir()
    rec = records.load(run_id, runs_dir)
    if rec.site != "nersc":
        raise BeamkitError(f"run {run_id} is a {rec.site!r} run; submit_run is the NERSC backend's tool "
                           f"(the Fermilab path submits through make_recoveries)")
    if rec.state not in SUBMITTABLE:
        raise BeamkitError(f"run {run_id} is in state {rec.state!r}; submit_run applies to {SUBMITTABLE}")
    _submit_missing(rec, cfg, make_client(cfg), runs_dir)
    return rec.to_dict()
```

Add `from pathlib import Path` to the module's imports.

- [ ] **Step 5: Dispatch in tools.py**

Add `from beamkit import backends` and `from beamkit.backends import nersc as nersc_backend` to the imports. Change `run_beamline`'s signature and first lines:

```python
def run_beamline(tag: str, run_as: str, deck_ref: Optional[str] = None, params: Optional[dict] = None,
                 events_per_job: int = 1000, njobs: int = 1, main_input: str = "Mu2E.in",
                 outloc: str = "scratch", dsconf: Optional[str] = None, slice_size: Optional[int] = None,
                 submit: bool = True, confirm: bool = False, deck_dir: Optional[str] = None,
                 deck_url: str = DEFAULT_DECK_URL, site: str = "fermilab",
                 walltime_s: int = nersc_backend.WALLTIME_DEFAULT) -> dict:
    """Pin the deck, allocate desc=tag and dsconf=<sha7>, then either
    register the cnf and create its campaign through prodtools and submit
    it in one tick (site='fermilab'), or build the cnf locally, lay the run
    out on CFS and submit one Slurm job per procs_per_node indices through
    the IRI API (site='nersc'). Every caller value is refused before the
    deck fetch and the remote probe, so a refused call burns no dsconf and
    writes no record."""
    backends.validate_site(site)
    if site == "nersc":
        if slice_size is not None:
            raise BeamkitError("slice_size applies to site='fermilab' only; a NERSC run is sliced by "
                               "procs_per_node from nersc.toml")
        return nersc_backend.run_beamline(tag=tag, run_as=run_as, deck_ref=deck_ref, params=params,
                                          events_per_job=events_per_job, njobs=njobs, main_input=main_input,
                                          outloc=outloc, dsconf=dsconf, submit=submit, deck_dir=deck_dir,
                                          deck_url=deck_url, walltime_s=walltime_s)
    if walltime_s != nersc_backend.WALLTIME_DEFAULT:
        raise BeamkitError("walltime_s applies to site='nersc' only; the Fermilab path takes its resources "
                           "from prodtools")
    ident = identity.resolve(run_as, confirm)
    ...   # the existing body, unchanged from here
```

Add the new tool after `make_recoveries`:

```python
def submit_run(run_id: str, run_as: str) -> dict:
    """NERSC runs only: submit the Slurm jobs of a run created with
    submit=False, or the jobs a partial submit did not reach. The Fermilab
    path submits through make_recoveries."""
    return nersc_backend.submit_run(run_id, run_as)
```

- [ ] **Step 6: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_backends_nersc_run.py tests/test_tools.py -q`
Expected: all pass. The `test_happy_path` duration assertion is `10 * 2 + 900 = 920`.

- [ ] **Step 7: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/beamkit/decks.py src/beamkit/backends/nersc.py src/beamkit/tools.py tests/test_backends_nersc_run.py tests/test_tools.py
git commit -m "feat(nersc): run_beamline site='nersc' lays the run out on CFS and submits Slurm jobs"
```

---

### Task 10: Status, outputs, listing and the recovery refusal for NERSC runs

**Files:**
- Modify: `src/beamkit/backends/nersc.py`
- Modify: `src/beamkit/tools.py`
- Create: `tests/test_backends_nersc_status.py`

**Interfaces:**
- Produces:
  ```python
  # backends/nersc.py
  TERMINAL = ("completed", "failed", "canceled")
  def out_counts(client, rec) -> dict      # {"expected", "nts", "logs", "missing": [<=50 ints], "nts_files": [names]}
  def status(rec) -> dict                  # {"jobs": [...], "outputs": out_counts, "beamfiles": [...]} and updates rec.state
  def outputs(rec) -> dict                 # {"run_id", "run_dir", "n_files", "total_size", "files": [{"name","index","size","path"}], "beamfiles": [...]}
  ```
  `status` job entries: `{"slurm_id", "offset", "count", "state", "exit_code", "elapsed", "node"}`. Run state after `status`: `complete` when every job is terminal and `nts == expected`; `short` when every job is terminal and `nts < expected`; `submitted` otherwise; `partially_submitted` and `created` are left alone.
- tools: `beamline_status`, `beamline_outputs` branch on `rec.site`; `make_recoveries` refuses a NERSC run; `list_beamline_runs` unchanged (records carry `site`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_backends_nersc_status.py
import pytest

from beamkit import BeamkitError, tools
from tests.test_backends_nersc_run import BASE, SHA, deck, fake, nersc_home, _run   # noqa: F401

RD = f"{BASE}/runs/T.e470313"


def _land(fake, idx, with_nts=True):
    seq = f"{idx:08d}"
    fake.files[f"{RD}/out/log.u.T.e470313.{seq}.log"] = b"BK_DONE\n"
    if with_nts:
        fake.files[f"{RD}/out/nts.u.T.e470313.{seq}.root"] = b"r" * (100 + idx)


def test_status_while_running(fake):
    _run(njobs=3)
    fake.jobs["58197742"]["state"] = "active"
    _land(fake, 0)
    st = tools.beamline_status("T.e470313")
    assert st["record"]["state"] == "submitted"
    assert st["nersc"]["jobs"][0]["state"] == "active" and st["nersc"]["jobs"][0]["node"] == "nid004381"
    assert st["nersc"]["outputs"] == {"expected": 3, "nts": 1, "logs": 1, "missing": [1, 2],
                                      "nts_files": ["nts.u.T.e470313.00000000.root"]}
    assert st["campaign"] is None


def test_status_complete_when_terminal_and_all_nts(fake):
    _run(njobs=3)
    fake.jobs["58197742"]["state"] = "completed"
    for i in range(3):
        _land(fake, i)
    st = tools.beamline_status("T.e470313")
    assert st["record"]["state"] == "complete" and st["nersc"]["outputs"]["missing"] == []
    assert tools.list_beamline_runs(state="complete")["count"] == 1


def test_status_short_when_terminal_and_nts_missing(fake):
    _run(njobs=3)
    fake.jobs["58197742"]["state"] = "failed"
    fake.jobs["58197742"]["exit_code"] = 1
    _land(fake, 0)
    _land(fake, 1, with_nts=False)
    st = tools.beamline_status("T.e470313")
    assert st["record"]["state"] == "short" and st["nersc"]["outputs"]["missing"] == [1, 2]
    assert st["nersc"]["jobs"][0]["exit_code"] == 1


def test_missing_list_is_capped_at_50(fake):
    _run(njobs=300)
    for j in fake.jobs.values():
        j["state"] = "completed"
    st = tools.beamline_status("T.e470313")
    assert len(st["nersc"]["outputs"]["missing"]) == 50 and st["nersc"]["outputs"]["nts"] == 0


def test_status_of_a_created_run_asks_nothing_of_slurm(fake):
    _run(njobs=3, submit=False)
    n = len(fake.calls)
    st = tools.beamline_status("T.e470313")
    assert st["record"]["state"] == "created" and st["nersc"]["jobs"] == []
    assert all("/compute/status/" not in c[1] for c in fake.calls[n:])


def test_outputs_lists_cfs_paths(fake):
    _run(njobs=3)
    _land(fake, 0)
    _land(fake, 2)
    out = tools.beamline_outputs("T.e470313")
    assert out["run_dir"] == RD and out["n_files"] == 2 and out["total_size"] == 100 + 102
    assert [f["index"] for f in out["files"]] == [0, 2]
    assert out["files"][1]["path"] == f"{RD}/out/nts.u.T.e470313.00000002.root"


def test_make_recoveries_refused_on_a_nersc_run(fake):
    _run(njobs=1)
    with pytest.raises(BeamkitError, match="no recovery on nersc; submit a new run"):
        tools.make_recoveries("T.e470313", "self")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_backends_nersc_status.py -q`
Expected: FAIL, `KeyError: 'nersc'` on `beamline_status`.

- [ ] **Step 3: Implement in `backends/nersc.py`**

```python
TERMINAL = ("completed", "failed", "canceled")
MISSING_CAP = 50


def _nts_index(name, rec):
    prefix = f"nts.{rec.owner}.{rec.tag}.{rec.dsconf}."
    base = name.rsplit("/", 1)[-1]
    if base.startswith(prefix) and base.endswith(".root"):
        seq = base[len(prefix):-5]
        if seq.isdigit():
            return int(seq)
    return None


def out_counts(client, rec) -> dict:
    entries = client.ls(f"{rec.nersc['run_dir']}/out")
    nts = {}
    logs = 0
    for e in entries:
        base = e["name"].rsplit("/", 1)[-1]
        idx = _nts_index(base, rec)
        if idx is not None:
            nts[idx] = e
        elif base.startswith(f"log.{rec.owner}.{rec.tag}.{rec.dsconf}."):
            logs += 1
    missing = sorted(set(range(rec.njobs)) - set(nts))
    return {"expected": rec.njobs, "nts": len(nts), "logs": logs, "missing": missing[:MISSING_CAP],
            "nts_files": [nts[i]["name"].rsplit("/", 1)[-1] for i in sorted(nts)]}


def _job_status(client, job):
    st = client.status(job["slurm_id"])
    md = st.get("meta_data") or {}
    return {"slurm_id": job["slurm_id"], "offset": job["offset"], "count": job["count"],
            "state": st["state"], "exit_code": st.get("exit_code"),
            "elapsed": md.get("elapsed"), "node": md.get("nodelist")}


def status(rec) -> dict:
    """Slurm state per job and one ls of out/. Sets the run state:
    complete / short once every job is terminal, submitted otherwise.
    created and partially_submitted are left as they are."""
    cfg = nersc_config.load(paths.home())
    client = make_client(cfg)
    jobs = [_job_status(client, j) for j in rec.nersc.get("jobs", [])]
    outs = out_counts(client, rec)
    if rec.state in ("submitted", "short", "complete") and jobs:
        if all(j["state"] in TERMINAL for j in jobs):
            rec.state = "complete" if outs["nts"] == outs["expected"] else "short"
        else:
            rec.state = "submitted"
        records.save(rec, paths.runs_dir())
    return {"jobs": jobs, "outputs": outs, "beamfiles": list(rec.beamfiles)}


def outputs(rec) -> dict:
    cfg = nersc_config.load(paths.home())
    client = make_client(cfg)
    rd = rec.nersc["run_dir"]
    files = []
    for e in client.ls(f"{rd}/out"):
        idx = _nts_index(e["name"], rec)
        if idx is not None:
            name = e["name"].rsplit("/", 1)[-1]
            files.append({"name": name, "index": idx, "size": int(e["size"]), "path": f"{rd}/out/{name}"})
    files.sort(key=lambda f: f["index"])
    return {"run_id": rec.run_id, "run_dir": rd, "n_files": len(files),
            "total_size": sum(f["size"] for f in files), "files": files, "beamfiles": list(rec.beamfiles)}
```

- [ ] **Step 4: Branch in tools.py**

```python
def make_recoveries(run_id: str, run_as: str, confirm: bool = False) -> dict:
    ...
    rec = records.load(run_id, paths.runs_dir())
    if rec.site == "nersc":
        raise BeamkitError(f"run {run_id}: no recovery on nersc; submit a new run (beamline_status reports "
                           f"the missing indices)")
    ...   # existing body


def beamline_status(run_id: str) -> dict:
    """The run record merged with prodtools' campaign_status (Fermilab) or
    with the Slurm job states and CFS output counts (NERSC)."""
    rec = records.load(run_id, paths.runs_dir())
    if rec.site == "nersc":
        n = nersc_backend.status(rec)
        return {"record": rec.to_dict(), "campaign": None, "nersc": n}
    campaign = None
    if rec.campaign_id is not None:
        campaign = bridge.campaign_status(rec.campaign_id, mine=identity.for_record(rec).mine)
    return {"record": rec.to_dict(), "campaign": campaign, "nersc": None}


def beamline_outputs(run_id: str) -> dict:
    """Files of the run's nts dataset with sizes and paths: dCache via SAM
    for a Fermilab run, CFS via one ls for a NERSC run."""
    rec = records.load(run_id, paths.runs_dir())
    if rec.site == "nersc":
        return nersc_backend.outputs(rec)
    ...   # existing body
```

Move the `records.load` in `make_recoveries` above the `campaign_id is None` check so the site test runs first.

- [ ] **Step 5: Run tests, then the whole suite**

Run: `.venv/bin/python -m pytest tests/test_backends_nersc_status.py -q` then `.venv/bin/python -m pytest -q`
Expected: all pass. `test_beamline_status_merges_record_and_campaign` in test_tools.py may compare the whole dict; if it does, add `"nersc": None` to its expected value.

- [ ] **Step 6: Commit**

```bash
git add src/beamkit/backends/nersc.py src/beamkit/tools.py tests/test_backends_nersc_status.py tests/test_tools.py
git commit -m "feat(nersc): status from Slurm and one ls, outputs on CFS, no recovery"
```

---

### Task 11: `make_beamfile` on the NERSC backend

**Files:**
- Create: `src/beamkit/templates/beamfile_job.py`
- Modify: `src/beamkit/nersc_templates.py` (add `render_beamfile_job`)
- Modify: `src/beamkit/backends/nersc.py` (add `make_beamfile`, `_refresh_beamfiles`; call it from `status`)
- Modify: `src/beamkit/tools.py` (`site` on `make_beamfile`)
- Create: `tests/test_nersc_beamfile.py`

**Interfaces:**
- Consumes: `beamfile.resolve_cuts`, `beamfile.validate_label`, `beamfile.validate_plane`, `naming.beamfile_name`, `out_counts`, `job_spec`-like spec (one process), `iri.IriClient.download`.
- Produces:
  ```python
  # nersc_templates.py
  def beamfile_module_source() -> str      # beamfile.py source with the beamkit import replaced by a local BeamkitError
  def render_beamfile_job(*, run_dir, owner, tag, dsconf, events_per_job, njobs, plane, label, flavor, cuts) -> str
  # backends/nersc.py
  BEAMFILE_WALLTIME_CAP = 4 * 3600
  def beamfile_duration(n_nts) -> int      # min(BEAMFILE_WALLTIME_CAP, 600 + 2 * n_nts)
  def make_beamfile(*, run_id, flavor, run_as, plane, cuts, label, publish) -> dict
  def _refresh_beamfiles(client, rec) -> None
  # tools.py
  def make_beamfile(..., site: str = "fermilab") -> dict
  ```
  Beam-file record entry, appended to `rec.beamfiles`:
  `{"run_id", "flavor", "label", "cuts", "plane", "slurm_id", "state": "submitted"|"complete"|"failed", "exit_code": None, "n_files_at_submit", "path": "<run_dir>/beamfiles/etc.<owner>.<tag>Beam-<label>.<dsconf>.0.txt", "sidecar": "<...>.json", "sha256": None, "size": None, "rows": None, "rows_in": None, "dropped": None, "pot": None, "n_files": None, "missing_indices": None, "sam_name": None, "location": None, "created"}`. On completion the sidecar's values fill the `None` fields.

- [ ] **Step 1: Write the job template**

`src/beamkit/templates/beamfile_job.py`:

```python
#!/usr/bin/env python
# beamkit NERSC backend: build one BLTrackFile on the node from the run's
# nts files on CFS. Self-contained: beamkit's beamfile module is embedded
# below; uproot comes from the cvmfs ana environment this runs under.
import glob
import json
import os
import re
import sys
from datetime import datetime, timezone

RUN_DIR = "@@RUN_DIR@@"
OWNER, TAG, DSCONF = "@@OWNER@@", "@@TAG@@", "@@DSCONF@@"
EVENTS_PER_JOB, NJOBS = @@EVENTS_PER_JOB@@, @@NJOBS@@
PLANE, LABEL, FLAVOR = "@@PLANE@@", "@@LABEL@@", "@@FLAVOR@@"
CUTS = json.loads('@@CUTS_JSON@@')
OUT_TXT = "@@OUT_TXT@@"
OUT_JSON = "@@OUT_JSON@@"

# ---- beamkit/beamfile.py, embedded ----
@@BEAMFILE_MODULE@@
# ---- end beamfile.py ----

BRANCHES = ("x", "y", "z", "Px", "Py", "Pz", "t", "PDGid", "EventID", "TrackID", "ParentID")
NAME_RE = re.compile(r"^nts\." + re.escape(OWNER) + r"\." + re.escape(TAG) + r"\." + re.escape(DSCONF) + r"\.(\d{8})\.root$")


def source_files():
    out = []
    for p in glob.glob(os.path.join(RUN_DIR, "out", "nts.*.root")):
        m = NAME_RE.match(os.path.basename(p))
        if m:
            out.append((int(m.group(1)), p))
    return [p for _, p in sorted(out)]


def rows(paths):
    import uproot
    key = "NTuple/" + PLANE
    for p in paths:
        f = uproot.open(p)
        if key not in f:
            raise BeamfileError("%s not in %s" % (key, p))
        a = f[key].arrays(list(BRANCHES), library="np")
        cols = [a[b] for b in BRANCHES]
        for i in range(len(a["x"])):
            yield Row(float(cols[0][i]), float(cols[1][i]), float(cols[2][i]), float(cols[3][i]),
                      float(cols[4][i]), float(cols[5][i]), float(cols[6][i]),
                      int(cols[7][i]), int(cols[8][i]), int(cols[9][i]), int(cols[10][i]))


def main():
    cuts = validate_cuts(CUTS)
    files = source_files()
    if not files:
        print("no nts files under %s/out" % RUN_DIR, file=sys.stderr)
        return 3
    present = [int(NAME_RE.match(os.path.basename(p)).group(1)) for p in files]
    stats = write_rows(OUT_TXT, rows(files), cuts)
    side = {"run_id": TAG + "." + DSCONF, "flavor": FLAVOR, "label": LABEL, "cuts": cuts, "plane": PLANE,
            "path": OUT_TXT, "sha256": stats["sha256"], "size": stats["size"], "rows": stats["rows_out"],
            "rows_in": stats["rows_in"], "dropped": stats["dropped"],
            "pot": len(files) * EVENTS_PER_JOB, "n_files": len(files),
            "missing_indices": missing_indices(present, NJOBS),
            "source_files": [os.path.basename(p) for p in files], "sam_name": None, "location": None,
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    with open(OUT_JSON + ".part", "w") as fh:
        json.dump(side, fh, indent=2)
        fh.write("\n")
    os.replace(OUT_JSON + ".part", OUT_JSON)
    print("BK_BEAMFILE_DONE rows=%d files=%d" % (stats["rows_out"], len(files)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_nersc_beamfile.py
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from beamkit import BeamkitError, beamfile, nersc_templates as nt, tools
from tests.test_backends_nersc_run import BASE, SHA, deck, fake, nersc_home, _run   # noqa: F401
from tests.test_backends_nersc_status import _land, RD

FIX = Path(__file__).parent / "fixtures"
ANA = beamfile.ANA_PYTHON
needs_ana = pytest.mark.skipif(not os.path.exists(ANA), reason="ana interpreter not on this host")


def _render(run_dir):
    return nt.render_beamfile_job(run_dir=run_dir, owner="u", tag="T", dsconf="e470313", events_per_job=10,
                                  njobs=3, plane="Z3712", label="bm", flavor="bm",
                                  cuts=beamfile.resolve_cuts("bm", None))


def test_rendered_job_compiles_and_embeds_the_module():
    src = _render("/global/cfs/x")
    assert "@@" not in src and "from beamkit" not in src
    assert "def filter_rows" in src and "def write_rows" in src and "class BeamkitError" in src
    compile(src, "beamfile_job.py", "exec")


@needs_ana
def test_rendered_job_reproduces_the_reference_beam_file(tmp_path):
    rd = tmp_path / "run"
    (rd / "out").mkdir(parents=True)
    (rd / "beamfiles").mkdir()
    shutil.copy(FIX / "plane47.root", rd / "out" / "nts.u.T.e470313.00000000.root")
    job = rd / "beamfiles" / "beamfile_job.bm.py"
    job.write_text(_render(str(rd)))
    r = subprocess.run([ANA, str(job)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = rd / "beamfiles" / "etc.u.TBeam-bm.e470313.0.txt"
    assert out.read_bytes() == (FIX / "reference_bm.txt").read_bytes()
    side = json.loads((rd / "beamfiles" / "etc.u.TBeam-bm.e470313.0.json").read_text())
    assert side["n_files"] == 1 and side["pot"] == 10 and side["missing_indices"] == [1, 2]
    assert side["rows"] > 0 and side["sha256"]


def test_make_beamfile_submits_one_job_and_records_it(fake):
    _run(njobs=3)
    _land(fake, 0)
    _land(fake, 1)
    entry = tools.make_beamfile("T.e470313", "bm", "self", site="nersc")
    assert entry["state"] == "submitted" and entry["slurm_id"] == "58197743" and entry["n_files_at_submit"] == 2
    assert entry["path"] == f"{RD}/beamfiles/etc.u.TBeam-bm.e470313.0.txt"
    assert f"{RD}/beamfiles/beamfile_job.bm.py" in fake.files and f"{RD}/beamfiles/beamfile.bm.sh" in fake.files
    spec = fake.jobs["58197743"]["spec"]
    assert spec["resources"]["process_count"] == 1 and spec["attributes"]["duration"] == 604
    assert spec["arguments"] == [f"{RD}/beamfiles/beamfile.bm.sh"]
    assert tools.beamline_status("T.e470313")["record"]["beamfiles"][0]["label"] == "bm"


def test_beamfile_completion_copies_the_sidecar_into_the_record(fake):
    _run(njobs=1)
    _land(fake, 0)
    tools.make_beamfile("T.e470313", "bm", "self", site="nersc")
    side = {"run_id": "T.e470313", "flavor": "bm", "label": "bm", "cuts": {}, "plane": "Z3712",
            "path": f"{RD}/beamfiles/etc.u.TBeam-bm.e470313.0.txt", "sha256": "ab", "size": 9, "rows": 4,
            "rows_in": 5, "dropped": {}, "pot": 10, "n_files": 1, "missing_indices": [], "source_files": ["x"],
            "sam_name": None, "location": None, "created": "t"}
    fake.files[f"{RD}/beamfiles/etc.u.TBeam-bm.e470313.0.json"] = json.dumps(side).encode()
    fake.jobs["58197743"]["state"] = "completed"
    st = tools.beamline_status("T.e470313")
    bf = st["record"]["beamfiles"][0]
    assert bf["state"] == "complete" and bf["rows"] == 4 and bf["sha256"] == "ab" and bf["pot"] == 10


def test_beamfile_job_failure_is_recorded(fake):
    _run(njobs=1)
    _land(fake, 0)
    tools.make_beamfile("T.e470313", "bm", "self", site="nersc")
    fake.jobs["58197743"]["state"] = "failed"
    fake.jobs["58197743"]["exit_code"] = 3
    bf = tools.beamline_status("T.e470313")["record"]["beamfiles"][0]
    assert bf["state"] == "failed" and bf["exit_code"] == 3


def test_label_is_never_reused(fake):
    _run(njobs=1)
    _land(fake, 0)
    tools.make_beamfile("T.e470313", "bm", "self", site="nersc")
    with pytest.raises(BeamkitError, match="label 'bm' already has a beam file"):
        tools.make_beamfile("T.e470313", "bm", "self", site="nersc")


def test_no_nts_files_is_refused_with_counts(fake):
    _run(njobs=2)
    with pytest.raises(BeamkitError, match="0 of 2 nts files"):
        tools.make_beamfile("T.e470313", "bm", "self", site="nersc")


def test_publish_refused_on_nersc(fake):
    _run(njobs=1)
    with pytest.raises(BeamkitError, match="publishing is part of harvest"):
        tools.make_beamfile("T.e470313", "bm", "self", site="nersc", publish=True)


def test_site_must_match_the_record(fake):
    _run(njobs=1)
    with pytest.raises(BeamkitError, match="is a 'nersc' run; pass site='nersc'"):
        tools.make_beamfile("T.e470313", "bm", "self")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_nersc_beamfile.py -q`
Expected: FAIL, `AttributeError: module 'beamkit.nersc_templates' has no attribute 'render_beamfile_job'`.

- [ ] **Step 4: Implement the renderer**

Append to `src/beamkit/nersc_templates.py`:

```python
import json
from beamkit import beamfile as _beamfile_module

_IMPORT_LINE = "from beamkit import BeamkitError\n"


def beamfile_module_source() -> str:
    """beamfile.py as a standalone module: the one beamkit import becomes a
    local class. Nothing else in that file depends on the package."""
    src = Path(_beamfile_module.__file__).read_text()
    if _IMPORT_LINE not in src:
        raise TemplateError("beamfile.py no longer has the expected single beamkit import; update the embedding")
    return src.replace(_IMPORT_LINE, "class BeamkitError(Exception):\n    pass\n", 1)


def render_beamfile_job(*, run_dir, owner, tag, dsconf, events_per_job, njobs, plane, label, flavor, cuts) -> str:
    stem = f"{run_dir}/beamfiles/etc.{owner}.{tag}Beam-{label}.{dsconf}.0"
    return render("beamfile_job.py", {
        "RUN_DIR": run_dir, "OWNER": owner, "TAG": tag, "DSCONF": dsconf,
        "EVENTS_PER_JOB": int(events_per_job), "NJOBS": int(njobs),
        "PLANE": plane, "LABEL": label, "FLAVOR": flavor,
        "CUTS_JSON": json.dumps(cuts).replace("\\", "\\\\").replace("'", "\\'"),
        "OUT_TXT": stem + ".txt", "OUT_JSON": stem + ".json",
        "BEAMFILE_MODULE": beamfile_module_source(),
    })
```

- [ ] **Step 5: Implement the backend**

Append to `src/beamkit/backends/nersc.py`:

```python
from beamkit import beamfile

BEAMFILE_WALLTIME_CAP = 4 * 3600


def beamfile_duration(n_nts) -> int:
    return min(BEAMFILE_WALLTIME_CAP, 600 + 2 * int(n_nts))


def make_beamfile(*, run_id, flavor, run_as, plane, cuts, label, publish) -> dict:
    cfg = nersc_config.load(paths.home())
    identity.resolve(run_as, site="nersc", owner=cfg.owner)
    if publish:
        raise BeamkitError("publish=True on a NERSC run: publishing is part of harvest, which runs at Fermilab; "
                           "build with publish=False")
    label = flavor if label is None else label
    resolved = beamfile.resolve_cuts(flavor, cuts)
    beamfile.validate_label(label)
    beamfile.validate_plane(plane)
    runs_dir = paths.runs_dir()
    rec = records.load(run_id, runs_dir)
    if rec.site != "nersc":
        raise BeamkitError(f"run {run_id} is a {rec.site!r} run; pass site={rec.site!r}")
    if any(b["label"] == label for b in rec.beamfiles):
        raise BeamkitError(f"run {run_id}: label {label!r} already has a beam file; a beam file is never "
                           f"overwritten (pick another label)")
    client = make_client(cfg)
    counts = out_counts(client, rec)
    if counts["nts"] == 0:
        raise BeamkitError(f"run {run_id}: 0 of {counts['expected']} nts files on CFS "
                           f"({counts['logs']} logs); nothing to build a beam file from")
    rd = rec.nersc["run_dir"]
    stem = f"{rd}/beamfiles/etc.{rec.owner}.{rec.tag}Beam-{label}.{rec.dsconf}.0"
    local = records.run_dir(runs_dir, run_id) / "beamfiles"
    local.mkdir(exist_ok=True)
    job_py, job_sh = local / f"beamfile_job.{label}.py", local / f"beamfile.{label}.sh"
    job_py.write_text(nersc_templates.render_beamfile_job(
        run_dir=rd, owner=rec.owner, tag=rec.tag, dsconf=rec.dsconf, events_per_job=rec.events_per_job,
        njobs=rec.njobs, plane=plane, label=label, flavor=flavor, cuts=resolved))
    job_sh.write_text(nersc_templates.render_beamfile_sh(cfg, job_py=f"{rd}/beamfiles/{job_py.name}"))
    client.upload(job_py, f"{rd}/beamfiles/{job_py.name}")
    client.upload(job_sh, f"{rd}/beamfiles/{job_sh.name}")
    spec = job_spec(cfg, run_id=rec.run_id, run_dir=rd, offset=0, count=1,
                    duration=beamfile_duration(counts["nts"]))
    spec["name"] = f"beamkit.{rec.run_id}.beamfile.{label}"
    spec["arguments"] = [f"{rd}/beamfiles/{job_sh.name}"]
    spec["stdout_path"], spec["stderr_path"] = f"{rd}/slurm/beamfile.{label}.out", f"{rd}/slurm/beamfile.{label}.err"
    spec["environment"] = {}
    jid = client.submit(spec, idem_key=f"{rec.run_id}/beamfile/{label}")
    entry = {"run_id": run_id, "flavor": flavor, "label": label, "cuts": resolved, "plane": plane,
             "slurm_id": jid, "state": "submitted", "exit_code": None, "n_files_at_submit": counts["nts"],
             "path": stem + ".txt", "sidecar": stem + ".json", "sha256": None, "size": None, "rows": None,
             "rows_in": None, "dropped": None, "pot": None, "n_files": None, "missing_indices": None,
             "sam_name": None, "location": None, "created": records.now_utc()}
    rec.beamfiles.append(entry)
    records.save(rec, runs_dir)
    return entry


SIDECAR_KEYS = ("sha256", "size", "rows", "rows_in", "dropped", "pot", "n_files", "missing_indices", "created")


def _refresh_beamfiles(client, rec) -> None:
    """Beam-file jobs still 'submitted': read Slurm; on a terminal state
    read the sidecar and copy its numbers in, or record the failure."""
    for bf in rec.beamfiles:
        if bf.get("state") != "submitted":
            continue
        st = client.status(bf["slurm_id"])
        if st["state"] not in TERMINAL:
            continue
        bf["exit_code"] = st.get("exit_code")
        try:
            side = json.loads(client.download(bf["sidecar"]))
        except (iri.IriError, ValueError):
            bf["state"] = "failed"
            continue
        for k in SIDECAR_KEYS:
            bf[k] = side.get(k)
        bf["state"] = "complete" if st["state"] == "completed" else "failed"
```

Add `import json` to the module. In `status()`, after `jobs = [...]`, add `_refresh_beamfiles(client, rec)` and make the final `records.save(rec, paths.runs_dir())` unconditional (the beam-file refresh may have changed the record even when the run state did not).

- [ ] **Step 6: Dispatch in tools.py**

```python
def make_beamfile(run_id: str, flavor: str, run_as: str, plane: str = "Z3712", cuts: Optional[dict] = None,
                  publish: bool = False, location: Optional[str] = None, confirm: bool = False,
                  label: Optional[str] = None, site: str = "fermilab") -> dict:
    """A BLTrackFile from whatever nts files the run has: read from SAM and
    built here (site='fermilab'), or built by one Slurm job on Perlmutter
    from the files on CFS (site='nersc', never downloads). No completeness
    check: pot counts the files that exist. flavor selects the cut table;
    label names the files and defaults to the flavor."""
    backends.validate_site(site)
    rec_site = records.load(run_id, paths.runs_dir()).site
    if rec_site != site:
        raise BeamkitError(f"run {run_id} is a {rec_site!r} run; pass site={rec_site!r}")
    if site == "nersc":
        if location is not None:
            raise BeamkitError("location applies to site='fermilab' publishing only")
        return nersc_backend.make_beamfile(run_id=run_id, flavor=flavor, run_as=run_as, plane=plane, cuts=cuts,
                                           label=label, publish=publish)
    ident = identity.resolve(run_as, confirm, writes=publish)
    ...   # existing body
```

- [ ] **Step 7: Run tests, then the whole suite**

Run: `.venv/bin/python -m pytest tests/test_nersc_beamfile.py -q` then `.venv/bin/python -m pytest -q`
Expected: all pass (the ana-gated test runs on mu2esrv01 where `/cvmfs/mu2e.opensciencegrid.org/env/ana/2.8.0/bin/python` exists).

- [ ] **Step 8: Commit**

```bash
git add src/beamkit/templates/beamfile_job.py src/beamkit/nersc_templates.py src/beamkit/backends/nersc.py src/beamkit/tools.py tests/test_nersc_beamfile.py
git commit -m "feat(nersc): make_beamfile as a Slurm job on Perlmutter"
```

---

### Task 12: Server wiring: tool table, instructions, `get_server_info` backends

**Files:**
- Modify: `src/beamkit/server.py`
- Modify: `src/beamkit/tools.py` (`get_server_info`)
- Modify: `tests/test_server.py`, `tests/test_tools.py`

**Interfaces:**
- Produces: `server.TOOLS` gains `"submit_run"`; `get_server_info()["backends"] == {"fermilab": {"available": bool, "detail": str}, "nersc": {"available": bool, "config": str, "detail": str}}`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_server.py` change the expected set to include `"submit_run"` and add:

```python
def test_instructions_name_the_nersc_path():
    for word in ("site=\"nersc\"", "nersc.toml", "CFS", "no recovery", "submit_run"):
        assert word in server.INSTRUCTIONS, word
```

Append to `tests/test_tools.py`:

```python
def test_get_server_info_reports_backends(fake_bridge, beamkit_home):
    info = tools.get_server_info()
    assert info["backends"]["fermilab"] == {"available": True, "detail": "prodtools at /pt"}
    assert info["backends"]["nersc"]["available"] is False
    assert info["backends"]["nersc"]["config"] == str(beamkit_home / "nersc.toml")
    assert "nersc.toml" in info["backends"]["nersc"]["detail"]


def test_get_server_info_without_prodtools_still_answers(fake_bridge, monkeypatch):
    def gone():
        raise tools.bridge.BridgeError("prodtools is not importable here")
    monkeypatch.setattr(tools.bridge, "prodtools_info", gone)
    info = tools.get_server_info()
    assert info["backends"]["fermilab"]["available"] is False and info["prodtools"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_server.py tests/test_tools.py -q -k "server_info or instructions or tool_table"`
Expected: FAIL on the tool set and on `KeyError: 'backends'`.

- [ ] **Step 3: Implement**

`src/beamkit/server.py`: add to `TOOLS` after `make_recoveries`:

```python
    "submit_run": "NERSC runs only: submit the Slurm jobs of a run created with submit=false, or the jobs a partial submit did not reach.",
```

Change the `run_beamline`, `beamline_status`, `beamline_outputs` and `make_beamfile` descriptions to mention both sites, e.g. `"Pin a deck commit and submit the run: through prodtools (site=\"fermilab\") or as Slurm jobs on Perlmutter through the IRI API (site=\"nersc\")."`. Append to `INSTRUCTIONS`:

```
site="nersc" (run_beamline, make_beamfile) runs on NERSC Perlmutter
through the IRI Facility API with no Fermilab service in the loop: the
cnf is built here, the run is laid out under base_dir/runs/<run_id>/ on
CFS, and one Slurm job per procs_per_node indices is submitted; outputs
stay on CFS. It needs $BEAMKIT_HOME/nersc.toml and a NERSC Superfacility
API client in sfapi_dir; run_as="self" only. There is no recovery on
nersc: beamline_status reports missing indices, a new run replaces a
short one; submit_run submits a run created with submit=false or the
jobs a partial submit did not reach. make_beamfile on a nersc run is a
second Slurm job that builds the beam file next to the nts files.
```

`src/beamkit/tools.py`:

```python
def get_server_info() -> dict:
    """beamkit version, which backends this host can drive, directories, limits."""
    try:
        pt = bridge.prodtools_info()
        fermilab = {"available": True, "detail": f"prodtools at {pt['root']}"}
    except BeamkitError as e:
        pt, fermilab = None, {"available": False, "detail": str(e)}
    cfg_path = nersc_config.config_path(paths.home())
    try:
        cfg = nersc_config.load(paths.home())
        nersc = {"available": True, "config": str(cfg_path),
                 "detail": f"account {cfg.account}, base_dir {cfg.base_dir}, owner {cfg.owner}"}
    except BeamkitError as e:
        nersc = {"available": False, "config": str(cfg_path), "detail": str(e)}
    return {"name": "beamkit", "version": __version__, "python": sys.executable,
            "prodtools": pt, "dev_dir": identity.dev_dir_from_env(),
            "backends": {"fermilab": fermilab, "nersc": nersc},
            "deck_url": DEFAULT_DECK_URL,
            "decks_dir": str(paths.decks_dir()), "records_dir": str(paths.runs_dir()),
            "beamfiles_dir": str(paths.beamfiles_dir()), "slice_max": SLICE_MAX,
            "walltime_default": nersc_backend.WALLTIME_DEFAULT}
```

Add `nersc_config` to the `from beamkit import (...)` line in tools.py.

- [ ] **Step 4: Run the whole suite and the server check**

Run: `.venv/bin/python -m pytest -q`, then `BEAMKIT_PRODTOOLS_ROOT=/exp/mu2e/app/users/oksuzian/muse_050125/prodtools scripts/start_mcp.sh --check`
Expected: all pass; the check prints `OK: tools ...` with eight names including `submit_run`.

- [ ] **Step 5: Commit**

```bash
git add src/beamkit/server.py src/beamkit/tools.py tests/test_server.py tests/test_tools.py
git commit -m "feat(server): submit_run tool, NERSC instructions, backend availability"
```

---

### Task 13: Documentation

**Files:**
- Modify: `docs/architecture.md`
- Modify: `README.md`

- [ ] **Step 1: architecture.md**

Add rows to the file table for `iri.py`, `nersc_config.py`, `nersc_cnf.py`, `nersc_templates.py`, `templates/`, `backends/__init__.py`, `backends/nersc.py`, with one-sentence purposes taken from each module's docstring and the line counts from `wc -l`. Update the `records.py` row's state list. Add a section `## Submitting a run at NERSC` after `## Submitting a run` with this mermaid sequence:

```mermaid
sequenceDiagram
    participant C as Claude (laptop)
    participant B as beamkit tools
    participant N as backends/nersc
    participant I as api.iri.nersc.gov
    participant P as Perlmutter node
    C->>B: run_beamline(tag, deck_ref, run_as="self", site="nersc", njobs)
    B->>N: run_beamline(...)
    N->>N: load nersc.toml, resolve identity, validate, pin deck
    N->>I: ls runs/<tag>.<sha7>  (dsconf collision probe)
    N->>N: build cnf tarball, render job.sh + inner.sh, save record (created)
    N->>I: mkdir run dir, out/, slurm/, beamfiles/; upload cnf, job.sh, inner.sh
    loop one Slurm job per procs_per_node indices
        N->>I: POST /compute/job (BK_OFFSET=k*procs_per_node)
        I-->>N: slurm id  (record: jobs[])
    end
    I->>P: srun job.sh x count
    P->>P: inner.sh: log, tar xf cnf, spack load g4beamline, g4bl, mv nts to out/
    C->>B: beamline_status(run_id)
    B->>N: status(rec)
    N->>I: GET /compute/status per job; ls out/
    N-->>C: jobs, expected/nts/logs/missing, state submitted|short|complete
```

Add to `## The rules that shaped it`: "No recovery on the NERSC path: status reports, a new run replaces a short one. The Fermilab path keeps prodtools' recovery." and "Fermilab services are a plugin: the NERSC path imports nothing from prodtools and touches no SAM, dCache or ledger; harvest to them is a later, optional step."

- [ ] **Step 2: README.md**

Add a section `## NERSC from a laptop`:

````markdown
## NERSC from a laptop

1. Create a NERSC Superfacility API client in Iris (Superfacility API
   Clients, "+ New Client", red level for job submission, your laptop's
   public IP in the allow list). Save the client id to `~/.sfapi/client_id`
   and the private key (PEM or JWK tab) to `~/.sfapi/priv_key.pem`, then
   `chmod 400 ~/.sfapi/priv_key.pem`.
2. `pip install beamkit` (Python 3.10+, git on PATH).
3. Write `~/.beamkit/nersc.toml`:

   ```toml
   api            = "https://api.iri.nersc.gov/api/v2"
   sfapi_dir      = "~/.sfapi"
   account        = "m4599"
   base_dir       = "/global/cfs/cdirs/m4599/Users/<nersc-login>/beamkit"
   qos            = "regular"     # "debug" for a 30-minute test
   owner          = "<nersc-login>"
   ```

4. Register the MCP server (Claude Code `.mcp.json` or Claude Desktop):

   ```json
   {"mcpServers": {"beamkit": {"command": "beamkit-mcp"}}}
   ```

5. `get_server_info` shows `backends.nersc.available: true`. Then
   `run_beamline(tag="G4blBeam", deck_ref="<sha or tag>", run_as="self",
   site="nersc", njobs=10, events_per_job=1000)`, `beamline_status`, and
   `make_beamfile(run_id, "bm", "self", site="nersc")`.

Outputs stay on CFS under `base_dir/runs/<run_id>/out/`; the beam file
under `beamfiles/`. Nothing is declared to SAM. There is no recovery:
`beamline_status` lists missing indices; a new run replaces a short one.
````

- [ ] **Step 3: Commit**

```bash
git add docs/architecture.md README.md
git commit -m "docs: NERSC backend in the architecture map and README"
```

---

### Task 14: Live smoke, opt in

**Files:**
- Create: `tests/test_nersc_live.py`

**Interfaces:**
- Consumes: everything. Gated by `BEAMKIT_NERSC_LIVE=<path to a real nersc.toml>`; that file's `qos` should be `debug`.

- [ ] **Step 1: Write the test**

```python
# tests/test_nersc_live.py
"""Two indices of ten events on Perlmutter, then a beam file. Opt in with
BEAMKIT_NERSC_LIVE=/path/to/nersc.toml (qos = "debug"), from a host whose
IP the sfapi client allows. Takes 10 to 40 minutes, mostly queue wait."""
import os
import shutil
import time

import pytest

from beamkit import iri, nersc_config, paths, tools
from beamkit.backends import nersc

LIVE = os.environ.get("BEAMKIT_NERSC_LIVE")
pytestmark = pytest.mark.skipif(not LIVE or not os.path.isfile(LIVE), reason="BEAMKIT_NERSC_LIVE not set")
DECK = "e470313b49aa9d00924a3f76aea0a00249e7b567"


def _wait(run_id, done, minutes):
    for _ in range(minutes * 2):
        st = tools.beamline_status(run_id)
        if done(st):
            return st
        time.sleep(30)
    pytest.fail(f"{run_id} not done after {minutes} min: {tools.beamline_status(run_id)['nersc']}")


def test_two_indices_then_a_beam_file(beamkit_home):
    beamkit_home.mkdir(parents=True, exist_ok=True)
    shutil.copy(LIVE, beamkit_home / "nersc.toml")
    cfg = nersc_config.load(beamkit_home)
    who = iri.IriClient(cfg).whoami()
    assert cfg.owner in str(who), f"nersc.toml owner {cfg.owner!r} not in whoami {who!r} (plan ruling 1)"
    tag = "G4blLive"
    rec = tools.run_beamline(tag=tag, deck_ref=DECK, run_as="self", site="nersc", events_per_job=10, njobs=2,
                             params={"epsMax": "0.01"}, walltime_s=1800)
    assert rec["state"] == "submitted" and len(rec["nersc"]["jobs"]) == 1
    st = _wait(rec["run_id"], lambda s: s["record"]["state"] in ("complete", "short"), 40)
    assert st["record"]["state"] == "complete", st["nersc"]
    assert st["nersc"]["outputs"] == {**st["nersc"]["outputs"], "expected": 2, "nts": 2, "logs": 2, "missing": []}
    outs = tools.beamline_outputs(rec["run_id"])
    assert outs["n_files"] == 2 and all(f["size"] > 10_000 for f in outs["files"])
    log = iri.IriClient(cfg).download(f"{rec['nersc']['run_dir']}/out/log.{rec['owner']}.{tag}.{rec['dsconf']}.00000001.log")
    assert "BK_DONE idx=1 rc=0" in log and "g4beamline: simulation complete" in log
    bf = tools.make_beamfile(rec["run_id"], "bm", "self", site="nersc")
    st = _wait(rec["run_id"], lambda s: s["record"]["beamfiles"][0]["state"] != "submitted", 30)
    bf = st["record"]["beamfiles"][0]
    assert bf["state"] == "complete", bf
    assert bf["rows"] > 0 and bf["n_files"] == 2 and bf["pot"] == 20 and bf["missing_indices"] == []
```

- [ ] **Step 2: Run it once from mu2esrv01**

Write a real config for the run (the sfapi client already exists in `~/.sfapi`):

```bash
cat > /exp/mu2e/data/users/oksuzian/beamkit/nersc.toml <<'EOF'
api            = "https://api.iri.nersc.gov/api/v2"
sfapi_dir      = "~/.sfapi"
account        = "m4599"
base_dir       = "/global/cfs/cdirs/m4599/Users/oksuzian/beamkit"
qos            = "debug"
owner          = "oksuzian"
EOF
BEAMKIT_NERSC_LIVE=/exp/mu2e/data/users/oksuzian/beamkit/nersc.toml .venv/bin/python -m pytest tests/test_nersc_live.py -q -s
```

Expected: 1 passed within 40 minutes. On a failure, read `slurm/0.err` and the index logs under `out/` on CFS through `iri.IriClient(cfg).download(...)`; the most likely causes are `spack load` output on stderr (harmless), the ana python path inside the container (spec open question 2: if `beamfile_job` fails with `ModuleNotFoundError: uproot`, change `templates/beamfile.sh` to the interpreter that `ls /cvmfs/mu2e.opensciencegrid.org/env/ana/` shows and rerun), and a client older than 48 h (recreate it in Iris).

- [ ] **Step 3: Record the result and commit**

Append the live-run facts (job ids, elapsed, rows) to `docs/specs/2026-09-11-nersc-backend-design.md` section 14 as "Confirmed 2026-MM-DD", then:

```bash
git add tests/test_nersc_live.py docs/specs/2026-09-11-nersc-backend-design.md
git commit -m "test(nersc): opt-in live smoke on Perlmutter, two indices and a beam file"
```

---

## Self-review against the spec

- §4.1 install and MCP launch: Task 0. §4.2 home: Task 1. §4.3 config: Task 2 (owner added per ruling 1). §4.4 identity: Task 6.
- §5 run_beamline steps 1 to 7: Task 9 (build: Task 7; layout: `_remote_layout`; submit: `_submit_missing`; record: fields in `nersc`). `submit=False` and `submit_run`: Task 9.
- §6 node scripts: Task 4, contract in Task 5.
- §7 beam file: Task 11. §8 status and outputs: Task 10; beam-file entries refreshed in Task 11.
- §9 harvest: interface only, no task, by design.
- §10 seam: Tasks 8 to 11 (ruling 2 keeps the Fermilab path in tools.py).
- §11 errors: config (Task 2), key permissions (Task 2), 401 (Task 3), upload cap (Tasks 3, 7), run dir on CFS (Task 9 via dsconf allocation and `_retryable`), partial submit (Task 9), task failure or timeout (Task 3), zero nts (Task 11). QOS validation against the facility's list is not in the API surface recorded; a wrong qos surfaces as the submit error from Slurm in Task 9's partial-submit path.
- §12 testing: fake IRI (Task 3), golden scripts (Task 4), index math (Task 8), record migration (Task 6), config validation (Task 2), 5 MB (Tasks 3, 7), partial submit (Task 9), contract (Task 5), live (Task 14).
- Names used across tasks: `nersc.make_client`, `nersc.run_dir`, `nersc.slices`, `nersc.duration_s`, `nersc.validate_walltime`, `nersc.job_spec`, `nersc.out_counts`, `nersc.status`, `nersc.outputs`, `nersc.make_beamfile`, `nersc.submit_run`, `nersc.WALLTIME_DEFAULT`, `nersc.TERMINAL`; `nt.g4bl_command`, `nt.g4bl_script`, `nt.render`, `nt.render_job`, `nt.render_inner`, `nt.render_beamfile_sh(cfg, *, job_py)`, `nt.render_beamfile_job`; `iri.IriClient(cfg, token_provider, session)`, `iri.IriError`, `iri.UPLOAD_MAX`; `nersc_config.load`, `nersc_config.config_path`, `NerscConfig.key_file/client_id/as_record`; `nersc_cnf.jobpars`, `nersc_cnf.build_cnf`; `decks.pin`; `backends.validate_site`; `records.RunRecord.site/nersc`; `identity.resolve(..., site, owner)`. Each is defined in the task that first names it as produced.
