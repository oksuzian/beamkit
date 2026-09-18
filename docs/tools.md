# Tools, names, records, privilege

## Tools

| tool | does | prodtools calls |
|---|---|---|
| `run_beamline(tag, deck_ref, run_as, params=None, events_per_job=1000, njobs=1, main_input="Mu2E.in", outloc="scratch", dsconf=None, slice_size=None, submit=True, confirm=False, deck_dir=None, site="fermilab", walltime_s=None)` | materialize deck, allocate dsconf, write `entry.json` + `run.json`, create the campaign, and (if `submit`) fire the first tick, which submits the whole run when `njobs <= 10000` (`site="fermilab"`); or build the cnf locally and submit one Slurm job per `procs_per_node` indices through the IRI API (`site="nersc"`) | `push_cnf`, then `run_submissions(campaign_id=...)` (fermilab only) |
| `make_recoveries(run_id, run_as, confirm=False)` | one prodtools tick: verify finished jobs, resubmit the missing indices, and submit any slice of this run not yet submitted (`njobs > 10000` only). Nothing advances on its own; a run with no failures never needs this call. Scoped to the campaign while it is `active`; once prodtools marks it `complete` (every slice submitted, rows still verifying) the **bare tick** runs instead, and the result says so. **Ledger-wide:** the verify/recovery pass always covers every active campaign in the caller's ledger, and the bare tick's top-up does too, so under `run_as="mu2epro"` it advances production campaigns. Fermilab only; refused on a NERSC run ("no recovery on nersc"). | `list_campaigns`, then `run_submissions(campaign_id=...)` or `run_submissions()` |
| `beamline_status(run_id)` | the record plus the site's live view: `{"record", "site", "status"}`. `status` is prodtools' `campaign_status` on `site="fermilab"` (`None` before a campaign exists), or `{"jobs", "outputs", "beamfiles"}` — Slurm job states, CFS output counts, beam-file jobs — on `site="nersc"`, which also reconciles and saves the record as a side effect | `campaign_status` (fermilab only) |
| `list_beamline_runs(state=None)` | run records under the caller's beamkit dir, newest first | — |
| `beamline_outputs(run_id)` | files of `nts.<owner>.<desc>.<dsconf>.root` with sizes and dCache paths | `utils.samweb_wrapper.file_sizes_in_dataset`, `utils.file_resolver.dataset_dir`, `utils.job_common.Mu2eName` |
| `fetch_outputs(run_id, dest, kind="nts")` | NERSC runs only: copy the nts files (`kind="nts"`) or the complete beam files (`kind="beamfiles"`) from CFS into the local directory `dest` through the API, at most 5 MB per file; a run with one larger file is refused whole (Globus or scp for those); files already in `dest` with the CFS size are not fetched again | — |
| `make_beamfile(run_id, flavor, run_as, plane="Z3712", cuts=None, publish=False, location=None, confirm=False, label=None, site="fermilab")` | build a BLTrackFile beam file from a run's ntuples with a preset or caller-supplied cut table; `label` names the files and defaults to the flavor; optionally publish it to SAM with the ntuples as parents | `utils.samweb_wrapper.file_sizes_in_dataset` / `utils.file_resolver.dataset_dir` (inputs); `push_file` when `publish=True` |
| `get_server_info()` | beamkit version, prodtools root and commit, venv python, deck cache dir, records dir, beamfiles dir | — |

`run_beamline` also takes `deck_url` (default the `Mu2e/G4BeamlineScripts`
repo) to point at a different deck repository.

`walltime_s` defaults to `None` in the tool's own schema; each backend fills
its own meaning and refuses the other's if set. `site="fermilab"` refuses it
outright (prodtools sizes the jobs from `slice_size`); `site="nersc"` fills
`None` with `WALLTIME_DEFAULT` (172800 s, 48 hours) and refuses `slice_size`
in the same way (a NERSC run is sliced by `procs_per_node` from
`nersc.toml`, not by the caller).

## Naming

- `desc` = `tag`, the caller-supplied label (e.g. `MuBeam`, `G4blSmoke`),
  validated as a Mu2e description token (`[A-Za-z0-9]+`).
- `dsconf` = the first 7 hex characters of the pinned deck commit sha,
  suffixed `-NNN` only on a SAM collision — the unsuffixed name is
  always tried first, and a cnf name is never reused once taken.
- `run_id` = `<desc>.<dsconf>` — unique by construction.
- Output dataset: `nts.<owner>.<desc>.<dsconf>.root`, `owner` following
  `run_as` (`$USER` for self, `mu2e` for mu2epro).

## Records

Everything lives under `BEAMKIT_HOME` (default
`/exp/mu2e/data/users/$USER/beamkit`):

- `decks/<sha12>/` — a pinned `G4BeamlineScripts` checkout, materialized
  once and reused by any run naming the same sha.
- `runs/<run_id>/entry.json` — the one-entry JSON handed to
  prodtools' `push_cnf` (`site="fermilab"` only).
