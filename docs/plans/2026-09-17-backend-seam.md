# Backend Seam Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `backends/fermilab.py` a peer of `backends/nersc.py` behind one interface, own Run creation once in `runs.py`, and give the run record one typed site block.

**Architecture:** `tools.py` loads a record or builds a `RunRequest`, asks `backends.get(site)` for the site's module, and delegates. `runs.create` runs the creation ritual once and calls the backend at named hooks. `records.RunRecord` stores `site` and a `block` dataclass owned by the backend, emitted in JSON under the site's name. Tests of the Fermilab path run against the fake prodtools MCP server through `BEAMKIT_PRODTOOLS_ROOT`.

**Tech Stack:** Python 3.10+ (`dataclasses`, `importlib`), pytest, the existing `tests/fake_prodtools_mcp.py` and `tests/fake_iri.py` fakes.

**Spec:** `docs/specs/2026-09-17-backend-seam-design.md`. Read it first; every task below argues from it. `CONTEXT.md` holds the vocabulary (Site, Backend, Run request, Site block).

## Global Constraints

- Work on branch `v1` in `/exp/mu2e/app/users/oksuzian/beamkit`. Never push, tag, or open a PR.
- Test command, from the repo root, must be green at the end of every task:
  `env -u PYTHONPATH -u BEAMKIT_PRODTOOLS_ROOT .venv/bin/python -m pytest -q -p no:cacheprovider`
  (baseline before Task 1: 404 passed, 4 skipped).
- The nine MCP tool names and their parameter names do not change (`server.TOOLS`). One default changes: `run_beamline(walltime_s)` becomes `Optional[int] = None`.
- Public result shapes that change, exactly as the spec says and no more: the record gains `site` + one block key (`"fermilab"` or `"nersc"`) and loses top-level `campaign_id`, `tarball`, `ticks`, `prodtools`, `nersc`; `beamline_status` returns `{"record", "site", "status"}`.
- Old-shape `run.json` files are refused with `RecordError`, never converted or skipped.
- Error messages quoted in this plan are exact; tests match on them.
- No new monkeypatch of `beamkit.bridge` outside `tests/test_backends_fermilab.py`.
- Commit messages end with:
  ```
  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_0138sWPyeSCDFbbn2cB9JBDy
  ```
- Style: module docstrings and comments in the voice of the existing code (short, states the rule and its reason). No `# TODO`.

---

## File structure

| File | After this plan |
| --- | --- |
| `src/beamkit/backends/__init__.py` | `SITES`, `get(site)`, `block_type(site)`. Nothing else. |
| `src/beamkit/backends/fermilab.py` | **new.** `Block`; the fifteen interface names; every Fermilab helper that lived in `tools.py` (`_outloc`, `_slice_size`, `_summary`, `_after_failed_push`, `_tick_into`, `_campaign`). |
| `src/beamkit/backends/nersc.py` | `Block`; the fifteen interface names; `run_beamline` deleted (its body becomes `validate`/`taken`/`new_block`/`enqueue`/`submit`). |
| `src/beamkit/runs.py` | **new.** `RunRequest`, `create(req)`. |
| `src/beamkit/records.py` | `RunRecord` with `site`, `block`; `LEGACY_KEYS`; `from_dict` refusal. |
| `src/beamkit/naming.py` | `+ dsconf_of(cnf_name)`. |
| `src/beamkit/tools.py` | Validate, load, dispatch. No site knowledge beyond `backends.get`. |
| `src/beamkit/__init__.py`, `pyproject.toml` | version `0.5.0`. |
| `tests/test_records.py` | block round trips, refusal. |
| `tests/test_backends_interface.py` | **new.** both modules export the interface. |
| `tests/test_runs.py` | **new.** ordering tests with a stub backend. |
| `tests/test_backends_fermilab.py` | **new.** `after_failure` outcomes with patched `bridge` (the one allowed place). |
| `tests/test_tools.py` | driven through `fake_prodtools_root`. |
| `tests/fake_prodtools_mcp.py` | `FAKE_PRODTOOLS_FAIL` takes a comma list; `push_cnf` reads `njobs` from the entry; `+ FAKE_PRODTOOLS_CNF_EXISTS`, `+ FAKE_PRODTOOLS_CAMPAIGNS`. |
| `docs/architecture.md`, `docs/tools.md`, `README.md` | updated. |

---

### Task 1: Site blocks in the run record

**Files:**
- Modify: `src/beamkit/records.py` (the `RunRecord` dataclass, `load`, `list_runs`)
- Modify: `src/beamkit/backends/__init__.py`
- Create: `src/beamkit/backends/fermilab.py` (Block only, for now)
- Modify: `src/beamkit/backends/nersc.py` (add `Block`; replace every `rec.nersc[...]` read/write)
- Modify: `src/beamkit/tools.py` (replace every `rec.campaign_id` / `rec.tarball` / `rec.ticks` / `rec.prodtools` and the two `RunRecord(...)` constructions)
- Test: `tests/test_records.py`, and the assertions in `tests/test_tools.py`, `tests/test_backends_nersc_run.py`, `tests/test_backends_nersc_status.py`, `tests/test_sfapi.py`, `tests/test_fetch_outputs.py`, `tests/test_nersc_beamfile.py`, `tests/test_nersc_live.py` that read those keys.

**Interfaces:**
- Produces: `records.RunRecord(..., site: str, block: object, ...)`; `RunRecord.to_dict()` emits `{"site": s, s: {...}}`; `records.LEGACY_KEYS`; `backends.get(site) -> module`; `backends.block_type(site) -> type`; `backends.fermilab.Block(prodtools: dict, campaign_id=None, tarball=None, ticks=[])`; `backends.nersc.Block(run_dir, cnf, walltime_s, config, jobs=[])`.
- Consumes: nothing new.

- [ ] **Step 1: Write the failing record tests**

Replace `tests/test_records.py`'s `_rec` helper and the two site tests (`test_record_without_site_reads_as_fermilab`, `test_nersc_block_round_trips`) with:

```python
from beamkit.backends import fermilab, nersc


def _rec(run_id="T.e470313", created="2026-09-03T10:00:00+00:00", state="created", site="fermilab", block=None):
    if block is None:
        block = (fermilab.Block(prodtools={"root": "/pt", "commit": "c" * 40, "dev_dir": None}) if site == "fermilab"
                 else nersc.Block(run_dir="/global/cfs/x/runs/" + run_id, cnf="cnf.u.T.e470313.0.tar",
                                  walltime_s=100, config={}))
    return records.RunRecord(run_id=run_id, tag="T", dsconf="e470313", owner="u", run_as="self",
                             deck={}, params={}, events_per_job=10, njobs=3, outloc="scratch", slice_size=3,
                             state=state, site=site, block=block, created=created)


def test_fermilab_block_round_trips_under_its_site_key(tmp_path):
    rec = _rec()
    rec.block.campaign_id, rec.block.tarball = 7, "cnf.u.T.e470313.0.tar"
    rec.block.ticks.append({"when": "t", "rc": 0, "needs_attention": False, "summary": ""})
    records.save(rec, tmp_path)
    d = json.loads((tmp_path / "T.e470313" / "run.json").read_text())
    assert d["site"] == "fermilab" and d["fermilab"]["campaign_id"] == 7 and "nersc" not in d
    assert "campaign_id" not in d and "block" not in d
    back = records.load("T.e470313", tmp_path)
    assert isinstance(back.block, fermilab.Block) and back.block.ticks[0]["rc"] == 0


def test_nersc_block_round_trips_under_its_site_key(tmp_path):
    rec = _rec(site="nersc")
    rec.block.jobs.append({"slurm_id": "1", "offset": 0, "count": 3, "submitted": "t"})
    records.save(rec, tmp_path)
    d = json.loads((tmp_path / "T.e470313" / "run.json").read_text())
    assert d["site"] == "nersc" and d["nersc"]["jobs"][0]["slurm_id"] == "1" and "fermilab" not in d
    back = records.load("T.e470313", tmp_path)
    assert isinstance(back.block, nersc.Block) and back.block.walltime_s == 100


@pytest.mark.parametrize("old", [
    {"campaign_id": 7},                       # pre-0.5.0 fermilab record
    {"site": "nersc", "nersc": {"jobs": []}, "campaign_id": None},   # pre-0.5.0 nersc record
    {"site": "fermilab"},                     # no block at all
])
def test_old_shape_is_refused_naming_the_file(tmp_path, old):
    d = tmp_path / "T.e470313"
    d.mkdir()
    base = {"run_id": "T.e470313", "tag": "T", "dsconf": "e470313", "owner": "u", "run_as": "self", "deck": {},
            "params": {}, "events_per_job": 10, "njobs": 3, "outloc": "scratch", "slice_size": 3,
            "state": "created", "beamkit_version": "0.4.0"}
    (d / "run.json").write_text(json.dumps({**base, **old}))
    with pytest.raises(records.RecordError, match=r"run\.json was written by beamkit 0\.4\.0 \(before 0\.5\.0\)"):
        records.load("T.e470313", tmp_path)
    with pytest.raises(records.RecordError, match="before 0.5.0"):
        records.list_runs(tmp_path)


def test_importing_records_does_not_import_backends():
    import subprocess, sys
    code = "import sys, beamkit.records; print('beamkit.backends' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "False"
```

