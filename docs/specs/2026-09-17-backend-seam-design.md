# beamkit: one backend seam, Run creation owned once

Date: 2026-09-17. Status: approved for planning. Supersedes nothing;
refines the layer picture in `docs/architecture.md` and the NERSC design
of 2026-09-11 (`backends/nersc.py` keeps its behaviour, changes its
shape). Vocabulary: `CONTEXT.md` (Site, Backend, Run request, Site block
were added for this design).

## 1. Goal

Make the two sites two adapters of one interface, and make Run creation
one module. After this change:

- `tools.py` knows no site. Every tool loads the record (or takes the
  caller's `site`), asks `backends.get(site)` for the backend, and
  delegates. Refusals of the kind "not on this site" live in the backend
  that refuses.
- `runs.py` owns the creation ritual: validate, resolve identity, pin
  deck, allocate dsconf, claim run dir, save the record before any remote
  effect, enqueue, check, submit, annotate failure. It exists once and
  calls the backend at five named points.
- The run record carries one typed **site block** instead of Fermilab
  fields on every record and an untyped `nersc` dict.
- `tests/test_tools.py` runs against the fake prodtools server through
  the seam that already exists (`BEAMKIT_PRODTOOLS_ROOT`), not against
  monkeypatched module attributes. Ordering rules get their own tests on
  `runs.create` with a stub backend.

## 2. Non-goals

- The nine MCP tools keep their names and arguments. `make_beamfile` keeps
  its `site` argument (refused on mismatch, as today).
- NERSC `status` keeps writing the record (beam-file completion is
  recorded there). Separating read from reconcile is a later change.
- The beam-file sidecar is still assembled per site. Unifying it is the
  next candidate, easier once the seam exists.
- `iri.py` / `sfapi.py` are untouched.
- No converter for records written before this change.

## 3. Friction today

| Trace | Modules | Hand-offs |
| --- | --- | --- |
| `run_beamline` fermilab | 11 | ~22 from `tools.run_beamline` |
| `run_beamline` nersc | 14 | ~25 from `backends/nersc.run_beamline`, the same preamble again |

The two orchestrators re-spell the same ordered ritual with silent
divergence: NERSC has a dsconf-reuse branch Fermilab lacks; `outloc` is
validated three times; the retryable predicate exists twice; the
18-argument `RunRecord(...)` call site is copied. `site` is decided seven
times in `tools.py` in three idioms. `rec.nersc` has five keys string-
indexed in ~15 places and declared nowhere. `tests/test_tools.py` patches
twelve attributes to reach the ritual; NERSC tests patch `iri.UPLOAD_MAX`
and `nersc_templates.render_inner` to fail at step k. Nothing checks that
the hand-written prodtools stubs return what `bridge` returns.

## 4. Architecture after

```
server.py ── tools.py ── runs.py ──┐
               │                   │  backends.get(site)
               │                   ▼
               │          backends/fermilab.py    backends/nersc.py
               │              │  bridge.py            │  iri.py / sfapi.py
               │              ▼                       ▼
               │          prodtools MCP           Superfacility API
               ▼
       records.py  identity.py  naming.py  compose.py  decks.py  beamfile.py  publishing.py
```

`tools.py` and `runs.py` import `backends`; `backends/*` import the leaf
modules; no leaf imports `backends` or `tools`. `records.py` learns the
block types through one late import (§7.3).

## 5. `runs.py`

### 5.1 `RunRequest`

A frozen dataclass of every caller value of `run_beamline`, built by
`tools.run_beamline` and validated nowhere else:

```
tag, run_as, deck_ref, params, events_per_job, njobs, main_input, outloc,
dsconf, submit, confirm, deck_dir, deck_url, site,
slice_size: int | None      # fermilab only
walltime_s: int | None      # nersc only
```

`walltime_s` and `slice_size` default to `None` at the tool; each
backend fills its own default (`WALLTIME_DEFAULT`, `min(njobs,
SLICE_MAX)`) and refuses the other's when set (ruling R6).

### 5.2 `create(req) -> dict`

The one sequence, in this order. Each step names what it refuses so a
refused call burns no dsconf and writes no record.

1. `backend = backends.get(req.site)`.
2. `ident = backend.resolve_identity(req)` (§6).
3. `naming.validate_tag(req.tag)`.
4. `params = compose.validate_inputs(events_per_job, njobs, outloc, params)`.
5. `req = backend.validate(req, ident)` — the site refuses the other
   site's argument, checks its own, fills its defaults (slice size,
   walltime, outloc rule with identity).