- `runs/<run_id>/run.json` — the run record: `tag`, `dsconf`, `owner`,
  `run_as`, `site`, deck pin, `params`, `njobs`, `outloc`, `slice_size`,
  `state`, `datasets` (the resolved `nts.<owner>.<desc>.<dsconf>.root`),
  `beamfiles`, and one typed block, keyed by `site`, never both:
  - `"fermilab": {prodtools, campaign_id, tarball, ticks}` — `prodtools`
    is the root/commit/dev_dir provenance of the checkout that shipped
    the cnf; `ticks` is one entry per prodtools tick (`run_submissions`)
    this run has seen: `when`, `rc`, `needs_attention`, a short summary.
  - `"nersc": {run_dir, cnf, walltime_s, config, jobs}` — `run_dir` and
    `cnf` are CFS paths, `config` is the `nersc.toml` snapshot the run
    was created under, `jobs` is the Slurm submissions.
- `beamfiles/` — beam files built by `make_beamfile`
  (`<run_id>.<label>.txt` + `.json` sidecar; the label defaults to the
  flavor).

**Records written before 0.5.0.** The block shape above replaced Fermilab
fields on every record and an untyped `nersc` dict. Loading an older
`run.json` (a legacy top-level key, or a `nersc` block without
`site == "nersc"`) is refused, not converted:

```
run.json was written by beamkit <version> (before 0.5.0) and is not
readable by this version; move the run dir aside
```

`list_beamline_runs` hits the same refusal on the first such file in
`runs/`, so an old run dir either moves aside or the listing itself
fails — no version is silently skipped.

Run states: `created` (campaign exists, nothing submitted),
`submitted`, `needs_attention` (the last tick returned prodtools'
rc=2: held rows or exhausted recoveries; a clean tick returns the run
to `submitted`), and `enqueue_failed` (the push never reached
prodtools; the run dir is retried in place). A push that raised after
the cnf reached SAM and the campaign was created adopts that campaign
into the record, so `make_recoveries` submits it instead of a retry
burning the next dsconf.

## Privilege

`run_as` is required on every mutating tool and passed through
unchanged: beamkit adds no gate of its own and removes none. Everything
`run_as` implies — the owner in every name, which ledger, whether
`confirm` is needed, the default publish location, whether a dev
prodtools checkout (`BEAMKIT_PRODTOOLS_DIR`) may ship to the workers —
is decided in one module, `identity.py`.
`outloc="disk"` (`/mu2e/persistent/datasets`) needs
`run_as="mu2epro"`: no other account has `storage.modify` there, so a
self run would finish g4bl on every worker and then 403 in `pushOutput`.
It is refused up front.

`make_beamfile(publish=True)` additionally requires `run_as` to match
the run's own: the beam file is named from the record's owner and pushed
as `run_as`, so a mismatch would publish one identity's name under the
other account.

`run_as="self"` writes only your own scratch, datasets and ledger — no
prompt. `run_as="mu2epro"` writes production SAM and submits
production grid jobs; it is refused unless `confirm=true`, both here
and inside prodtools.

Add `mcp__beamkit__run_beamline`, `mcp__beamkit__make_recoveries` and
`mcp__beamkit__make_beamfile` to the PreToolUse hook that prompts on
`mcp__prodtools-write__*` with `run_as=mu2epro`. The in-tool confirm
gate does not depend on it.

## Recoveries are ledger-wide

Prodtools scopes only the top-up to the campaign — the verify/recovery
pass covers every active campaign in the caller's ledger, so under
`run_as="mu2epro"` it recovers production campaigns too.

## Beam files

`make_beamfile` turns one run's `NTuple/<plane>` (default `Z3712`)
crossings into a single g4bl `beam ascii` input. Two presets ship,
reproducing MakeSource.py (2013) exactly:

```python
FLAVORS = {
    "bm": {"keep_pdg": None,   "drop_pdg": [2112], "min_p_mev": {22: 1.0, 11: 10.0, -11: 10.0}},
    "ps": {"keep_pdg": [2112], "drop_pdg": [],     "min_p_mev": {}},
}
```

Any other `flavor` needs an explicit `cuts` dict:

```python
{"keep_pdg": list[int] | None,   # exclusive whitelist; None = all PDG ids
 "drop_pdg": list[int],          # blacklist; must be [] when keep_pdg is set
 "min_p_mev": dict[int, float]}  # per-PDG floor on |p| in MeV/c; rows below are dropped
```

`label` names the local files and the SAM artifact
(`etc.<owner>.<tag>Beam-<label>.<dsconf>.0.txt`) and defaults to the
flavor; a second beam file with the same cuts takes a new label, not a
new flavor. `plane` is the `NTuple/<plane>` name and is validated
before anything is read.

There is no completeness check: `pot = n_files * events_per_job` from
whatever nts files exist in SAM, and `missing_indices` records the
gap. `location` (`scratch`/`disk`/`tape`) defaults to `tape` for
`run_as="mu2epro"` and `scratch` for self, and is only used when
`publish=True`; a published file is never copied tape-to-disk
afterwards, so choose the location at publish time. Publish needs
prodtools `push_file` — without it `make_beamfile` works with
`publish=False` only, and says so.

## Not in v1

- Sweeps (list-valued params fanning out into N runs) and any
  surrogate-driven loop.
- Stage-2 resampling from a beam file (`run_beamline(...,
  beamfile=...)`): specified so the record schema and tool signature
  won't change later, but gated on deck and prodtools prerequisites
  that don't exist yet.
- `push_file` for anything beyond `make_beamfile(publish=True)`.