Add `import json` and `import pytest` at the top of the file if missing. Keep every other test in the file; they use `_rec()` and keep passing.

- [ ] **Step 2: Run them to verify they fail**

Run: `env -u PYTHONPATH -u BEAMKIT_PRODTOOLS_ROOT .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_records.py`
Expected: FAIL with `ImportError: cannot import name 'fermilab'` (module does not exist yet).

- [ ] **Step 3: `backends/__init__.py`**

Replace the file with:

```python
"""Where a run executes, and the module that carries that site's half of
every tool. Two backends, one interface (docs/specs/2026-09-17-backend-
seam-design.md §6); tools.py and runs.py ask get(site) and never import a
backend by name."""
import importlib

from beamkit import BeamkitError

SITES = ("fermilab", "nersc")


def get(site):
    """The backend module for a site. Imported on first use, so records.py
    can name a block type without an import cycle."""
    if site not in SITES:
        raise BeamkitError(f"site must be one of {SITES}, got {site!r}")
    return importlib.import_module(f"beamkit.backends.{site}")


def block_type(site):
    return get(site).Block
```

- [ ] **Step 4: The two `Block` dataclasses**

Create `src/beamkit/backends/fermilab.py`:

```python
"""The Fermilab backend: a run is a prodtools campaign, driven through
bridge.py. This module carries the Fermilab half of every tool (the
interface in docs/specs/2026-09-17-backend-seam-design.md §6)."""
from dataclasses import dataclass, field


@dataclass
class Block:
    """What only the Fermilab backend reads and writes on a run record."""
    prodtools: dict                      # root, commit, dev_dir
    campaign_id: int | None = None
    tarball: str | None = None
    ticks: list = field(default_factory=list)
```

In `src/beamkit/backends/nersc.py`, after the imports, add:

```python
@dataclass
class Block:
    """What only the NERSC backend reads and writes on a run record."""
    run_dir: str
    cnf: str
    walltime_s: int
    config: dict
    jobs: list = field(default_factory=list)
```

and `from dataclasses import dataclass, field` at the top.

- [ ] **Step 5: `RunRecord` with `site` and `block`**

In `src/beamkit/records.py` replace the dataclass with:

```python
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
```

In `load`, pass the path: `return RunRecord.from_dict(json.loads(p.read_text()), where=str(p))`. `list_runs` calls `load`, so it raises the same.

- [ ] **Step 6: Every reader and writer of the moved fields**

Mechanical replacements. `grep -n "rec\.\(campaign_id\|tarball\|ticks\|prodtools\|nersc\)\b\|\.nersc\[" src/beamkit/tools.py src/beamkit/backends/nersc.py` lists them all. Apply:

| Was | Becomes |
| --- | --- |
| `rec.campaign_id` | `rec.block.campaign_id` |
| `rec.tarball` | `rec.block.tarball` |
| `rec.ticks` | `rec.block.ticks` |
| `rec.prodtools` | `rec.block.prodtools` |
| `rec.nersc["run_dir"]` / `rec.nersc['run_dir']` | `rec.block.run_dir` |
| `rec.nersc["jobs"]` / `rec.nersc.get("jobs", [])` / `rec.nersc.get("jobs")` | `rec.block.jobs` |
| `rec.nersc["walltime_s"]` | `rec.block.walltime_s` |
| `rec.nersc["cnf"]` | `rec.block.cnf` |
| `rec.nersc["config"]` | `rec.block.config` |

The two `RunRecord(...)` constructions change as follows. In `tools.run_beamline`:

```python
    rec = records.RunRecord(run_id=run_id, tag=tag, dsconf=dsconf, owner=ident.owner, run_as=run_as,
                            deck=pin.as_record(), params=params, events_per_job=events_per_job,
                            njobs=njobs, outloc=outloc, slice_size=slice_size, state="created",
                            site="fermilab",
                            block=fermilab_backend.Block(prodtools=dict(bridge.prodtools_info(), dev_dir=dev_dir)),
                            created=records.now_utc(), beamkit_version=__version__)
```

with `from beamkit.backends import fermilab as fermilab_backend` added to the imports. In `backends/nersc.run_beamline`:

```python
    rec = records.RunRecord(run_id=run_id, tag=tag, dsconf=dsconf, owner=ident.owner, run_as=run_as,
                            deck=pin.as_record(), params=params, events_per_job=events_per_job, njobs=njobs,
                            outloc="scratch", slice_size=cfg.procs_per_node, state="created",
                            created=records.now_utc(), beamkit_version=__version__, site="nersc",
                            block=Block(run_dir=rd, cnf=cnf_name, walltime_s=walltime_s, config=cfg.as_record()),
                            datasets=[naming.dataset(ident.owner, tag, dsconf)])
```

`tools.make_recoveries`'s `if rec.site == "nersc"` check runs before `rec.block.campaign_id`, so a NERSC record never reaches the Fermilab attribute; keep that order. `tools._after_failed_push` sets `rec.block.campaign_id, rec.block.tarball, rec.state = camp["id"], name, "created"`.

`tools.beamline_status`: `if rec.block.campaign_id is not None:`.

- [ ] **Step 7: Test assertions on the moved keys**

`grep -n '\["campaign_id"\]\|\["tarball"\]\|\["ticks"\]\|\["prodtools"\]\|\.campaign_id\|\.tarball\|\.ticks\|\.prodtools\|\.nersc\b' tests/*.py` and update each:

- `out["campaign_id"]` → `out["fermilab"]["campaign_id"]`; same for `tarball`, `ticks`, `prodtools`. A saved record read via `records.load`: `saved.campaign_id` → `saved.block.campaign_id`, etc.
- NERSC results: `rec["campaign_id"] is None` → `"fermilab" not in rec`. `rec["nersc"]["jobs"]` stays as written (the JSON key is unchanged).
- Test-built records (any `records.RunRecord(...)` in tests outside `test_records.py`): add `site=` and `block=` using the `_rec` shape above.

- [ ] **Step 8: Run the whole suite**

Run: `env -u PYTHONPATH -u BEAMKIT_PRODTOOLS_ROOT .venv/bin/python -m pytest -q -p no:cacheprovider`
Expected: all passed (count grows by the four new record tests, minus the two replaced). Fix any assertion the grep missed until green.

- [ ] **Step 9: Commit**

```bash
git add src/beamkit/records.py src/beamkit/backends/__init__.py src/beamkit/backends/fermilab.py src/beamkit/backends/nersc.py src/beamkit/tools.py tests/
git commit -m "records: one typed site block per run, old shapes refused

RunRecord carries site and block (backends.<site>.Block); run.json and
every tool result emit the block under the site's name. Records written
before 0.5.0 raise RecordError naming the file. backends.get(site) and
backends.block_type(site) replace validate_site.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0138sWPyeSCDFbbn2cB9JBDy"
```

---

### Task 2: The Fermilab backend module and per-record dispatch

**Files:**
- Modify: `src/beamkit/backends/fermilab.py` (grows: helpers and tool bodies moved from `tools.py`)
- Modify: `src/beamkit/backends/nersc.py` (`available`, `make_recoveries`, `submit_run(rec, run_as)`, `make_beamfile` signature)
- Modify: `src/beamkit/tools.py` (`make_recoveries`, `submit_run`, `beamline_status`, `beamline_outputs`, `fetch_outputs`, `make_beamfile`, `get_server_info` become dispatch)
- Create: `tests/test_backends_interface.py`
- Test: `tests/test_backends_nersc_status.py`, `tests/test_sfapi.py`, `tests/test_nersc_live.py`, `tests/test_fetch_outputs.py`, `tests/test_nersc_beamfile.py`, `tests/test_tools.py` (status key rename)

**Interfaces:**
- Consumes: `records.RunRecord.block`, `backends.get`.
- Produces, in both `backends/fermilab.py` and `backends/nersc.py`: `available() -> tuple[bool, str]`; `status(rec) -> dict | None`; `outputs(rec) -> dict`; `fetch_outputs(rec, dest, kind) -> dict`; `submit_run(rec, run_as) -> dict`; `make_recoveries(rec, run_as, confirm) -> dict`; `make_beamfile(rec, *, flavor, run_as, plane, cuts, label, publish, location, confirm) -> dict`. Also `fermilab.SLICE_MAX = 10000`, `fermilab._tick_into`, `fermilab._campaign`, `fermilab._after_failed_push`, `fermilab._outloc`, `fermilab._slice_size`, `fermilab._summary` (bodies unchanged; Task 3 wires them into the creation hooks).
- `tools.beamline_status(run_id)` returns `{"record": ..., "site": rec.site, "status": ...}`.