6. `pin = decks.pin(...)`.
7. `dsconf = naming.allocate_dsconf(ident.owner, req.tag,
   naming.dsconf_base(pin.sha), backend.taken(ident, req.tag),
   explicit=req.dsconf)`.
8. `run_id = naming.run_id(req.tag, dsconf)`;
   `rdir = records.claim_run_dir(runs_dir, run_id, retryable)` where
   `retryable(run_id, runs_dir)` is `records.try_load` followed by
   `backend.retryable(rec)`.
9. `rec = RunRecord(... state="created", site=req.site,
   block=backend.new_block(req, ident, pin, run_id, dsconf))`;
   `records.save`.
10. `try: backend.enqueue(rec, req, ident, pin, rdir)` — creates the
    remote thing (Fermilab: entry.json, `push_cnf`, campaign and tarball
    into the block, `rec.njobs` from prodtools; NERSC: cnf, job.sh,
    inner.sh, remote layout). On any exception: `rec.state =
    "enqueue_failed"`, `rec.error = "<Type>: <msg>"`,
    `outcome = backend.after_failure(rec, ident)`, save, raise
    `BeamkitError(f"run {run_id}: enqueue failed ({e}){outcome}")`.
11. `records.save`. If `rec.njobs != req.njobs`: raise the existing
    "prodtools reports campaign … holds N jobs" error (site-neutral; NERSC
    never changes `njobs`).
12. `if req.submit: backend.submit(rec, ident, req.confirm)` — Fermilab
    `_tick_into`, NERSC `_submit_missing`. Each keeps its own failure
    annotation (they leave the run in `needs_attention` / `error` or
    `partially_submitted`, not `enqueue_failed`).
13. `return rec.to_dict()`.

`create` is the only writer of `state="created"` and `"enqueue_failed"`.

## 6. The backend interface

Two modules, `backends/fermilab.py` and `backends/nersc.py`, each
exporting these names. `backends/__init__.py` holds `SITES`,
`get(site)` (refuses an unknown site with the current message) and
nothing else.

| Function | Fermilab | NERSC |
| --- | --- | --- |
| `resolve_identity(req) -> Identity` | `identity.resolve(run_as, confirm)` | `identity.resolve(run_as, site="nersc", owner=cfg.owner)`; self only |
| `validate(req, ident) -> RunRequest` | refuses `walltime_s`; `slice_size` default `min(njobs, SLICE_MAX)` and range; `_outloc(outloc, ident)` | refuses `slice_size`; `outloc` must be `scratch`; `validate_walltime`; fills `WALLTIME_DEFAULT` |
| `taken(ident, tag) -> Callable[[str], bool]` | `bridge.cnf_exists` | run dir exists on CFS **and** no retryable local record explains it (§8) |
| `retryable(rec) -> bool` | `enqueue_failed` and `block.campaign_id is None` | `enqueue_failed` and `not block.jobs` |
| `new_block(req, ident, pin, run_id, dsconf) -> Block` | `prodtools=bridge.prodtools_info() + dev_dir` | `run_dir`, `cnf`, `walltime_s`, `jobs=[]`, `config` |
| `enqueue(rec, req, ident, pin, rdir) -> None` | entry.json, `push_cnf`, campaign/tarball/`njobs` | cnf, templates, `_remote_layout` |
| `after_failure(rec, ident) -> str` | today's `_after_failed_push` (SAM and ledger probe; may adopt a campaign) | `"; fix the cause and call again, the run dir is reused"` |
| `submit(rec, ident, confirm) -> None` | `_tick_into` scoped to the campaign | `_submit_missing` |
| `submit_run(rec, run_as) -> dict` | refuses: "the Fermilab path submits through make_recoveries" | today's body |
| `make_recoveries(rec, run_as, confirm) -> dict` | today's body (`_campaign`, `_tick_into`) | refuses: "no recovery on nersc" |
| `status(rec) -> dict \| None` | `bridge.campaign_status` or `None` when no campaign | today's body (writes the record) |
| `outputs(rec) -> dict` | `bridge.dataset_files` | today's body |
| `fetch_outputs(rec, dest, kind) -> dict` | refuses: outputs are in dCache | today's body |
| `make_beamfile(rec, ...) -> dict` | today's Fermilab body from `tools.py` | today's body |
| `Block` | dataclass (§7.1) | dataclass (§7.1) |
| `available() -> (bool, str)` | `bridge.availability()` | `nersc.toml` present and loads |

