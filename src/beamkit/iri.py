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
