# beamkit NERSC backend: g4bl runs on Perlmutter through the IRI Facility API

Status: design, approved in conversation 2026-09-11, awaiting review of this
document.

## 1. Problem

beamkit runs G4beamline production at Fermilab by delegating everything
below the deck to prodtools: cnf registration in SAM, the submission
ledger, jobsub, recovery, pushOutput. On 2026-09-10 and 2026-09-11 two
probes proved that both prodtools payloads run unchanged on NERSC
Perlmutter when launched through the IRI Facility API
(`https://api.iri.nersc.gov/api/v2`): a Mu2e art job (job 58177878) and
the prodtools g4bl worker recipe on beamkit's pinned deck e470313 (job
58197742, exit 0, warning summary identical to the Fermilab run of the
same deck). See `wiki/pages/nersc-iri-feasibility.md` in prodtools for
the probe record and the API facts.

The requested capability: submit a beamkit run to Perlmutter from a
laptop, with no Fermilab service in the loop. Not every beamkit user has
a Fermilab account; SAM and dCache must be optional.

## 2. Decisions

| decision | choice | rejected |
|---|---|---|
| where the NERSC path lives | beamkit, as a second backend behind one seam | prodtools backend (needs Fermilab credentials and cvmfs at submit time); a separate `beamkit.nersc` tool set (two record shapes, drift); a bare IRI script (not a product) |
| outputs | stay on NERSC CFS; harvest to dCache and SAM is a separate, optional, later step | per-job pushOutput (needs a Fermilab token on every node); NERSC-only with no path back |
| job shape | one Slurm job per slice of `procs_per_node` indices, all on one node; index = slice offset + `SLURM_PROCID` | shared QOS with one job per index (per-user queue caps, hundreds of jobs); multi-node slices (same code later if wanted) |
| recovery | none; status reports missing indices, a new run replaces a short one | prodtools-style recovery pass |
| beam file | built by a second Slurm job on Perlmutter from the nts files on CFS | download nts through the API (5 MB per-file cap, breaks past a few hundred files); out of scope |
| node script | beamkit's own two-file template, g4bl lines copied from prodtools' `_g4bl_script` and pinned by the contract test | prodtools runner from the cvmfs release (expects the jobsub environment: `$PROCESS`, `$CONDOR_DIR_INPUT`, `$JSB_TMP`) |

The node-script decision reverses the 2026-09-03 rule "worker code
reaches nodes only via the prodtools cvmfs release" for the NERSC path
only. The Fermilab path is untouched.

## 3. Facts the design rests on

- Perlmutter mounts cvmfs on compute nodes, including
  `mu2e.opensciencegrid.org`, `oasis.opensciencegrid.org` and
  `singularity.opensciencegrid.org`. g4beamline 3.08b loads with
  `spack load --sh g4beamline` after `setupmu2e-art.sh`. The
  fnal-wn-el9 image is an unpacked sandbox on cvmfs and apptainer is on
  cvmfs too, so no image pull is needed.
- The IRI adapter's own container block is unusable for this: more than
  one `volume_mounts` entry fails with `Error: parsing reference " "`
  (exit 125). Jobs run native on SLES and enter the container
  themselves.
- Upload and download are capped at 5 242 880 bytes per file. The
  e470313 deck tarball is 113 KB. An nts file from 1000 events is about
  1.3 MB. A beam file for 1e7 protons is about 2.6 GB.
- Auth that works unattended is a NERSC Superfacility API client
  (client id plus private key, client-credentials grant at
  `https://oidc.nersc.gov/c2id/token`, 600 s access tokens). A red-level
  client, needed for job submission, is pinned to at most two source IPs
  and lives 48 h until NERSC's security review extends it to 30 d.
  Globus tokens are rejected by the v2 API. The facility-neutral AmSC
  keycard is not obtainable outside Genesis Mission projects and NERSC
  has it disabled.
- A Perlmutter CPU node has 128 cores and 512 GB. g4bl is single
  threaded, about 1 GB RSS, about 1.4 s per event on the e470313 deck.
  NERSC bills node-hours.
