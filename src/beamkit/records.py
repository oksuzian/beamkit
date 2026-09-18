"""Run records: files under runs/<run_id>/. prodtools' ledger is the system of
record for submission state; these hold only what prodtools does not know."""
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from beamkit import BeamkitError

# needs_attention: the last tick returned prodtools rc=2 (held rows, exhausted
# recoveries); a clean tick returns the run to submitted. partially_submitted,
# short, complete are NERSC-path states: partially_submitted is a submit that
# stopped part way, short is every job terminal with fewer nts files than
# njobs, complete is every job terminal with all nts files.
STATES = ("enqueue_failed", "created", "submitted", "needs_attention",
          "partially_submitted", "short", "complete")


class RecordError(BeamkitError):
    pass


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


LEGACY_KEYS = frozenset({"campaign_id", "tarball", "ticks", "prodtools"})


@dataclass
class RunRecord:
    run_id: str
    tag: str
    dsconf: str
    owner: str
    run_as: str
    deck: dict
    params: dict
    events_per_job: int
    njobs: int
    outloc: str
    slice_size: int
    state: str
    site: str
    block: object                        # backends.<site>.Block
    datasets: list = field(default_factory=list)   # nts.<owner>.<desc>.<dsconf>.root
    created: str = ""
    beamkit_version: str = ""
    beamfiles: list = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict:
        """The block travels under its site's name, so run.json says
        "fermilab": {...} or "nersc": {...}, never "block"."""
        d = asdict(self)
        d[self.site] = d.pop("block")
        return d

    @classmethod
    def from_dict(cls, d: dict, where="run.json") -> "RunRecord":
        from beamkit import backends    # late: every backend imports records
        site = d.get("site")
        if LEGACY_KEYS & set(d) or site not in backends.SITES or site not in d:
            raise RecordError(f"{where} was written by beamkit {d.get('beamkit_version') or '<unknown>'} "
                              f"(before 0.5.0) and is not readable by this version; move the run dir aside")
        d = dict(d)
        block = backends.block_type(site)(**d.pop(site))
        try:
            return cls(block=block, **d)
        except TypeError as e:
            raise RecordError(f"{where}: {e}") from e


def run_dir(runs_dir, run_id) -> Path:
    return Path(runs_dir) / run_id


def atomic_write_text(path, text) -> Path:
    """Write through <path>.part and rename, so a reader never sees a
    half-written file and a crash leaves the old one intact."""
    path = Path(path)
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(text)
    os.replace(tmp, path)
    return path


def save(rec: RunRecord, runs_dir) -> Path:
    d = run_dir(runs_dir, rec.run_id)
    d.mkdir(parents=True, exist_ok=True)
    return atomic_write_text(d / "run.json", json.dumps(rec.to_dict(), indent=2) + "\n")


def load(run_id, runs_dir) -> RunRecord:
    p = run_dir(runs_dir, run_id) / "run.json"
    if not p.is_file():
        raise RecordError(f"no run record for {run_id!r} at {p}")
    return RunRecord.from_dict(json.loads(p.read_text()), where=str(p))


def try_load(run_id, runs_dir) -> RunRecord | None:
    """The record, or None when there is none to read: for the callers that
    ask a question about a run dir rather than open one."""
    try:
        return load(run_id, runs_dir)
    except RecordError:
        return None


def claim_run_dir(runs_dir, run_id, retryable) -> Path:
    """The local run dir of a new run, created. A run id is never reused, so
    an existing dir is refused unless `retryable(run_id, runs_dir)` says the
    previous attempt created nothing anywhere else and may be overwritten
    in place."""
    d = run_dir(runs_dir, run_id)
    if d.exists() and not retryable(run_id, runs_dir):
        raise BeamkitError(f"run dir {d} already exists; a run id is never reused")
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_runs(runs_dir, state=None) -> list[RunRecord]:
    if state is not None and state not in STATES:
        raise RecordError(f"state must be one of {STATES}, got {state!r}")
    root = Path(runs_dir)
    if not root.is_dir():
        return []
    recs = [load(p.parent.name, runs_dir) for p in sorted(root.glob("*/run.json"))]
    if state is not None:
        recs = [r for r in recs if r.state == state]
    return sorted(recs, key=lambda r: r.created, reverse=True)