The signature is the interface. A new site is a module with these
fifteen names; `tests/test_backends_interface.py` asserts both modules
export exactly them (§10).

`tools.make_recoveries`, `tools.submit_run`, `tools.fetch_outputs`,
`tools.beamline_outputs`, `tools.beamline_status` become: load the
record, `backends.get(rec.site).<fn>(rec, ...)`. `beamline_status`
returns `{"record": rec.to_dict(), "site": rec.site, "status":
backend.status(rec)}` (ruling R3: symmetric keys replace
`campaign`/`nersc`).

## 7. The run record

### 7.1 Site blocks

```python
# backends/fermilab.py
@dataclass
class Block:
    prodtools: dict                 # root, commit, dev_dir
    campaign_id: int | None = None
    tarball: str | None = None
    ticks: list = field(default_factory=list)

# backends/nersc.py
@dataclass
class Block:
    run_dir: str
    cnf: str
    walltime_s: int
    config: dict
    jobs: list = field(default_factory=list)
```

`slice_size`, `outloc`, `njobs`, `datasets`, `beamfiles`, `error`,
`created`, `beamkit_version` stay top-level: both sites read them.

### 7.2 `RunRecord`

```python
run_id, tag, dsconf, owner, run_as, deck, params, events_per_job, njobs,
outloc, slice_size, state, site, block,
datasets, created, beamkit_version, beamfiles, error
```

`to_dict()` emits the block under the site's name:
`{"site": "nersc", "nersc": {...}}` / `{"site": "fermilab", "fermilab":
{...}}`, and never both. That is the `run.json` shape and the public
`record` shape in every tool result.

### 7.3 Load and refusal

`from_dict` reads `site`, takes `d[site]`, and builds the block with
`backends.block_type(site)` — a function in `backends/__init__.py`
imported inside `from_dict` (no import-time cycle; `records` stays leaf
for everything else). Any of the old top-level keys (`campaign_id`,
`tarball`, `ticks`, `prodtools`) or a `nersc` key without `site ==
"nersc"` raises

```
RecordError: runs/<run_id>/run.json was written by beamkit <version>
(before 0.5.0) and is not readable by this version; move the run dir
aside
```

`list_runs` raises the same on the first such file: the directory is
either migrated by hand or not listed. No silent skip (no-fallbacks
rule).

## 8. Dsconf allocation, one rule

`allocate_dsconf(owner, tag, base, taken, explicit)` runs the same for
both sites. The NERSC branch that reused the base dsconf when the prior
attempt was retryable goes away; its reason (the probe would see our own
leftover directory) moves into the NERSC `taken`:

```
taken(cnf_name):
    dsconf = naming.dsconf_of(cnf_name)          # new one-liner in naming
    run_id = naming.run_id(tag, dsconf)
    if backend.retryable(records.try_load(run_id, runs_dir)): return False
    return client.exists(run_dir(cfg, run_id))
```

Fermilab keeps probing SAM: a cnf that reached SAM is a real collision.
`test_backends_nersc_run.py::test_retry_after_layout_failure_reuses_the_dsconf`
(or its current name) keeps passing with no change in observable
behaviour.

## 9. `tools.py` after

Validate the `site` argument where a tool takes one, build the request or
load the record, dispatch. Expected size: about a third of today. The
helpers `_outloc`, `_slice_size`, `_is_retryable_run_dir`,
`_after_failed_push`, `_tick_into`, `_campaign`, `_dataset`, `_summary`
move to `backends/fermilab.py` unchanged in body. `get_server_info`
reports both backends' `available()`.

## 10. Testing

