"""Run records: files under runs/<run_id>/. prodtools' ledger is the system of
record for submission state; these hold only what prodtools does not know."""
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# needs_attention: the last tick returned prodtools rc=2 (held rows, exhausted
# recoveries); a clean tick returns the run to submitted.
STATES = ("enqueue_failed", "created", "submitted", "needs_attention")


class RecordError(RuntimeError):
    pass


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
    campaign_id: int | None = None
    tarball: str | None = None
    datasets: list = field(default_factory=list)          # resolved nts.<owner>.<desc>.<dsconf>.root
    prodtools_datasets: list = field(default_factory=list)  # prodtools' raw return: the outloc glob
    created: str = ""
    ticks: list = field(default_factory=list)
    prodtools: dict = field(default_factory=dict)
    beamkit_version: str = ""
    sweep_id: str | None = None
    beamfiles: list = field(default_factory=list)
    beamfile_in: str | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "RunRecord":
        return cls(**d)


def run_dir(runs_dir, run_id) -> Path:
    return Path(runs_dir) / run_id


def save(rec: RunRecord, runs_dir) -> Path:
    d = run_dir(runs_dir, rec.run_id)
    d.mkdir(parents=True, exist_ok=True)
    out = d / "run.json"
    tmp = d / "run.json.part"
    tmp.write_text(json.dumps(rec.to_dict(), indent=2) + "\n")
    os.replace(tmp, out)
    return out


def load(run_id, runs_dir) -> RunRecord:
    p = run_dir(runs_dir, run_id) / "run.json"
    if not p.is_file():
        raise RecordError(f"no run record for {run_id!r} at {p}")
    return RunRecord.from_dict(json.loads(p.read_text()))


def list_runs(runs_dir, state=None) -> list[RunRecord]:
    if state is not None and state not in STATES:
        raise RecordError(f"state must be one of {STATES}, got {state!r}")
    root = Path(runs_dir)
    if not root.is_dir():
        return []
    recs = [RunRecord.from_dict(json.loads(p.read_text()))
            for p in sorted(root.glob("*/run.json"))]
    if state is not None:
        recs = [r for r in recs if r.state == state]
    return sorted(recs, key=lambda r: r.created, reverse=True)