- [ ] **Step 1: Write the failing interface test (the seven tool-side names)**

Create `tests/test_backends_interface.py`:

```python
"""Both backends export the interface of the spec's §6, with the same
parameter names. A new site is a module that passes this test."""
import inspect

import pytest

from beamkit import backends
from beamkit.backends import fermilab, nersc

TOOL_SIDE = {
    "available": [],
    "status": ["rec"],
    "outputs": ["rec"],
    "fetch_outputs": ["rec", "dest", "kind"],
    "submit_run": ["rec", "run_as"],
    "make_recoveries": ["rec", "run_as", "confirm"],
    "make_beamfile": ["rec", "flavor", "run_as", "plane", "cuts", "label", "publish", "location", "confirm"],
}


@pytest.mark.parametrize("backend", [fermilab, nersc], ids=["fermilab", "nersc"])
@pytest.mark.parametrize("name,params", TOOL_SIDE.items())
def test_tool_side_interface(backend, name, params):
    fn = getattr(backend, name)
    assert list(inspect.signature(fn).parameters) == params, name


def test_get_returns_the_module_and_refuses_others():
    assert backends.get("fermilab") is fermilab and backends.get("nersc") is nersc
    with pytest.raises(backends.BeamkitError, match="site must be one of"):
        backends.get("perlmutter")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `env -u PYTHONPATH -u BEAMKIT_PRODTOOLS_ROOT .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_backends_interface.py`
Expected: FAIL, `AttributeError: module 'beamkit.backends.fermilab' has no attribute 'available'`.

- [ ] **Step 3: Move the Fermilab bodies**

Cut from `tools.py` and paste into `backends/fermilab.py`, bodies unchanged except where noted: `SLICE_MAX`, `_outloc`, `_slice_size`, `_summary`, `_dataset`, `_after_failed_push`, `_tick_into`, `_campaign`. Then add the seven tool-side functions:

```python
def available() -> tuple:
    return bridge.availability()


def status(rec):
    """prodtools' campaign_status for the run's campaign; None before one
    exists (enqueue_failed before the cnf reached SAM)."""
    if rec.block.campaign_id is None:
        return None
    return bridge.campaign_status(rec.block.campaign_id, mine=identity.for_record(rec).mine)


def outputs(rec) -> dict:
    """Files of the run's nts dataset in dCache, via SAM."""
    dataset = _dataset(rec)
    files = bridge.dataset_files(dataset, rec.outloc)
    return {"run_id": rec.run_id, "dataset": dataset, "location": rec.outloc,
            "n_files": len(files), "total_size": sum(f["size"] for f in files), "files": files}


def fetch_outputs(rec, dest, kind) -> dict:
    raise BeamkitError(f"run {rec.run_id} is a 'fermilab' run; its outputs are in dCache, see beamline_outputs")


def submit_run(rec, run_as) -> dict:
    raise BeamkitError(f"run {rec.run_id} is a 'fermilab' run; the Fermilab path submits through make_recoveries")


def make_recoveries(rec, run_as, confirm) -> dict:
    """One prodtools tick for this run. While the campaign is active the tick
    is scoped to it. Once prodtools has marked it complete (every slice
    submitted, rows still verifying) a scoped tick is refused as not
    active, so the bare tick is used: its verify/recovery pass reaches this
    run's rows and its top-up feeds every other active campaign in the
    ledger. The result names which form ran."""
    ident = identity.resolve(run_as, confirm)
    run_id = rec.run_id
    if rec.block.campaign_id is None:
        raise BeamkitError(f"run {run_id} has no campaign (state {rec.state!r}); nothing to recover")
    if ident.run_as != rec.run_as:
        raise BeamkitError(f"run {run_id} lives in the run_as={rec.run_as!r} ledger; tick it as that identity")
    camp = _campaign(rec.block.campaign_id, mine=ident.mine)
    scoped = camp["state"] == "active"
    t = _tick_into(rec, run_as, confirm, paths.runs_dir(),
                   failure=f"run {run_id}: tick of campaign {rec.block.campaign_id} failed",
                   campaign_id=rec.block.campaign_id if scoped else None)
    return {"run_id": run_id, "campaign_id": rec.block.campaign_id, "campaign_state": camp["state"],
            "rc": t["rc"], "needs_attention": t["needs_attention"], "output": t["output"],
            "tick_scope": "campaign" if scoped else "ledger",
            "note": ("the verify/recovery pass covered every active campaign in this ledger, not only this run"
                     if scoped else
                     f"campaign {rec.block.campaign_id} is {camp['state']!r}, so this was the bare tick: the "
                     f"verify/recovery pass reached its rows and the top-up fed every active campaign in "
                     f"this ledger")}


def make_beamfile(rec, *, flavor, run_as, plane, cuts, label, publish, location, confirm) -> dict:
    """A BLTrackFile from whatever nts files the run has in SAM, built here."""
    ident = identity.resolve(run_as, confirm, writes=publish)
    run_id = rec.run_id
    label = flavor if label is None else label
    resolved = beamfile.resolve_cuts(flavor, cuts)
    beamfile.validate_label(label)
    beamfile.validate_plane(plane)
    if publish:
        location = location or ident.default_publish_location
        publishing.check_ready(location)
    # the file is NAMED from the record's identity and PUSHED as run_as; a
    # mismatch publishes one owner's name under the other account
    if publish and ident.run_as != rec.run_as:
        raise BeamkitError(f"run {run_id} was created with run_as={rec.run_as!r}, so its beam file is "
                           f"named etc.{rec.owner}.…; publishing it as run_as={run_as!r} would push "
                           f"that name under the other identity. Pass run_as={rec.run_as!r}")
    out_dir = paths.beamfiles_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_txt = out_dir / f"{run_id}.{label}.txt"
    out_json = out_dir / f"{run_id}.{label}.json"
    if out_txt.exists() or out_json.exists():
        raise BeamkitError(f"{out_txt} exists; a beam file is never overwritten (pick another label)")
    dataset = _dataset(rec)
    files = bridge.dataset_files(dataset, rec.outloc)
    if not files:
        raise BeamkitError(f"no files in {dataset} at {rec.outloc}: nothing to build a beam file from")
    stats = beamfile.build([f["path"] for f in files], plane, resolved, out_txt)
    side = {"run_id": run_id, "flavor": flavor, "label": label, "cuts": resolved, "plane": plane,
            "path": str(out_txt), "sha256": stats["sha256"], "size": stats["size"], "rows": stats["rows_out"],
            "rows_in": stats["rows_in"], "dropped": stats["dropped"],
            "pot": len(files) * rec.events_per_job, "n_files": len(files),
            "missing_indices": beamfile.missing_indices([f["index"] for f in files], rec.njobs),
            "source_files": [f["name"] for f in files], "sam_name": None, "location": None,
            "created": records.now_utc()}
    if publish:
        sam_name = naming.beamfile_name(rec.owner, rec.tag, label, rec.dsconf)
        publishing.publish(out_txt, out_dir / sam_name, location, side["source_files"], run_as, confirm)
        side["sam_name"], side["location"] = sam_name, location
    records.atomic_write_text(out_json, json.dumps(side, indent=2) + "\n")
    rec.beamfiles.append(side)
    records.save(rec, paths.runs_dir())
    return side
```

Imports for `backends/fermilab.py`:

```python
import json
from dataclasses import dataclass, field

from beamkit import BeamkitError, beamfile, bridge, identity, naming, paths, publishing, records
```

- [ ] **Step 4: NERSC side of the seven names**

In `backends/nersc.py`:

```python
def available() -> tuple:
    """nersc.toml present and valid; the detail names account, base_dir, owner."""
    cfg_path = nersc_config.config_path(paths.home())
    try:
        cfg = config()
    except BeamkitError as e:
        return False, f"{cfg_path}: {e}"
    return True, f"account {cfg.account}, base_dir {cfg.base_dir}, owner {cfg.owner} ({cfg_path})"


def make_recoveries(rec, run_as, confirm) -> dict:
    raise BeamkitError(f"run {rec.run_id}: no recovery on nersc; submit a new run (beamline_status reports "
                       f"the missing indices)")
```

Change `submit_run(run_id, run_as)` to `submit_run(rec, run_as)`: delete the `records.load` line and the `rec.site != "nersc"` check (dispatch guarantees the site); `run_id = rec.run_id`.

Change `make_beamfile(*, rec, flavor, run_as, plane, cuts, label, publish)` to `make_beamfile(rec, *, flavor, run_as, plane, cuts, label, publish, location, confirm)`; first lines become:

```python
    if location is not None:
        raise BeamkitError("location applies to site='fermilab' publishing only")
    if publish:
        raise BeamkitError("publish=True on a NERSC run: publishing is part of harvest, which runs at Fermilab; "
                           "build with publish=False")
