"""The cnf tarball for a NERSC run, built on the caller's machine: work/
(the deck without VCS internals) plus jobpars.json in the shape prodtools'
json2jobdef._build_g4bl_tarball writes, so a later harvest can declare it
as the parent of every nts file unchanged."""
import json
import os
import tarfile
from pathlib import Path

from beamkit import BeamkitError, iri

VCS_DIRS = (".git", ".svn", ".hg")


class CnfError(BeamkitError):
    pass


def jobpars(*, owner, tag, dsconf, main_input, events_per_job, njobs, params) -> dict:
    jp = {"runner": "g4bl", "desc": tag, "dsconf": dsconf, "main_input": main_input,
          "events_per_job": int(events_per_job), "njobs": int(njobs), "owner": owner}
    if params:
        jp["g4bl_params"] = dict(params)
    jp["tbs"] = {"njobs": int(njobs), "outfiles": {"g4bl": f"nts.owner.{tag}.version.sequencer.root"}}
    return jp


def _skip_vcs(ti):
    return None if any(part in VCS_DIRS for part in ti.name.split("/")) else ti


def build_cnf(deck_dir, jobpars: dict, out_path) -> Path:
    deck_dir, out_path = Path(deck_dir), Path(out_path)
    if not (deck_dir / jobpars["main_input"]).is_file():
        raise CnfError(f"main_input {jobpars['main_input']!r} not found in deck dir {deck_dir}")
    if out_path.exists():
        raise CnfError(f"{out_path} exists; a cnf is never overwritten")
    part = out_path.with_name(out_path.name + ".part")
    jp_path = out_path.with_name("jobpars.json")
    jp_path.write_text(json.dumps(jobpars, indent=2) + "\n")
    try:
        with tarfile.open(part, "w") as t:
            t.add(deck_dir, arcname="work", filter=_skip_vcs)
            t.add(jp_path, arcname="jobpars.json")
        size = part.stat().st_size
        if size > iri.UPLOAD_MAX:
            raise CnfError(f"{out_path.name}: {size} bytes exceeds the {iri.UPLOAD_MAX}-byte upload cap of the "
                           f"API; shrink the deck (large geometry or beam files do not belong in the cnf)")
        os.replace(part, out_path)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    finally:
        jp_path.unlink(missing_ok=True)
    return out_path
