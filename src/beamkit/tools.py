"""The beamkit tools. server.py registers them with FastMCP as they are, so
every parameter is annotated and `run_as` has no default where a call
writes. Orchestration only: load the record or build a RunRequest, then
delegate to runs.create or backends.get(site)."""
import os
import sys
from typing import Optional

from beamkit import (BeamkitError, __version__, backends, bridge, identity, nersc_config, paths,
                     records, runs)
from beamkit.backends import fermilab as fermilab_backend
from beamkit.backends import nersc as nersc_backend
from beamkit.decks import DEFAULT_DECK_URL


def _load(run_id):
    return records.load(run_id, paths.runs_dir())


def run_beamline(tag: str, run_as: str, deck_ref: Optional[str] = None, params: Optional[dict] = None,
                 events_per_job: int = 1000, njobs: int = 1, main_input: str = "Mu2E.in",
                 outloc: str = "scratch", dsconf: Optional[str] = None, slice_size: Optional[int] = None,
                 submit: bool = True, confirm: bool = False, deck_dir: Optional[str] = None,
                 deck_url: str = DEFAULT_DECK_URL, site: str = "fermilab",
                 walltime_s: Optional[int] = None) -> dict:
    """Pin the deck, allocate desc=tag and dsconf=<sha7>, then either
    register the cnf and create its campaign through prodtools and submit
    it in one tick (site='fermilab'), or build the cnf locally, lay the run
    out on CFS and submit one Slurm job per procs_per_node indices through
    the IRI API (site='nersc'). Every caller value is refused before the
    deck fetch and the remote probe, so a refused call burns no dsconf and
    writes no record."""
    return runs.create(runs.RunRequest(tag=tag, run_as=run_as, site=site, deck_ref=deck_ref, deck_dir=deck_dir,
                                       deck_url=deck_url, params=params, events_per_job=events_per_job,
                                       njobs=njobs, main_input=main_input, outloc=outloc, dsconf=dsconf,
                                       submit=submit, confirm=confirm, slice_size=slice_size,
                                       walltime_s=walltime_s))


def make_recoveries(run_id: str, run_as: str, confirm: bool = False) -> dict:
    """One prodtools tick for this run. While the campaign is active the tick
    is scoped to it. Once prodtools has marked it complete (every slice
    submitted, rows still verifying) a scoped tick is refused as not
    active, so the bare tick is used: its verify/recovery pass reaches this
    run's rows and its top-up feeds every other active campaign in the
    ledger. The result names which form ran."""
    rec = _load(run_id)
    return backends.get(rec.site).make_recoveries(rec, run_as, confirm)


def submit_run(run_id: str, run_as: str) -> dict:
    """NERSC runs only: submit the Slurm jobs of a run created with
    submit=False, or the jobs a partial submit did not reach. The Fermilab
    path submits through make_recoveries."""
    rec = _load(run_id)
    return backends.get(rec.site).submit_run(rec, run_as)


def beamline_status(run_id: str) -> dict:
    """The run record with the site's view of it: prodtools' campaign_status
    (site='fermilab', None before a campaign exists) or the Slurm job states,
    CFS output counts and beam-file jobs (site='nersc')."""
    rec = _load(run_id)
    status = backends.get(rec.site).status(rec)      # nersc: also reconciles and saves the record
    return {"record": rec.to_dict(), "site": rec.site, "status": status}


def list_beamline_runs(state: Optional[str] = None) -> dict:
    """Run records under this user's beamkit dir, newest first."""
    recs = records.list_runs(paths.runs_dir(), state=state)
    return {"records_dir": str(paths.runs_dir()), "count": len(recs), "runs": [r.to_dict() for r in recs]}


def beamline_outputs(run_id: str) -> dict:
    """Files of the run's nts dataset with sizes and paths: dCache via SAM
    for a Fermilab run, CFS via one ls for a NERSC run."""
    rec = _load(run_id)
    return backends.get(rec.site).outputs(rec)


def fetch_outputs(run_id: str, dest: str, kind: str = "nts") -> dict:
    """Copy a NERSC run's nts files (kind="nts") or complete beam files
    (kind="beamfiles") from CFS into the local directory dest, through the
    API, at most 5 MB per file; a run with a larger file is refused whole.
    Files already in dest with the right size are not fetched again. A
    Fermilab run's outputs are in dCache already."""
    rec = _load(run_id)
    return backends.get(rec.site).fetch_outputs(rec, dest, kind)


def make_beamfile(run_id: str, flavor: str, run_as: str, plane: str = "Z3712", cuts: Optional[dict] = None,
                  publish: bool = False, location: Optional[str] = None, confirm: bool = False,
                  label: Optional[str] = None, site: str = "fermilab") -> dict:
    """A BLTrackFile from whatever nts files the run has: read from SAM and
    built here (site='fermilab'), or built by one Slurm job on Perlmutter
    from the files on CFS (site='nersc', never downloads). No completeness
    check: pot = n_files * events_per_job and missing_indices are recorded.
    flavor "bm" or "ps" selects a preset cut table; any other flavor needs
    cuts={keep_pdg, drop_pdg, min_p_mev}. label names the files and the SAM
    artifact and defaults to the flavor. publish=true pushes it to SAM via
    prodtools push_file, to tape for mu2epro and scratch for self."""
    backend = backends.get(site)
    rec = _load(run_id)
    if rec.site != site:
        raise BeamkitError(f"run {run_id} is a {rec.site!r} run; pass site={rec.site!r}")
    return backend.make_beamfile(rec, flavor=flavor, run_as=run_as, plane=plane, cuts=cuts,
                                 label=label, publish=publish, location=location, confirm=confirm)


def get_server_info() -> dict:
    """beamkit version, which backends this host can drive, directories, limits."""
    f_ok, f_detail = fermilab_backend.available()
    n_ok, n_detail = nersc_backend.available()
    return {"name": "beamkit", "version": __version__, "python": sys.executable,
            "prodtools": bridge.prodtools_info() if f_ok else None,
            "dev_dir": os.environ.get(identity.DEV_DIR_VAR) or None,
            "backends": {"fermilab": {"available": f_ok, "detail": f_detail},
                         "nersc": {"available": n_ok, "detail": n_detail,
                                   "config": str(nersc_config.config_path(paths.home()))}},
            "deck_url": DEFAULT_DECK_URL,
            "decks_dir": str(paths.decks_dir()), "records_dir": str(paths.runs_dir()),
            "beamfiles_dir": str(paths.beamfiles_dir()), "slice_max": fermilab_backend.SLICE_MAX,
            "walltime_default": nersc_backend.WALLTIME_DEFAULT}