- Filesystem operations in the API are asynchronous tasks polled by
  `task_uri`. `POST /filesystem/mkdir/{id}`, `ls`, `upload`, `download`
  all follow that shape. Uploading into a directory that does not exist
  fails with `Error: 400: Error downloading: No such file`.
- The API rejects an `Idempotency-Key` header with 501 unless the
  facility configures an idempotency store; NERSC has not.

## 4. Laptop side

### 4.1 Install

`pip install beamkit` on any host with Python 3.10 or newer and git.
prodtools is imported only inside the fermilab backend module, so a host
without prodtools can run every NERSC tool. `get_server_info` reports
which backends are available and why one is not.

The MCP server is the existing one (`server.py`, FastMCP over stdio,
the tools.py functions registered as they are). Today it launches only
through `scripts/start_mcp.sh`, which requires `BEAMKIT_PRODTOOLS_ROOT`
and borrows prodtools' venv for the `mcp` package because
`pyproject.toml` declares no dependencies. For the laptop:

- `pyproject.toml` gains `dependencies = ["mcp", "requests", "authlib",
  "tomli; python_version < '3.11'"]`.
- A console script `beamkit-mcp = beamkit.server:main`.
- Registration on the laptop, in Claude Code's `.mcp.json` or Claude
  Desktop's config:

  ```json
  {"mcpServers": {"beamkit": {"command": "beamkit-mcp",
                               "env": {"BEAMKIT_HOME": "/Users/you/.beamkit"}}}}
  ```

`start_mcp.sh` stays as the Fermilab launcher, where the fermilab
backend needs prodtools' venv on the path.

### 4.2 Home directory

`paths.home()` resolves in this order and records the answer in every
run record:

1. `BEAMKIT_HOME` when set.
2. `/exp/mu2e/data/users/<user>/beamkit` when `/exp/mu2e/data/users`
   exists on the host (the existing Fermilab default, kept so existing
   runs stay where they are).
3. `~/.beamkit`.

Decks, runs and beamfiles live under it as today.

### 4.3 NERSC configuration

One file, `$BEAMKIT_HOME/nersc.toml`. A missing file or a missing key is
refused with a message that lists every required key. No key has a
silent default except the two cvmfs paths and `procs_per_node`.

```toml
api            = "https://api.iri.nersc.gov/api/v2"
sfapi_dir      = "~/.sfapi"      # client_id, and priv_key.pem or priv_key.jwk, mode 400
account        = "m4599"         # Slurm allocation to charge
base_dir       = "/global/cfs/cdirs/m4599/Users/oksuzian/beamkit"
qos            = "regular"       # "debug" for tests; whole-node slices
# shared_qos = "shared"          # partial slices (<= shared_max_procs), per-core charge
# shared_max_procs = 64
procs_per_node = 128
image          = "/cvmfs/singularity.opensciencegrid.org/fermilab/fnal-wn-el9:latest"
apptainer      = "/cvmfs/oasis.opensciencegrid.org/mis/apptainer/current/bin/apptainer"
```

Compute and filesystem resource ids are looked up by name (`"compute"`,
and `"cfs"` for the filesystem) on each call with one request each.
They are never stored. The access token is minted per tool call and
held in memory only. A private key file that is group or world
readable is refused.

### 4.4 Identity

`site="nersc"` accepts `run_as="self"` only; `run_as="mu2epro"` at NERSC
is refused before any network call. The owner field in every file name
is the `owner` key of `nersc.toml`, so a user with no Mu2e account gets
names in the Mu2e grammar under their own name. The API's whoami
endpoint, called with a client-credential token, reports the numeric
NERSC account id (for example `{"username": "105241"}`), not the login,
so it cannot supply the owner; nothing in beamkit calls it today except
the live smoke test.
`Identity` gains a `site` field; `identity.resolve` takes `site` and
applies these rules. The record stores both.

## 5. `run_beamline` on the NERSC backend

Signature grows by one keyword: `site: str = "fermilab"`. Everything
before the backend dispatch is shared with the Fermilab path.

1. Load `nersc.toml`, resolve identity, validate inputs
   (`compose.validate_inputs`, `naming.validate_tag`, slice size). Every
   refusal happens here.