```

(`confirm` is accepted and unused: a NERSC run is `self` only.)

- [ ] **Step 5: `tools.py` dispatches**

Replace the bodies of these six tools (docstrings stay; they are the MCP descriptions):

```python
def _load(run_id):
    return records.load(run_id, paths.runs_dir())


def make_recoveries(run_id: str, run_as: str, confirm: bool = False) -> dict:
    """<docstring unchanged>"""
    rec = _load(run_id)
    return backends.get(rec.site).make_recoveries(rec, run_as, confirm)


def submit_run(run_id: str, run_as: str) -> dict:
    """<docstring unchanged>"""
    rec = _load(run_id)
    return backends.get(rec.site).submit_run(rec, run_as)


def beamline_status(run_id: str) -> dict:
    """The run record with the site's view of it: prodtools' campaign_status
    (site='fermilab', None before a campaign exists) or the Slurm job states,
    CFS output counts and beam-file jobs (site='nersc')."""
    rec = _load(run_id)
    status = backends.get(rec.site).status(rec)      # nersc: also reconciles and saves the record
    return {"record": rec.to_dict(), "site": rec.site, "status": status}


def beamline_outputs(run_id: str) -> dict:
    """<docstring unchanged>"""
    rec = _load(run_id)
    return backends.get(rec.site).outputs(rec)


def fetch_outputs(run_id: str, dest: str, kind: str = "nts") -> dict:
    """<docstring unchanged>"""
    rec = _load(run_id)
    return backends.get(rec.site).fetch_outputs(rec, dest, kind)


def make_beamfile(run_id: str, flavor: str, run_as: str, plane: str = "Z3712", cuts: Optional[dict] = None,
                  publish: bool = False, location: Optional[str] = None, confirm: bool = False,
                  label: Optional[str] = None, site: str = "fermilab") -> dict:
    """<docstring unchanged>"""
    backends.get(site)
    rec = _load(run_id)
    if rec.site != site:
        raise BeamkitError(f"run {run_id} is a {rec.site!r} run; pass site={rec.site!r}")
    return backends.get(site).make_beamfile(rec, flavor=flavor, run_as=run_as, plane=plane, cuts=cuts,
                                            label=label, publish=publish, location=location, confirm=confirm)
```

`get_server_info`:

```python
def get_server_info() -> dict:
    """beamkit version, which backends this host can drive, directories, limits."""
    f_ok, f_detail = fermilab_backend.available()
    n_ok, n_detail = nersc_backend.available()
    return {"name": "beamkit", "version": __version__, "python": sys.executable,
            "prodtools": bridge.prodtools_info() if f_ok else None, "dev_dir": identity.dev_dir_from_env(),
            "backends": {"fermilab": {"available": f_ok, "detail": f_detail},
                         "nersc": {"available": n_ok, "detail": n_detail,
                                   "config": str(nersc_config.config_path(paths.home()))}},
            "deck_url": DEFAULT_DECK_URL,
            "decks_dir": str(paths.decks_dir()), "records_dir": str(paths.runs_dir()),
            "beamfiles_dir": str(paths.beamfiles_dir()), "slice_max": fermilab_backend.SLICE_MAX,
            "walltime_default": nersc_backend.WALLTIME_DEFAULT}
