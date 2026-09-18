# beamkit

The vocabulary of beamkit, the G4beamline production front end over the Mu2e
prodtools `g4bl` runner. beamkit owns deck pins, names, run records and beam
files; prodtools owns the queue, the catalog and submission state.

## Language

**Tag**:
The caller-supplied physics label of a run, used as its Mu2e `desc`.
_Avoid_: description, name, job name

**Deck**:
A G4BeamlineScripts checkout at one commit, the input g4bl runs.
_Avoid_: scripts, geometry, config

**Deck pin**:
The commit sha a run's deck was materialized from; the dsconf is derived from it.
_Avoid_: version, ref (a ref is what the caller passes; the pin is what it resolved to)

**Dsconf**:
The first seven hex characters of the deck pin, suffixed `-NNN` only on a SAM collision.
_Avoid_: configuration, version

**Run**:
One tag at one dsconf: a deck pin, its composed entry, one prodtools campaign, and the record of both.
_Avoid_: job, submission, campaign (a campaign is prodtools' half of a run)

**Run id**:
`<tag>.<dsconf>`; unique by construction and never reused.

**Run record**:
The file under `runs/<run_id>/` holding what prodtools does not know: pin, params, ticks, beam files.
_Avoid_: ledger (the ledger is prodtools'), state file

**Identity**:
Who a call acts as. `run_as` resolved into owner, ledger, confirm requirement and what may ship.
_Avoid_: account, user, principal

**Owner**:
The account name in every Mu2e name a run produces: `$USER` for self, `mu2e` for mu2epro.

**Campaign**:
prodtools' record of a run's submission: slices, cursor, state. Created by `push_cnf`.

**Tick**:
One `submissions run`: verify, resubmit missing indices, feed the next slice. Nothing advances without one.
_Avoid_: cron, poll, sync

**Bare tick**:
A tick with no campaign filter; the only form that reaches a campaign prodtools has marked complete.

**Beam file**:
A g4bl BLTrackFile built from a run's ntuples at one plane with one cut table.
_Avoid_: source file, input file

**Flavor**:
The name of a cut table: a preset (`bm`, `ps`) or a caller label with an explicit `cuts` dict.
_Avoid_: mode, type

**Label**:
The name of a beam file's local files and SAM artifact; defaults to the flavor.
_Avoid_: flavor (a label may name a second file built with the same flavor)

**Site**:
Where a run's jobs execute: `fermilab` (the grid, through prodtools) or `nersc` (Perlmutter, through the Superfacility API).
_Avoid_: backend (the backend is the code; the site is the place), facility, cluster

**Backend**:
The module that carries one site's half of every tool: the name-taken probe, the retry predicate, enqueue, status, outputs, beam-file build. Two backends, one interface; `tools.py` never knows which.
_Avoid_: driver, plugin, adapter (an adapter is the architecture word; in beamkit's language it is a backend)

**Run request**:
Every caller value of `run_beamline`, validated once, site-neutral fields plus the two site-specific ones. The backend refuses the other site's argument.
_Avoid_: args, kwargs, options

**Site block**:
The part of a run record only its site's backend reads and writes: `fermilab` (campaign, tarball, ticks, prodtools) or `nersc` (run dir on CFS, cnf, walltime, Slurm jobs, config). Declared by the backend, stored by records.py without knowing the keys.
_Avoid_: extra, meta, dict

**Sidecar**:
The `.json` beside a beam file: cuts, counts, pot, missing indices, source files.

## Relationships

- A **Run** has exactly one **Deck pin**, one **Dsconf**, one **Identity**, one **Site** and one **Site block**; a Fermilab **Run** also has one **Campaign**.
- A **Tag** at two **Deck pins** is two **Runs**; the same **Tag** and pin twice is a `-NNN` **Dsconf**.
- A **Run** produces one nts dataset and any number of **Beam files**.
- A **Beam file** has one **Flavor** and one **Label**; several **Beam files** may share a **Flavor**.
- A **Tick** advances every **Campaign** in an **Identity**'s ledger; only its top-up can be scoped to one.

## Example dialogue

> **Dev:** "The **run** finished its only slice but `make_recoveries` says the **campaign** is complete. Is it done?"
> **Domain expert:** "Complete means every slice was submitted, not that every job succeeded. The rows still verify. That is why the tool switched to a **bare tick**: a scoped tick on a complete campaign is refused."
> **Dev:** "And if I want a second **beam file** with the same cuts?"
> **Domain expert:** "Same **flavor**, different **label**. The label names the files; the flavor names the cuts."

## Flagged ambiguities

- "location" meant both the read side (`outloc`, where the run's ntuples live) and the write side (where a published beam file lands). Resolved: `outloc` is the run's; `location` is the beam file's.
- "flavor" meant both the cut table and the file label. Resolved: **Flavor** is cuts, **Label** is names.
- "campaign" was used for the whole run. Resolved: a **Campaign** is prodtools' half; a **Run** is the whole.
- "backend" and "site" were used for each other. Resolved: a **Site** is the place; a **Backend** is the module for it.
- "retry" meant both a recovery tick and calling `run_beamline` again after an `enqueue_failed`. Resolved: a **Tick** recovers; a retryable run dir is one a new `run_beamline` may overwrite because the previous attempt created nothing at the site.