2. Pin the deck into `decks/<sha12>/` (unchanged code; needs git on the
   laptop).
3. Allocate dsconf: base is the deck sha7; the collision probe is an
   API `ls` of `base_dir/runs/<tag>.<sha7>`; `-NNN` on a hit, same rule
   as the SAM probe today.
4. Build the cnf tarball locally: `work/` is the deck without `.git`,
   `.svn`, `.hg`; `jobpars.json` carries the same keys prodtools writes
   (`runner`, `desc`, `dsconf`, `main_input`, `events_per_job`, `njobs`,
   `owner`, `g4bl_params`, `tbs`). Named
   `cnf.<owner>.<tag>.<dsconf>.0.tar` so a later harvest can declare it
   as the parent unchanged. Refused above 5 MB.
5. Create the remote layout:

   ```
   base_dir/runs/<run_id>/cnf.<owner>.<tag>.<dsconf>.0.tar
   base_dir/runs/<run_id>/job.sh
   base_dir/runs/<run_id>/inner.sh
   base_dir/runs/<run_id>/out/        nts and per-index logs
   base_dir/runs/<run_id>/slurm/      stdout and stderr per Slurm job
   base_dir/runs/<run_id>/beamfiles/  written by make_beamfile
   ```

   The run record is written locally (state `created`) before the first
   remote write, as today.