```

`tools.run_beamline` still calls `_outloc`, `_slice_size`, `_is_retryable_run_dir`, `_after_failed_push`, `_tick_into`: reference them as `fermilab_backend._outloc` etc. until Task 3 replaces the function. Delete `backends.validate_site` uses (`backends.get(site)` is the check). Remove imports `tools.py` no longer needs (`json`, `beamfile`, `publishing`).

- [ ] **Step 6: Status key rename in tests**

`grep -n 'st\["nersc"\]\|\["campaign"\]\|\[.nersc.\]' tests/*.py`: every `st["nersc"]` → `st["status"]`; `out["campaign"]` → `out["status"]`. In `tests/test_backends_nersc_status.py` line `assert st["campaign"] is None` → `assert st["site"] == "nersc"`. In `tests/test_tools.py` the two `out["campaign"]` lines become `out["status"]`.

Tests that call `nersc.submit_run("T.e470313", "self")` or `nersc.make_beamfile(rec=...)` directly: switch them to `tools.submit_run(...)` / `tools.make_beamfile(..., site="nersc")`.

- [ ] **Step 7: Run the suite**

Run: `env -u PYTHONPATH -u BEAMKIT_PRODTOOLS_ROOT .venv/bin/python -m pytest -q -p no:cacheprovider`
Expected: green. `tests/test_server.py::test_instructions_name_the_nersc_path` and the tool-table tests still pass because `server.TOOLS` and the docstrings did not move.

- [ ] **Step 8: Commit**

```bash
git add src/beamkit/backends/ src/beamkit/tools.py tests/
git commit -m "backends: fermilab.py as a peer of nersc.py; tools dispatch on rec.site

The seven per-record tools (status, outputs, fetch_outputs, submit_run,
make_recoveries, make_beamfile, available) exist in both backends with
one signature; tools.py loads the record and delegates. beamline_status
returns {record, site, status}. tests/test_backends_interface.py pins
the names.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0138sWPyeSCDFbbn2cB9JBDy"
```

---

### Task 3: `runs.py` owns Run creation

**Files:**
- Create: `src/beamkit/runs.py`
- Modify: `src/beamkit/naming.py` (`+ dsconf_of`)
- Modify: `src/beamkit/backends/fermilab.py` (creation hooks)
- Modify: `src/beamkit/backends/nersc.py` (creation hooks; delete `run_beamline`, `_retryable`, `_taken`)
- Modify: `src/beamkit/tools.py` (`run_beamline` builds a request; delete `_is_retryable_run_dir`; `walltime_s` default)
- Create: `tests/test_runs.py`
- Modify: `tests/test_backends_interface.py` (full interface), `tests/test_naming.py`, `tests/test_tools.py` and `tests/test_backends_nersc_run.py` (error text of enqueue failure)

**Interfaces:**
- Produces: `runs.RunRequest` (frozen dataclass; fields in Step 3); `runs.create(req) -> dict`; `naming.dsconf_of(cnf_name) -> str`. In both backends: `resolve_identity(req) -> Identity`; `validate(req, ident) -> RunRequest`; `taken(ident, tag) -> Callable[[str], bool]`; `retryable(rec) -> bool`; `new_block(req, ident, pin, run_id, dsconf) -> Block`; `enqueue(rec, req, ident, pin, rdir) -> None`; `after_failure(rec, ident) -> str`; `submit(rec, ident, confirm) -> None`.
- Consumes: Task 1 and 2 names.

- [ ] **Step 1: Failing tests for `runs.create` with a stub backend**

Create `tests/test_runs.py`:

```python
"""runs.create with a stub backend: the ordering rules of the spec's §5.2,
with no facility in the loop."""
import json
from types import SimpleNamespace

import pytest

from beamkit import BeamkitError, backends, decks, identity, paths, records, runs
from beamkit.backends import fermilab

SHA = "e470313" + "0" * 33


class Stub:
    """A backend as a bag of functions that record their calls."""

    def __init__(self, *, taken=lambda name: False, fail_enqueue=None, njobs_from_site=None):
        self.calls = []
        self._taken, self._fail, self._njobs = taken, fail_enqueue, njobs_from_site

    def resolve_identity(self, req):
        self.calls.append("resolve_identity")
        return identity.Identity(run_as="self", owner="u", dev_dir=None)

    def validate(self, req, ident):
        self.calls.append("validate")
        if req.walltime_s is not None:
            raise BeamkitError("walltime_s applies to site='nersc' only")
        return req if req.slice_size is not None else runs.replace(req, slice_size=req.njobs)

    def taken(self, ident, tag):
        self.calls.append("taken")
        return self._taken

    def retryable(self, rec):
        return rec.state == "enqueue_failed" and rec.block.campaign_id is None

    def new_block(self, req, ident, pin, run_id, dsconf):
        self.calls.append("new_block")
        return fermilab.Block(prodtools={"root": "/pt", "commit": "c" * 40, "dev_dir": None})

    def enqueue(self, rec, req, ident, pin, rdir):
        self.calls.append("enqueue")
        self.record_on_disk_at_enqueue = (rdir / "run.json").is_file()
        if self._fail:
            raise self._fail
        rec.block.campaign_id = 7
        if self._njobs is not None:
            rec.njobs = self._njobs

    def after_failure(self, rec, ident):
        self.calls.append("after_failure")
        return "; stub outcome"

    def submit(self, rec, ident, confirm):
        self.calls.append("submit")
        rec.state = "submitted"
        records.save(rec, paths.runs_dir())


@pytest.fixture
def stub(monkeypatch, tmp_path):
    d = tmp_path / "deckcache" / SHA[:12]
    d.mkdir(parents=True)
    (d / "Mu2E.in").write_text("param -unset First_Event=1\n")
    monkeypatch.setattr(decks, "materialize", lambda url, ref, cache: decks.DeckPin(url, ref, SHA, str(d), True))
    s = Stub()
    real_get = backends.get
    monkeypatch.setattr(backends, "get", lambda site: s if site == "stub" else real_get(site))
    monkeypatch.setattr(backends, "SITES", (*backends.SITES, "stub"))   # records.from_dict checks membership
    return s


def _req(**kw):
    base = dict(tag="T", run_as="self", site="stub", deck_ref=SHA, events_per_job=10, njobs=3)
    base.update(kw)
    return runs.RunRequest(**base)


def test_happy_path_order_and_record(stub):
    out = runs.create(_req())
    assert stub.calls == ["resolve_identity", "validate", "taken", "new_block", "enqueue", "submit"]
    assert out["state"] == "submitted" and out["site"] == "stub" and out["datasets"] == ["nts.u.T.e470313.root"]
    assert stub.record_on_disk_at_enqueue, "the record is saved before the first remote effect"


def test_refused_input_burns_no_dsconf_and_writes_no_record(stub):
    with pytest.raises(BeamkitError, match="walltime_s applies"):
        runs.create(_req(walltime_s=5))
    assert "taken" not in stub.calls and not (paths.runs_dir()).exists()


def test_missing_main_input_is_refused_before_any_probe(stub):
    with pytest.raises(BeamkitError, match=r"main_input 'Nope\.in' not found in deck dir"):
        runs.create(_req(main_input="Nope.in"))
    assert "taken" not in stub.calls and not (paths.runs_dir()).exists()


def test_enqueue_failure_is_annotated_with_the_backend_outcome(stub):
    stub._fail = RuntimeError("boom")
    with pytest.raises(BeamkitError, match=r"run T\.e470313: enqueue failed \(boom\); stub outcome"):
        runs.create(_req())
    rec = records.load("T.e470313", paths.runs_dir())
    assert rec.state == "enqueue_failed" and rec.error == "RuntimeError: boom"
    assert stub.calls[-2:] == ["enqueue", "after_failure"] and "submit" not in stub.calls


def test_njobs_mismatch_raises_after_save_and_before_submit(stub):
    stub._njobs = 5
    with pytest.raises(BeamkitError, match="holds 5 jobs but this call asked for 3"):
        runs.create(_req())
    rec = records.load("T.e470313", paths.runs_dir())
    assert rec.njobs == 5 and rec.state == "created" and "submit" not in stub.calls


def test_submit_false_never_submits(stub):
    out = runs.create(_req(submit=False))
    assert out["state"] == "created" and "submit" not in stub.calls


def test_retryable_prior_attempt_is_overwritten_and_other_dirs_refused(stub):
    stub._fail = RuntimeError("boom")
    with pytest.raises(BeamkitError):
        runs.create(_req())
    stub._fail = None
    out = runs.create(_req())
    assert out["state"] == "submitted"
    with pytest.raises(BeamkitError, match="already exists; a run id is never reused"):
        runs.create(_req())


def test_dsconf_collision_takes_the_next_suffix(stub):
    stub._taken = lambda name: name == "cnf.u.T.e470313.0.tar"
    out = runs.create(_req())
    assert out["dsconf"] == "e470313-001" and out["run_id"] == "T.e470313-001"
```

Note the fixture: `backends.get` is replaced by a wrapper so `"stub"` resolves to the stub while real sites still resolve, and `backends.SITES` gains `"stub"` because `records.from_dict` checks membership. `backends.block_type` looks up `get` at call time, so it follows the patch with no change to `backends/__init__.py`.

- [ ] **Step 2: Run to verify failure**

Run: `env -u PYTHONPATH -u BEAMKIT_PRODTOOLS_ROOT .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_runs.py`
Expected: FAIL, `ImportError: cannot import name 'runs'`.

- [ ] **Step 3: `runs.py`**

```python
"""Run creation, once. The ritual of the spec's §5.2: every caller value is
refused before the deck fetch and the site probe (a refused call burns no
dsconf and writes no record); the record is saved before the first remote
effect; an enqueue that fails leaves an enqueue_failed record the next
call may overwrite. The site's part is the backend's eight hooks."""
from dataclasses import dataclass, replace

from beamkit import BeamkitError, __version__, backends, compose, decks, naming, paths, records
from beamkit.decks import DEFAULT_DECK_URL
from pathlib import Path


@dataclass(frozen=True)
class RunRequest:
    """Every caller value of run_beamline. slice_size is Fermilab's,
    walltime_s is NERSC's; the backend refuses the other's and fills
    its own default in validate()."""
    tag: str
    run_as: str
    site: str = "fermilab"
    deck_ref: str | None = None
    deck_dir: str | None = None
    deck_url: str = DEFAULT_DECK_URL
    params: dict | None = None
    events_per_job: int = 1000
    njobs: int = 1
    main_input: str = "Mu2E.in"
    outloc: str = "scratch"
    dsconf: str | None = None
    submit: bool = True
    confirm: bool = False
    slice_size: int | None = None
    walltime_s: int | None = None


def create(req: RunRequest) -> dict:
    backend = backends.get(req.site)
    ident = backend.resolve_identity(req)
    naming.validate_tag(req.tag)
    params = compose.validate_inputs(events_per_job=req.events_per_job, njobs=req.njobs, outloc=req.outloc,
                                     params=req.params)
    req = backend.validate(replace(req, params=params), ident)
    pin = decks.pin(req.deck_ref, req.deck_dir, req.deck_url, paths.decks_dir(), ident.production)
    if not (Path(pin.dir) / req.main_input).is_file():
        raise BeamkitError(f"main_input {req.main_input!r} not found in deck dir {pin.dir}")
    runs_dir = paths.runs_dir()

    def retryable(run_id, runs_dir):
        rec = records.try_load(run_id, runs_dir)
        return rec is not None and backend.retryable(rec)

    dsconf = naming.allocate_dsconf(ident.owner, req.tag, naming.dsconf_base(pin.sha),
                                    backend.taken(ident, req.tag), explicit=req.dsconf)
    run_id = naming.run_id(req.tag, dsconf)
    rdir = records.claim_run_dir(runs_dir, run_id, retryable)
    rec = records.RunRecord(run_id=run_id, tag=req.tag, dsconf=dsconf, owner=ident.owner, run_as=req.run_as,
                            deck=pin.as_record(), params=req.params, events_per_job=req.events_per_job,
                            njobs=req.njobs, outloc=req.outloc, slice_size=req.slice_size, state="created",
                            site=req.site, block=backend.new_block(req, ident, pin, run_id, dsconf),
                            datasets=[naming.dataset(ident.owner, req.tag, dsconf)],
                            created=records.now_utc(), beamkit_version=__version__)
    records.save(rec, runs_dir)
    try:
        backend.enqueue(rec, req, ident, pin, rdir)
    except Exception as e:
        rec.state, rec.error = "enqueue_failed", f"{type(e).__name__}: {e}"
        outcome = backend.after_failure(rec, ident)
        records.save(rec, runs_dir)
        raise BeamkitError(f"run {run_id}: enqueue failed ({e}){outcome}") from e
    records.save(rec, runs_dir)
    if rec.njobs != req.njobs:
        raise BeamkitError(f"run {run_id}: the site reports the run holds {rec.njobs} jobs but this call asked "
                           f"for {req.njobs}; the record now carries {rec.njobs} and nothing was submitted. "
                           f"Understand the difference, then make_recoveries({run_id!r}, {req.run_as!r}) "
                           f"submits it")
    if req.submit:
        backend.submit(rec, ident, req.confirm)
    return rec.to_dict()
```

(`replace` is re-exported for the stub in tests: `from dataclasses import replace` at module level is enough.)

- [ ] **Step 4: `naming.dsconf_of`**

In `naming.py`, after `cnf_name`:

```python
def dsconf_of(cnf_name) -> str:
    """The dsconf a cnf name carries: its fourth dot-field."""
    return cnf_name.split(".")[3]
```

and in `tests/test_naming.py`:

```python
def test_dsconf_of_reads_the_fourth_field():
    assert naming.dsconf_of("cnf.u.T.e470313-002.0.tar") == "e470313-002"
```

- [ ] **Step 5: Fermilab creation hooks**

Append to `backends/fermilab.py`:

```python
# --- creation hooks (runs.create)

def resolve_identity(req):
    return identity.resolve(req.run_as, req.confirm)


def validate(req, ident):
    """Fermilab's own arguments: no walltime (prodtools sizes the jobs), the
    slice size in range, and the outloc/identity pair. dev_dir_for_shipping
    is called here so a production call with a dev checkout is refused
    before the deck fetch."""
    if req.walltime_s is not None:
        raise BeamkitError("walltime_s applies to site='nersc' only; the Fermilab path takes its resources "
                           "from prodtools")
    ident.dev_dir_for_shipping()
    _outloc(req.outloc, ident)
    return replace(req, slice_size=_slice_size(req.slice_size, req.njobs))


def taken(ident, tag):
    return bridge.cnf_exists


def retryable(rec) -> bool:
    """A run dir left by a push that never reached prodtools: state
    'enqueue_failed' with no campaign. Nothing was created anywhere else, so
    the retry overwrites it in place."""
    return rec.state == "enqueue_failed" and rec.block.campaign_id is None


def new_block(req, ident, pin, run_id, dsconf):
    return Block(prodtools=dict(bridge.prodtools_info(), dev_dir=ident.dev_dir_for_shipping()))


def enqueue(rec, req, ident, pin, rdir):
    """entry.json, then push_cnf: the cnf into SAM and its campaign into the
    ledger. rec.njobs becomes the campaign's count, which missing_indices
    is measured against."""
    entry = compose.entry(tag=rec.tag, dsconf=rec.dsconf, deck_dir=pin.dir, main_input=req.main_input,
                          events_per_job=req.events_per_job, njobs=req.njobs, outloc=req.outloc, params=req.params)
    entry_path = compose.write_entry_json(entry, rdir / "entry.json")
    pushed = bridge.push_cnf(entry_path, rec.tag, rec.dsconf, rec.slice_size, req.run_as, req.confirm,
                             prodtools_dir=rec.block.prodtools["dev_dir"])
    rec.block.campaign_id, rec.block.tarball = pushed["campaign_id"], pushed["tarball"]
    rec.njobs = pushed["njobs"]


def after_failure(rec, ident) -> str:
    return _after_failed_push(rec, ident)


def submit(rec, ident, confirm):
    _tick_into(rec, ident.run_as, confirm, paths.runs_dir(),
               failure=f"run {rec.run_id}: campaign {rec.block.campaign_id} was created but the first tick "
                       f"failed; call make_recoveries({rec.run_id!r}, {ident.run_as!r}) to submit it",
               campaign_id=rec.block.campaign_id)
```

Add `from dataclasses import dataclass, field, replace` and `compose` to the imports. In `_after_failed_push`, delete the line `rec.datasets = [_dataset(rec)]` (the record carries it from creation).

- [ ] **Step 6: NERSC creation hooks**

In `backends/nersc.py` delete `run_beamline`, `_taken`, `_retryable`, and add:

```python
# --- creation hooks (runs.create)

def resolve_identity(req):
    return identity.resolve(req.run_as, site="nersc", owner=config().owner)


def validate(req, ident):
    """NERSC's own arguments: no slice_size (procs_per_node slices the run),
    outputs stay on CFS, walltime in range with its default filled."""
    if req.slice_size is not None:
        raise BeamkitError("slice_size applies to site='fermilab' only; a NERSC run is sliced by "
                           "procs_per_node from nersc.toml")
    cfg = config()
    if req.outloc != "scratch":
        raise BeamkitError(f"outloc={req.outloc!r}: outputs of a NERSC run stay on CFS under {cfg.base_dir}; "
                           f"pass outloc='scratch' (the default) or omit it")
    walltime_s = WALLTIME_DEFAULT if req.walltime_s is None else validate_walltime(req.walltime_s)
    return replace(req, walltime_s=walltime_s, slice_size=cfg.procs_per_node)


def taken(ident, tag):
    """allocate_dsconf's probe: a cnf name is taken when its run directory
    exists on CFS, unless a retryable local record explains that directory
    (a failed layout of our own): probing that as taken would burn a new
    dsconf and orphan the enqueue_failed record."""
    cfg, client = _cfg_client()
    runs_dir = paths.runs_dir()

    def probe(cnf_name):
        run_id = naming.run_id(tag, naming.dsconf_of(cnf_name))
        rec = records.try_load(run_id, runs_dir)
        if rec is not None and retryable(rec):
            return False
        return client.exists(run_dir(cfg, run_id))
    return probe


def retryable(rec) -> bool:
    return rec.site == "nersc" and rec.state == "enqueue_failed" and not rec.block.jobs


def new_block(req, ident, pin, run_id, dsconf):
    cfg = config()
    return Block(run_dir=run_dir(cfg, run_id), cnf=naming.cnf_name(ident.owner, req.tag, dsconf),
                 walltime_s=req.walltime_s, config=cfg.as_record())


def enqueue(rec, req, ident, pin, rdir):
    """The cnf, job.sh and inner.sh built here and laid out on CFS."""
    cfg, client = _cfg_client()
    rd, cnf_name = rec.block.run_dir, rec.block.cnf
    local_cnf = rdir / cnf_name
    local_cnf.unlink(missing_ok=True)
    nersc_cnf.build_cnf(pin.dir, nersc_cnf.jobpars(owner=ident.owner, tag=rec.tag, dsconf=rec.dsconf,
                                                    main_input=req.main_input, events_per_job=req.events_per_job,
                                                    njobs=req.njobs, params=req.params), local_cnf)
    (rdir / "job.sh").write_text(nersc_templates.render_job(cfg, rd))
    (rdir / "inner.sh").write_text(nersc_templates.render_inner(
        cfg, run_id=rec.run_id, run_dir=rd, owner=ident.owner, tag=rec.tag, dsconf=rec.dsconf,
        events_per_job=req.events_per_job, main_input=req.main_input, params=req.params))
    _remote_layout(cfg, client, rd, [(local_cnf, f"{rd}/{cnf_name}"), (rdir / "job.sh", f"{rd}/job.sh"),
                                     (rdir / "inner.sh", f"{rd}/inner.sh")])


def after_failure(rec, ident) -> str:
    return "; fix the cause and call again, the run dir is reused"


def submit(rec, ident, confirm):
    cfg, client = _cfg_client()
    _submit_missing(rec, cfg, client, paths.runs_dir())
```

Add `replace` to the `dataclasses` import. `compose` is no longer imported by nersc.py if nothing else uses it; drop it from the import line if so.

- [ ] **Step 7: `tools.run_beamline` builds a request**

Replace the whole function:

```python
def run_beamline(tag: str, run_as: str, deck_ref: Optional[str] = None, params: Optional[dict] = None,
                 events_per_job: int = 1000, njobs: int = 1, main_input: str = "Mu2E.in",
                 outloc: str = "scratch", dsconf: Optional[str] = None, slice_size: Optional[int] = None,
                 submit: bool = True, confirm: bool = False, deck_dir: Optional[str] = None,
                 deck_url: str = DEFAULT_DECK_URL, site: str = "fermilab",
                 walltime_s: Optional[int] = None) -> dict:
    """<docstring unchanged>"""
    return runs.create(runs.RunRequest(tag=tag, run_as=run_as, site=site, deck_ref=deck_ref, deck_dir=deck_dir,
                                       deck_url=deck_url, params=params, events_per_job=events_per_job,
                                       njobs=njobs, main_input=main_input, outloc=outloc, dsconf=dsconf,
                                       submit=submit, confirm=confirm, slice_size=slice_size,
                                       walltime_s=walltime_s))
```

Delete `_is_retryable_run_dir` and the `fermilab_backend._…` references that only `run_beamline` used. `tools.py` imports become `from beamkit import BeamkitError, __version__, backends, bridge, identity, nersc_config, paths, records, runs` plus the two backend modules for `get_server_info`.

- [ ] **Step 8: Full interface test**

In `tests/test_backends_interface.py` add:

```python
CREATION = {
    "resolve_identity": ["req"],
    "validate": ["req", "ident"],
    "taken": ["ident", "tag"],
    "retryable": ["rec"],
    "new_block": ["req", "ident", "pin", "run_id", "dsconf"],
    "enqueue": ["rec", "req", "ident", "pin", "rdir"],
    "after_failure": ["rec", "ident"],
    "submit": ["rec", "ident", "confirm"],
}


@pytest.mark.parametrize("backend", [fermilab, nersc], ids=["fermilab", "nersc"])
@pytest.mark.parametrize("name,params", CREATION.items())
def test_creation_interface(backend, name, params):
    assert list(inspect.signature(getattr(backend, name)).parameters) == params, name


@pytest.mark.parametrize("backend", [fermilab, nersc], ids=["fermilab", "nersc"])
def test_block_is_a_dataclass(backend):
    import dataclasses
    assert dataclasses.is_dataclass(backend.Block)
```

- [ ] **Step 9: Error-text updates in existing tests**

`grep -n "push_cnf failed\|nothing was submitted\|prodtools reports campaign\|the run dir is reused" tests/*.py`. The enqueue failure now reads `run <id>: enqueue failed (<e>)<outcome>`:

- `tests/test_tools.py`: `match="push_cnf failed"` → `match="enqueue failed"`; the njobs test's match becomes `"holds 5 jobs but this call asked for 3"` (adjust numbers to the test's values).
- `tests/test_backends_nersc_run.py`: `match="nothing was submitted"` → `match=r"enqueue failed \(.*\); fix the cause and call again, the run dir is reused"`.
- The NERSC test that retried after a layout failure with an explicit or base dsconf keeps its assertion (same dsconf, same run dir) — it now passes through the probe rule, not the deleted branch. If a test called `nersc.run_beamline(...)` directly, switch it to `tools.run_beamline(..., site="nersc")`.
- Tests that asserted `walltime_s` handling with the numeric default still pass: `None` and `172800` both yield `WALLTIME_DEFAULT` in the block.

- [ ] **Step 10: Run the suite**

Run: `env -u PYTHONPATH -u BEAMKIT_PRODTOOLS_ROOT .venv/bin/python -m pytest -q -p no:cacheprovider`
Expected: green.

- [ ] **Step 11: Commit**

```bash
git add src/beamkit/runs.py src/beamkit/naming.py src/beamkit/backends/ src/beamkit/tools.py tests/test_runs.py tests/test_backends_interface.py tests/test_naming.py tests/test_tools.py tests/test_backends_nersc_run.py
git commit -m "runs: Run creation once, backends supply eight hooks

runs.create runs the ritual (validate, pin, allocate, claim, save before
any remote effect, enqueue, njobs check, submit) for both sites;
tools.run_beamline builds a RunRequest. The NERSC dsconf-reuse branch is
now a rule of its taken() probe. walltime_s defaults to None at the tool.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0138sWPyeSCDFbbn2cB9JBDy"
```

---

### Task 4: `test_tools.py` through the fake prodtools server

**Files:**
- Modify: `tests/fake_prodtools_mcp.py`
- Modify: `tests/test_tools.py` (fixture and the failure-injection tests)
- Create: `tests/test_backends_fermilab.py`
- Modify: `tests/conftest.py` (clear the two new knobs)

**Interfaces:**
- Produces: fake knobs `FAKE_PRODTOOLS_FAIL=<tool>[,<tool>...]`, `FAKE_PRODTOOLS_CNF_EXISTS=<name>[,<name>...]`, `FAKE_PRODTOOLS_CAMPAIGNS=<json list of rows>`; fake `push_cnf` returns `njobs` read from the entry JSON.
- Consumes: `conftest.fake_prodtools_root`, `bridge.reset()`.

- [ ] **Step 1: Fake server knobs**

In `tests/fake_prodtools_mcp.py`:

1. Rename the module import `import json` → `import json as jsonlib` and update its uses (`jsonlib.loads` in `dataset_files`, `jsonlib.dumps` in `_record` if used). The `push_cnf` parameter named `json` shadows the module, which is why.
2. `_trip`: `if tool in filter(None, os.environ.get("FAKE_PRODTOOLS_FAIL", "").split(",")):`.
3. `push_cnf` returns `"njobs": jsonlib.loads(open(json).read())[0]["njobs"]`.
4. `locate_file`: `exists = name in filter(None, os.environ.get("FAKE_PRODTOOLS_CNF_EXISTS", "").split(","))` replaces the `endswith` rule. Update `tests/test_bridge.py` tests that relied on the old rule (`grep -n "e470313.0.tar\|locate_file\|cnf_exists" tests/test_bridge.py`): set the knob in those tests to the name they expect to exist.
5. `list_campaigns`: rows come from `jsonlib.loads(os.environ.get("FAKE_PRODTOOLS_CAMPAIGNS") or '[{"id": 7, "state": "complete", "tarball": "cnf.u.T.e470313.0.tar"}]')`.

In `tests/conftest.py` add `"FAKE_PRODTOOLS_CNF_EXISTS", "FAKE_PRODTOOLS_CAMPAIGNS"` to the `delenv` loop.

- [ ] **Step 2: Run the bridge tests to verify they still pass**

Run: `env -u PYTHONPATH -u BEAMKIT_PRODTOOLS_ROOT .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_bridge.py tests/test_mcpclient.py`
Expected: green (after the knob edits from step 1.4).

- [ ] **Step 3: Rewrite the `test_tools.py` fixture**

Replace `fake_bridge` with:

```python
@pytest.fixture
def prodtools(fake_prodtools_root, monkeypatch, deck):
    """The real bridge against the fake prodtools servers. Only what is not
    prodtools stays patched: the deck checkout and the account name."""
    from beamkit import decks
    monkeypatch.setattr(decks, "materialize",
                        lambda url, ref, cache: decks.DeckPin(url, ref, SHA, str(deck), True))
    monkeypatch.setattr(identity, "_username", lambda: "u")
    return fake_prodtools_root


def calls(root, tool):
    """The fake's call log for one tool, oldest first."""
    log = root.parent / "calls.jsonl"
    if not log.exists():
        return []
    return [json.loads(l)["args"] for l in log.read_text().splitlines() if json.loads(l)["tool"] == tool]
```

(Check `_record` in the fake for the exact line shape and adapt `calls()`.) Then, test by test:

- Every `fake_bridge` parameter → `prodtools`. Assertions on `calls["push_cnf"]` → `calls(prodtools, "push_cnf")`; `calls["tick"]` → `calls(prodtools, "run_submissions")`; `calls["cnf_exists"]` → `calls(prodtools, "locate_file")`; `calls["campaigns"]` → `calls(prodtools, "list_campaigns")`.
- `campaign_status` result assertion: the fake returns `{"called": {"campaign_id": 7, "mine": True}}`; assert on that.
- Failure injection: replace each `monkeypatch.setattr(tools.bridge, ...)` with the knob, then `bridge.reset()` so the next call spawns a child that sees it:

| Old patch | Knob |
| --- | --- |
| `push_cnf` raises | `FAKE_PRODTOOLS_FAIL=push_cnf` |
| `cnf_exists` returns True for the name | `FAKE_PRODTOOLS_CNF_EXISTS=cnf.u.T.e470313.0.tar` |
| `campaigns` returns `[]` | `FAKE_PRODTOOLS_CAMPAIGNS=[]` |
| `campaigns` returns an active row | `FAKE_PRODTOOLS_CAMPAIGNS=[{"id": 7, "state": "active", "tarball": "cnf.u.T.e470313.0.tar"}]` |
| `campaigns` raises | `FAKE_PRODTOOLS_FAIL=push_cnf,list_campaigns` |
| `cnf_exists` raises | `FAKE_PRODTOOLS_FAIL=push_cnf,locate_file` |
| `tick` raises | `FAKE_PRODTOOLS_FAIL=run_submissions` |
| `push_cnf` fails once then succeeds | set `FAIL=push_cnf`, `bridge.reset()`, call, expect failure; `delenv`, `bridge.reset()`, call again |

Write a helper in the file:

```python
def knob(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(f"FAKE_PRODTOOLS_{k}", v)
    bridge.reset()
```

- The dsconf-collision test: `knob(monkeypatch, CNF_EXISTS="cnf.u.T.e470313.0.tar")`, expect `dsconf == "e470313-001"`.
- `make_recoveries` tests that need an active campaign: `knob(monkeypatch, CAMPAIGNS='[{"id": 7, "state": "active", "tarball": "cnf.u.T.e470313.0.tar"}]')`.
- `make_beamfile` Fermilab tests: the fake `dataset_files` returns two files with `/pnfs/...` paths; `beamfile.build` cannot read them. Keep whatever the tests patched there before (`grep -n "beamfile" tests/test_tools.py`); if they patched `tools.beamfile.build`, patch `fermilab.beamfile.build` (same object, `beamfile` module attribute) — that is a beamfile patch, not a bridge patch, and stays allowed.
- `get_server_info` test: `fermilab.available()` now spawns the read child; the fake's `get_server_info` supplies `prodtools_info`. Assert on `out["backends"]["fermilab"]["available"] is True`.

- [ ] **Step 4: `after_failure` outcomes directly**

The three SAM-probe outcomes are cheaper to read as unit tests on the Fermilab backend than through the fake. Create `tests/test_backends_fermilab.py` (the one file allowed to patch `bridge`):

```python
"""after_failure: what a failed push left behind. The only file that
patches bridge; everything else drives the fake prodtools servers."""
import pytest

from beamkit import bridge, identity, records
from beamkit.backends import fermilab


def _rec(state="enqueue_failed"):
    return records.RunRecord(run_id="T.e470313", tag="T", dsconf="e470313", owner="u", run_as="self", deck={},
                             params={}, events_per_job=10, njobs=3, outloc="scratch", slice_size=3, state=state,
                             site="fermilab", block=fermilab.Block(prodtools={}))


IDENT = identity.Identity(run_as="self", owner="u", dev_dir=None)


def test_not_in_sam_means_the_dsconf_is_free(monkeypatch):
    monkeypatch.setattr(bridge, "cnf_exists", lambda n: False)
    assert "dsconf 'e470313' is free" in fermilab.after_failure(_rec(), IDENT)


def test_in_sam_without_campaign_means_burned(monkeypatch):
    monkeypatch.setattr(bridge, "cnf_exists", lambda n: True)
    monkeypatch.setattr(bridge, "campaigns", lambda mine: [])
    assert "is burned and the next call allocates the next suffix" in fermilab.after_failure(_rec(), IDENT)


def test_in_sam_with_campaign_is_adopted(monkeypatch):
    monkeypatch.setattr(bridge, "cnf_exists", lambda n: True)
    monkeypatch.setattr(bridge, "campaigns",
                        lambda mine: [{"id": 9, "state": "active", "tarball": "cnf.u.T.e470313.0.tar"}])
    rec = _rec()
    out = fermilab.after_failure(rec, IDENT)
    assert rec.block.campaign_id == 9 and rec.state == "created" and "campaign 9 exists" in out


def test_probe_failures_say_so(monkeypatch):
    monkeypatch.setattr(bridge, "cnf_exists", lambda n: (_ for _ in ()).throw(bridge.BridgeError("sam down")))
    assert "could not be determined" in fermilab.after_failure(_rec(), IDENT)
    monkeypatch.setattr(bridge, "cnf_exists", lambda n: True)
    monkeypatch.setattr(bridge, "campaigns", lambda mine: (_ for _ in ()).throw(bridge.BridgeError("locked")))
    assert "the ledger could not be read" in fermilab.after_failure(_rec(), IDENT)
```

With these in place, the four `test_push_fails_*` tests in `test_tools.py` reduce to two through the fake: `FAIL=push_cnf` alone (dsconf free, record `enqueue_failed`, retry reuses the dir) and `FAIL=push_cnf` + `CNF_EXISTS` + an active `CAMPAIGNS` row (adopted: state `created`, `make_recoveries` then submits). Delete the other two from `test_tools.py`.

- [ ] **Step 5: No bridge patches remain elsewhere**

Run: `grep -rn "setattr(.*bridge" tests/ | grep -v test_backends_fermilab.py`
Expected: no output.

- [ ] **Step 6: Run the suite**

Run: `env -u PYTHONPATH -u BEAMKIT_PRODTOOLS_ROOT .venv/bin/python -m pytest -q -p no:cacheprovider`
Expected: green. `test_tools.py` is slower now (child spawn per test, about 1 s each); if the whole file exceeds 60 s, share one spawned pair across the module by making `prodtools` module-scoped with the `bridge.reset()` calls left to the knob helper.

- [ ] **Step 7: Commit**

```bash
git add tests/
git commit -m "tests: test_tools drives the fake prodtools servers, not patched bridge functions

The fake gains FAIL as a list, CNF_EXISTS and CAMPAIGNS knobs, and reads
njobs from the entry. after_failure's outcomes are unit-tested in
test_backends_fermilab.py, the one file that still patches bridge.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0138sWPyeSCDFbbn2cB9JBDy"
```

---

### Task 5: Version 0.5.0 and docs

**Files:**
- Modify: `src/beamkit/__init__.py`, `pyproject.toml`, `README.md`
- Modify: `docs/architecture.md`, `docs/tools.md`
- Modify: `tests/test_version.py` / `tests/test_packaging.py` if they pin the number

**Interfaces:** none.

- [ ] **Step 1: Version**

`__version__ = "0.5.0"` in `src/beamkit/__init__.py`; `version = "0.5.0"` in `pyproject.toml`; README's two `@v0.4.0` → `@v0.5.0`. Run `grep -rn "0\.4\.0" src tests pyproject.toml README.md docs/*.md` and fix every hit outside `docs/specs/` and `docs/plans/`.

- [ ] **Step 2: `docs/architecture.md`**

- Layer diagram: add `runs.py` between `tools.py` and the backends; add `backends/fermilab.py` beside `backends/nersc.py` with `bridge.py` under it; move `TOOLS --> BRG` to `FL --> BRG`.
- "What each file is for": rows for `tools.py` ("validate the site, build a RunRequest or load the record, delegate to `backends.get(site)`"), `runs.py` (the §5.2 sequence in one sentence, "the only writer of `created` and `enqueue_failed`"), `backends/__init__.py` (`SITES`, `get`, `block_type`), `backends/fermilab.py` (what it carries, that `bridge` is reached only from here and `runs`... no: `bridge` is imported by `fermilab.py` and `publishing.py`), `backends/nersc.py` (drop "the site=nersc mirror of tools.py"), `records.py` (site + block, the refusal).
- "The rules that shaped it": add **One ritual.** (Run creation exists once; a site supplies eight hooks) and amend **One import point** to say `bridge.py` is imported by `backends/fermilab.py` and `publishing.py` only.

- [ ] **Step 3: `docs/tools.md`**

- The record description: `site`, and the block under `"fermilab"` (`prodtools`, `campaign_id`, `tarball`, `ticks`) or `"nersc"` (`run_dir`, `cnf`, `walltime_s`, `config`, `jobs`).
- `beamline_status` result: `{"record", "site", "status"}`; `status` is `campaign_status` output or `null` on Fermilab, `{"jobs", "outputs", "beamfiles"}` on NERSC.
- `run_beamline`: `walltime_s` default `null` (NERSC applies 172800).
- A short "Records written before 0.5.0" note with the error text and the remedy (move the run dir aside).

- [ ] **Step 4: Run the suite and the docs greps**

Run: `env -u PYTHONPATH -u BEAMKIT_PRODTOOLS_ROOT .venv/bin/python -m pytest -q -p no:cacheprovider`
Expected: green.
Run: `grep -n "validate_site\|rec\.nersc\b\|\"campaign\": \|nersc_backend.run_beamline" docs/*.md src/beamkit/*.py src/beamkit/backends/*.py`
Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add src/beamkit/__init__.py pyproject.toml README.md docs/architecture.md docs/tools.md tests/
git commit -m "0.5.0: one backend seam; docs for runs.py, site blocks, status keys

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0138sWPyeSCDFbbn2cB9JBDy"
```

---

### Task 6: Live check (controller runs this; not a subagent task)

- Move aside `$BEAMKIT_HOME/runs` (old-shape records): `mv /exp/mu2e/data/users/oksuzian/beamkit/runs /exp/mu2e/data/users/oksuzian/beamkit/runs.pre-0.5.0`.
- Fermilab: `live_probe.py run_beamline '{"tag": "G4blSeam", "deck_ref": "e470313", "run_as": "self", "njobs": 2, "events_per_job": 10, "params": {"epsMax": "0.01"}}'` with `BEAMKIT_PRODTOOLS_ROOT` at the prodtools checkout and `BEAMKIT_PRODTOOLS_DIR` shipping it; then `beamline_status`, `beamline_outputs` once the cluster drains, `make_beamfile` `bm`.
- NERSC: same tag with `site="nersc"`, then `beamline_status` until complete, `make_beamfile` with `site="nersc"`, `fetch_outputs` (sfapi transport).
- Record both run ids and outcomes in memory `project_beamkit.md`.

---

## Self-review

**Spec coverage.** §5.1 RunRequest → T3 S3. §5.2 all 13 steps → T3 S3 (`create`); the main-input check between pin and allocate is the one addition beyond the spec's list, justified by its "refused before the probe" rule. §6 table: tool-side seven → T2, creation eight → T3, `Block` → T1, `available` → T2, `beamline_status` keys → T2 S5. §7.1–7.3 → T1 (`LEGACY_KEYS`, message, late import, `list_runs` propagation). §8 → T3 S6 `taken`. §9 → T2 S5, T3 S7. §10: `test_runs` T3 S1, `test_backends_interface` T2 S1 + T3 S8, `test_tools` migration T4, records tests T1 S1, no-import test T1 S1, live check T6. §11 docs → T5. §12 version → T5. §13 risk 2 → T4 S4. Rulings R1–R8 all reflected.

**Placeholder scan.** Docstrings marked `<docstring unchanged>` refer to text present in the file the implementer edits; every other code block is complete.

**Type consistency.** `backend.taken(ident, tag)` returns a callable of `cnf_name` (T3 S3, S5, S6, S8). `retryable(rec)` takes a record; `runs.create`'s local `retryable(run_id, runs_dir)` wraps it (T3 S3) and `claim_run_dir` keeps its `(run_id, runs_dir)` contract from the 2026-09-17 simplify commit. `make_beamfile(rec, *, flavor, run_as, plane, cuts, label, publish, location, confirm)` in T2 S1, S3, S4, S5. `Block` field names in T1 S4 match every `rec.block.*` in T1 S6, T2, T3. `status` returns `None` on Fermilab without a campaign (T2 S3) and the test `out["status"] is None` in T2 S6 relies on it.
