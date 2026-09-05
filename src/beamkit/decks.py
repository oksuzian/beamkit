"""Deck pinning: one commit of Mu2e/G4BeamlineScripts, materialized once."""
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from beamkit import BeamkitError
from beamkit.naming import SHA_RE

DEFAULT_DECK_URL = "https://github.com/Mu2e/G4BeamlineScripts"


class DeckError(BeamkitError):
    pass


@dataclass(frozen=True)
class DeckPin:
    url: str | None
    ref: str | None
    sha: str | None
    dir: str
    pinned: bool
    dirty: bool = False

    def as_record(self) -> dict:
        return asdict(self)


def _git(*args, cwd=None) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        raise DeckError(f"git {' '.join(args)} failed (rc={proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout.strip()


def resolve_ref(url: str, ref: str) -> str:
    """A 40-hex sha as given; otherwise a tag (peeled) or branch via ls-remote."""
    if SHA_RE.match(ref):
        return ref
    out = _git("ls-remote", "--tags", "--heads", url)
    by_ref = {}
    for line in out.splitlines():
        if "\t" in line:
            sha, name = line.split("\t", 1)
            by_ref[name] = sha
    for key in (f"refs/tags/{ref}^{{}}", f"refs/tags/{ref}", f"refs/heads/{ref}"):
        if key in by_ref:
            return by_ref[key]
    raise DeckError(f"deck_ref {ref!r} is neither a 40-hex commit sha nor a tag or branch of {url}")


def materialize(url: str, ref: str, cache_dir: Path) -> DeckPin:
    """Fetch exactly `ref`'s commit into cache_dir/<sha12>/; reuse if present."""
    sha = resolve_ref(url, ref)
    cache_dir = Path(cache_dir)
    dest = cache_dir / sha[:12]
    if dest.exists():
        if not dest.is_dir():
            raise DeckError(f"{dest} exists and is not a directory; remove it by hand")
        head = _git("rev-parse", "HEAD", cwd=dest)
        if head != sha:
            raise DeckError(f"{dest} holds commit {head}, expected {sha}; remove it by hand")
        return DeckPin(url, ref, sha, str(dest), True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    part = Path(tempfile.mkdtemp(prefix=dest.name + ".part-", dir=cache_dir))
    try:
        _git("init", "-q", cwd=part)
        _git("remote", "add", "origin", url, cwd=part)
        _git("fetch", "-q", "--depth", "1", "origin", sha, cwd=part)
        _git("checkout", "-q", "--detach", "FETCH_HEAD", cwd=part)
        head = _git("rev-parse", "HEAD", cwd=part)
        if head != sha:
            raise DeckError(f"fetched {head} for {ref!r}, expected {sha}")
    except Exception:
        shutil.rmtree(part, ignore_errors=True)
        raise
    try:
        part.rename(dest)
    except OSError as exc:
        head = None
        try:
            if dest.is_dir():
                head = _git("rev-parse", "HEAD", cwd=dest)
        except DeckError:
            head = None
        finally:
            shutil.rmtree(part, ignore_errors=True)
        if head == sha:
            return DeckPin(url, ref, sha, str(dest), True)
        raise DeckError(
            f"could not place deck {sha} at {dest}: {exc}; "
            f"{dest} holds {head or 'no git checkout'}") from exc
    return DeckPin(url, ref, sha, str(dest), True)


def inspect_local(dir) -> DeckPin:
    """A development deck: recorded as it is, dirty or not, never refused."""
    d = Path(dir)
    if not d.is_dir():
        raise DeckError(f"deck_dir {dir} is not a directory")
    try:
        sha = _git("rev-parse", "HEAD", cwd=d)
        dirty = bool(_git("status", "--porcelain", cwd=d))
    except DeckError:
        sha, dirty = None, False
    return DeckPin(None, None, sha, str(d.resolve()), False, dirty)