6. Submit `ceil(njobs / procs_per_node)` Slurm jobs, in order. Job k
   carries `node_count=1`,
   `process_count = processes_per_node = min(procs_per_node, njobs - k*procs_per_node)`,
   `cpu_cores_per_process=1`, environment `BK_OFFSET=k*procs_per_node`,
   `executable=/bin/bash`, `arguments=[job.sh]`, stdout and stderr under
   `slurm/`, `account` from config. A full slice takes a whole node:
   `exclusive_node_use=true`, `queue_name=qos`. A slice smaller than a
   node with at most `shared_max_procs` processes (default 64, half a
   Perlmutter node, the shared queue's cap) goes to `queue_name=shared_qos`
   (default `shared`) with `exclusive_node_use=false`, charged per core;
   a partial slice above the cap takes a whole node like a full one.
   `custom_attributes {"constraint": "cpu", "licenses": "cvmfs", "module": "cvmfs"}`,
   and `duration = min(walltime_s, events_per_job * 2 + 900)` seconds where
   `walltime_s` is a new optional tool parameter defaulting to 172800.
   No Idempotency-Key header is sent: NERSC's deployment answers 501 "no
   idempotency store is configured" when one is present; the run
   record's job list is what prevents a duplicate submit.
7. The record gains `site`, keeps `slice_size` at the top level (the
   procs_per_node the run was created with, fixed for its lifetime and
   what `submit_run` slices by — not the live `nersc.toml`, which may
   change before a later submit), and gains a `nersc` block:

   ```json
   "site": "nersc",
   "slice_size": 128,
   "nersc": {
     "run_dir": ".../runs/<run_id>",
     "cnf": "cnf.<owner>.<tag>.<dsconf>.0.tar",
     "walltime_s": 172800,
     "jobs": [{"slurm_id": "58197742", "offset": 0, "count": 128, "submitted": "..."}],
     "config": {"base_dir": "...", "account": "m4599", "qos": "regular",
                "owner": "u", "procs_per_node": 128, "...": "every nersc.toml value at create time"}
   }
   ```

   `campaign_id` and `tarball` stay null for a NERSC run. Records
   without a `site` key read as `site="fermilab"`.

`submit=False` stops after step 5 and leaves the run in state `created`
with the remote layout in place; `make_recoveries` does not apply, so a
later submit is a new tool call `submit_run(run_id, run_as)` on the
NERSC backend only, which performs step 6 for a `created` run.

## 6. What runs on the node

Two files rendered by beamkit from templates and uploaded next to the
cnf. Rendering fills in run dir, run id, owner, tag, dsconf,
events_per_job, main_input, params (sorted `key=value`, shell quoted),
image and apptainer paths. Nothing is looked up at run time.

`job.sh`, native SLES, executed once per srun task:

```bash
#!/bin/bash
IDX=$((BK_OFFSET + SLURM_PROCID))
exec "{apptainer}" exec -B /cvmfs -B /global/cfs --env IDX=$IDX "{image}" /bin/bash "{run_dir}/inner.sh"
```

`inner.sh`, inside EL9. The per-index log is opened first so 128 tasks
never interleave, and it lands whether or not g4bl succeeds:

```bash
#!/bin/bash
SEQ=$(printf %08d "$IDX")
exec > "{run_dir}/out/log.{owner}.{tag}.{dsconf}.$SEQ.log" 2>&1
set -x
W=/tmp/bk.{run_id}.$IDX; mkdir -p "$W"; cd "$W" || exit 2
tar xf "{run_dir}"/cnf.*.tar || exit 2
unset SPACK_ENV PYTHONHOME PYTHONPATH PYTHONNOUSERSITE
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh > /dev/null 2>&1
eval "$(spack load --sh g4beamline)" || exit 4
cd work
g4bl {main_input} viewer=none First_Event=$((IDX*{events_per_job}+1)) Num_Events={events_per_job} \
     histoFile="$W/nts.{owner}.{tag}.{dsconf}.$SEQ.root"{params}
rc=$?
[ $rc -eq 0 ] && mv "$W"/nts.*.root "{run_dir}/out/"
sha256sum "{run_dir}/out/nts.{owner}.{tag}.{dsconf}.$SEQ.root" 2>/dev/null
echo "BK_DONE idx=$IDX rc=$rc"
rm -rf "$W"
exit $rc
```

The four lines from `unset` to `g4bl` are prodtools'
`utils/runmu2e.py::_g4bl_script` verbatim. `tests/test_bridge_contract.py`
gains one assertion: with `BEAMKIT_PRODTOOLS_ROOT` set, beamkit's rendered
template and prodtools' function agree on those lines for the same
inputs. The work directory is node-local tmpfs (353 GB); a deck copy and
one output per process fit with room to spare. Output moves to CFS only
on exit 0.

## 7. `make_beamfile` on the NERSC backend

Signature grows by `site: str = "fermilab"`. For a NERSC run:

1. Resolve cuts, label and plane as today.
2. Render `beamfile_job.py`, a single self-contained script built from
   `beamfile.py` and `_read_plane.py` (pure Python, uproot, numpy) with
   the cuts table, plane, label and run dir substituted. Render
   `beamfile.sh`, which enters the same container and runs the script
   with `/cvmfs/mu2e.opensciencegrid.org/env/ana/2.8.0/bin/python`.
   Upload both to `beamfiles/`.
3. Submit one Slurm job, one process, which the §5 rule places in
   `shared_qos` non-exclusive, duration `600 + 2 * n_nts` seconds capped
   at 4 h.
4. The job reads `out/nts.*.root`, writes
   `beamfiles/etc.<owner>.<tag>Beam-<label>.<dsconf>.0.txt` and a
   sidecar `beamfiles/etc.<owner>.<tag>Beam-<label>.<dsconf>.0.json`
   with cuts, rows in and out, pot, and the input file list with sha256.
5. The record's `beamfiles` list gains an entry of today's shape plus
   `slurm_id` and `state`. `beamline_status` reports the job; when it is
   terminal and the sidecar exists, the entry is `complete` and the
   sidecar's numbers are copied into the record.

No nts file is ever downloaded. `publish=True` on a NERSC run is
refused: publishing is part of harvest.

## 8. Status and outputs

`beamline_status(run_id)` on a NERSC run returns:

- each Slurm job with its IRI state (`new`, `queued`, `held`, `active`,
  `completed`, `failed`, `canceled`), exit code, elapsed, node;
- counts from one `ls` of `out/`: `expected`, `nts`, `logs`, and
  `missing` (indices with no nts file, first 50) ;
- beam-file jobs, as in section 7.

The run state is `complete` when every Slurm job is terminal and `nts`
equals `njobs`; `submitted` while any job is not terminal; `short` when
all are terminal and `nts` is below `njobs`. Nothing resubmits.
`make_recoveries` on a NERSC run is refused with the message "no
recovery on nersc; submit a new run". `beamline_outputs` lists CFS paths
for nts and beam files. `list_beamline_runs` gains a `site` column.

## 9. Harvest (interface only, not built in v1)

`harvest(run_id, run_as, confirm)` on the fermilab backend against a
NERSC run: copy `out/` and the cnf tarball from CFS to dCache from a
Fermilab host, declare the cnf, then declare each nts file with the cnf
as parent through prodtools `push_file`. Requires Fermilab credentials
and prodtools; a laptop without them never sees the tool succeed. v1
guarantees what harvest needs: Mu2e file grammar on CFS, one directory
per run, the cnf next to its outputs, sha256 in every log. The transfer
mechanism (Globus, gfal from a DTN, or scp) is chosen when harvest is
built.

## 10. Backend seam

```
beamkit/backends/__init__.py    Backend protocol, get_backend(site)
beamkit/backends/fermilab.py    today's bridge.py calls, unchanged behaviour
beamkit/backends/nersc.py       IRI client + orchestration for sections 5-8
beamkit/iri.py                  thin HTTP client: token, resources, mkdir, ls,
                                upload, download, submit, status, task polling
beamkit/nersc_config.py         nersc.toml loading and validation
beamkit/templates/              job.sh, inner.sh, beamfile.sh, beamfile_job.py
```

The protocol is the set of operations tools.py already performs through
`bridge.py`, plus `submit_run` and `status_run`:

```python
class Backend(Protocol):
    name: str
    def name_taken(self, ident, tag, dsconf) -> bool: ...
    def create_run(self, ident, rec, entry_path, deck_dir) -> RunRecord: ...
    def submit_run(self, ident, rec) -> RunRecord: ...
    def status_run(self, rec) -> dict: ...
    def outputs(self, rec) -> list[dict]: ...
    def make_beamfile(self, ident, rec, spec) -> RunRecord: ...
    def recover(self, ident, rec) -> RunRecord: ...      # nersc raises
```

`bridge.py` stays as the only prodtools importer and becomes the body of
the fermilab backend. tools.py dispatches on `site` once, at the top of
each tool, and never tests the string again. `iri.py` knows nothing
about beamkit: it takes a config and returns parsed JSON or raises
`IriError(status, detail)`.

## 11. Errors

All loud, none retried, every message names the next action.

- Missing or partial `nersc.toml`: the list of required keys.
- Private key readable by others: refused, with the chmod to run.
- 401 from the token endpoint or the API: names the two usual causes,
  source IP not in the client's allow list, and the 48 h client
  lifetime.
- Upload over 5 MB, run directory already present on CFS: refused
  before any remote write.
- Slurm job k fails to submit after jobs 0 to k-1 succeeded: record
  saved with the jobs that exist, state `partially_submitted`, error
  names k and the API detail. `submit_run` on such a run submits only
  the missing jobs.
- An IRI task ending `failed` or `canceled`, or not finishing within 10
  minutes: `IriError` with the task id and the adapter's detail.
- `make_beamfile` with zero nts files: refused, with the counts.

## 12. Testing

- Unit, offline: a fake IRI server built from the probe's recorded
  responses (resources, mkdir, ls, upload, submit, status, download,
  task polling) behind `requests`; golden files for the three rendered
  scripts; index math at slice edges (njobs = 1, 128, 129, 10000);
  record migration (records without `site` read as `fermilab`);
  `nersc.toml` validation; the 5 MB refusal; partial-submit bookkeeping.
- Contract: the four g4bl lines against prodtools' `_g4bl_script` when
  `BEAMKIT_PRODTOOLS_ROOT` is set, same skip rule as today's contract
  test.
- Live, opt in with `BEAMKIT_NERSC_LIVE=1`: 2 indices of 10 events in
  the debug qos, wait, then a beam file; asserts exit 0, two nts files,
  two logs each ending in `BK_DONE`, a beam file and its sidecar. Runs
  from a host whose IP the client allows.

## 13. Non-goals (v1)

- Recovery of any kind.
- Harvest to dCache and SAM (section 9 fixes the interface only).
- Multi-node slices, GPU nodes, shared QOS.
- Stage-2 resampling on NERSC.
- Any use of the API's container block.
- Token caching on disk, client creation or renewal.

## 14. Open questions

None blocking. Two defaults to confirm during implementation:

1. `walltime_s` default of 172800 s (48 h) is the regular-QOS ceiling on
   Perlmutter CPU nodes at the time of writing; verify against
   `/compute/resources` before pinning it.
2. The beam-file job's ana 2.8.0 python path is the one beamkit already
   uses at Fermilab; confirm uproot imports inside the container on
   Perlmutter in the live test before relying on it.

**Confirmed 2026-09-12.** Live smoke `tests/test_nersc_live.py` passed
from mu2esrv01 against api.iri.nersc.gov in 10 min 12 s wall: run
`G4blLive.e470313-001` (the unsuffixed id was taken by an earlier
attempt's leftover directory), deck e470313, 2 indices of 10 events,
debug qos. Slurm job 58227570 (2 processes, one node): submitted
23:40:06 PT, started 23:43:13, ended 23:44:35, elapsed 1 min 22 s on
nid004326; both nts files (85 KB, 81 KB) and both index logs landed in
`out/`. Beam-file job 58227941: submitted 23:45:39, started 23:48:09,
elapsed 55 s on nid005525; `bm` beam file 14 rows from 94 read, 2
files, pot 20, no missing indices, sidecar copied into the record;
uproot imported fine under the cvmfs ana 2.8.0 python inside the
container (open question 2 closed). Three facts corrected along the
way, all recorded above: whoami returns the numeric account id, the
API's mkdir needs an existing parent, and the Idempotency-Key header is
refused with 501.

## 15. Second transport: Superfacility API v1.2 (added 2026-09-13)

On 2026-09-12 the IRI v2 adapter at NERSC returned 500 on every
compute/filesystem endpoint for more than fifteen hours while the legacy
Superfacility API (api.nersc.gov/api/v1.2) kept working with the same
client credential. `transport = "sfapi"` in `nersc.toml` selects
`beamkit.sfapi.SfapiClient`, which implements the same seven methods the
backend calls (`mkdir`, `ls`, `exists`, `upload`, `download`, `submit`,
`status`) with the same return shapes, so nothing above `make_client`
changes. Differences the transport hides:

- v1.2 reports most failures as HTTP 200 with `status: "ERROR"` and an
  error string; the client raises `IriError` on those.
- utilities paths keep their leading slash after the machine segment
  (`/utilities/upload/perlmutter//global/cfs/...`); a single slash is
  resolved relative to the service and answers "No such file".
- `mkdir` runs `utilities/command mkdir -p <path>` (v1.2 has no mkdir
  endpoint); it is the only shell command the transport runs.
- `submit` renders the PSI/J spec into an sbatch script (`-J -A -q -N -n
  --ntasks-per-node -c -t -o -e`, `--exclusive` when the spec says so,
  `-C` from `constraint`, `--export=NONE`, `cd`, `export BK_OFFSET`,
  `exec srun --export=ALL <executable> <arguments>`; the srun is what
  gives each of the process_count tasks its SLURM_PROCID, as the IRI
  adapter does; a bare exec ran job.sh once and index 1 never started). `licenses` and `module` are not rendered: Perlmutter's sbatch
  rejects `-L cvmfs` and cvmfs is mounted on every node.
- `status` reads `sacct` through `compute/jobs/{machine}?sacct=true&cached=false&kwargs=jobid=N`
  (the cached view only covers today) and maps Slurm states onto the IRI
  vocabulary (queued/active/completed/failed/canceled); a job not yet in
  sacct is `queued`.
- Optional keys `sfapi_api` (default `https://api.nersc.gov/api/v1.2`)
  and `machine` (default `perlmutter`).

Confirmed 2026-09-13: run G4blSfapi.e470313 (Slurm 58267921, the bare-exec
version, one index ran) and G4blSfapi.e470313-001 (Slurm 58268029, srun: 2 of 2
outputs, 1 min 24 s in the shared qos, record `complete`) laid out,
submitted and tracked through v1.2 while the IRI v2 adapter was still
down.