- `tests/test_runs.py` (new): a stub backend module (a `SimpleNamespace`
  with the fifteen names, most recording calls) drives `runs.create`.
  Tests: refused input burns no dsconf and writes no record; the record
  exists on disk before `enqueue` is called; `enqueue` failure leaves
  `enqueue_failed` with the backend's outcome text appended; `njobs`
  mismatch raises after save and before `submit`; `submit=False` never
  calls `submit`; a retryable prior attempt is overwritten, a
  non-retryable one refused.
- `tests/test_backends_interface.py` (new): both backends export exactly
  the interface names, with matching signatures (`inspect.signature`
  parameter names).
- `tests/test_tools.py`: the `fake_bridge` fixture is replaced by
  `fake_prodtools_root` (already in `conftest.py`). Failure injection
  moves into `tests/fake_prodtools_mcp.py` knobs: `FAKE_PRODTOOLS_FAIL`
  already exists; add `FAKE_PRODTOOLS_CNF_EXISTS` (names reported taken)
  and `FAKE_PRODTOOLS_CAMPAIGNS` (ledger rows) so the SAM-probe tests of
  `after_failure` run through the real bridge. `tools.decks.materialize`
  and `identity._username` patches stay (git and getpass are not
  prodtools).
- `tests/test_backends_nersc_*.py`: unchanged in intent; call sites move
  from `nersc.run_beamline(...)` to `tools.run_beamline(..., site="nersc")`
  or `runs.create(req)` where they tested the ritual, and to the backend
  functions where they tested the site.
- `tests/test_records.py`: round trip of both block types; refusal of the
  old shape with the message above.
- `tests/test_bridge_contract.py`: unchanged.
- Live check before tagging: one Fermilab run (`run_as="self"`, 2 jobs)
  and one NERSC run through `live_probe.py`, plus `make_beamfile` on each.

## 11. Docs

- `docs/architecture.md`: layer diagram and file table gain `runs.py`,
  `backends/fermilab.py`, the block types; the "What each file is for"
  rows for `tools.py` and `backends/__init__.py` shrink accordingly.
- `docs/tools.md`: record shape (site blocks), `beamline_status` result
  keys, the old-record refusal.
- `CONTEXT.md`: done (Site, Backend, Run request, Site block).
- `README.md`: version line.

## 12. Sequencing and version

One branch off `v1`, tasks in this order: backend interface + Fermilab
module (pure move) → site blocks in records (with refusal) → `runs.py`
and the NERSC probe change → `tools.py` collapse → tests migration →
docs. Version becomes **0.5.0** (record shape and `beamline_status` keys
change). `v0.4.0` is not tagged; the uvx line in prodtools' `.mcp.json`
pins `v0.5.0` instead (ruling R5).

## 13. Risks

- **Old records on disk.** Every existing `runs/<id>/run.json` under the
  user's `BEAMKIT_HOME` becomes unreadable. Mitigation: the error names
  the file and the remedy; the runs are test runs.
- **`after_failure` through the real fake server.** The three SAM-probe
  outcomes need knobs in the fake; if a knob proves awkward, the
  Fermilab backend's `after_failure` may be tested directly with
  patched `bridge` calls in `tests/test_backends_fermilab.py`. That is
  the only place a `bridge` monkeypatch remains acceptable.
- **`records` importing `backends` lazily.** The late import is one
  function; a test asserts `import beamkit.records` does not import
  `beamkit.backends`.

## 14. Rulings

- R1: backends are modules, not classes; no injection. The prodtools seam
  is `BEAMKIT_PRODTOOLS_ROOT`, the NERSC seam is the fake session.
- R2: site blocks are symmetric dataclasses owned by the backends; the
  record stores one, keyed by site in JSON.
- R3: `beamline_status` returns `{"record", "site", "status"}`.
- R4: old-shape records are refused, not converted.
- R5: version 0.5.0; `v0.4.0` never tagged.
- R6: `walltime_s` becomes `Optional[int] = None` in the tool signature
  (the schema default changes from 172800 to null; the value applied is
  the same). `tools.py` then carries no NERSC constant.
- R7: NERSC `status` keeps writing the record in this change.
- R8: the NERSC dsconf-reuse branch is replaced by the probe rule in §8.
