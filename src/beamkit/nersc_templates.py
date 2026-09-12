"""The files beamkit puts on a Perlmutter node. Placeholders are @@NAME@@
(bash owns `$` and `{}`); every placeholder must be supplied and every
supplied key must be used. The g4bl lines are prodtools'
utils.runmu2e._g4bl_script, reproduced here because the NERSC path runs
no prodtools on the node; tests/_bridge_contract_probe.py holds the two
equal."""
import re
import shlex
from pathlib import Path

from beamkit import BeamkitError

TEMPLATES = Path(__file__).with_name("templates")
PLACEHOLDER = re.compile(r"@@([A-Z0-9_]+)@@")
G4BL_RECIPE = (
    "unset SPACK_ENV PYTHONHOME PYTHONPATH PYTHONNOUSERSITE",
    "source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh > /dev/null 2>&1",
    'eval "$(spack load --sh g4beamline)"',
)


class TemplateError(BeamkitError):
    pass


def g4bl_command(main_input, first_event, num_events, histo_arg, params=None) -> str:
    """prodtools' g4bl line. `main_input` and every `params` value are
    always shlex.quote'd, matching utils.runmu2e._g4bl_script exactly:
    those are arbitrary caller strings and must survive whitespace and
    shell metacharacters unchanged on the node. `first_event` and
    `histo_arg` arrive pre-rendered by the caller, which is either a
    literal (quoted by the caller, e.g. g4bl_script) or a bash variable
    reference that must not be quoted away (render_inner)."""
    extra = "".join(f" {k}={shlex.quote(str(v))}" for k, v in sorted((params or {}).items()))
    return (f"g4bl {shlex.quote(main_input)} viewer=none First_Event={first_event} Num_Events={num_events} "
            f"histoFile={histo_arg}" + extra)


def g4bl_script(main_input, first_event, num_events, histo_path, params=None) -> str:
    """Byte-equal to prodtools utils.runmu2e._g4bl_script for the same arguments."""
    return "\n".join((*G4BL_RECIPE, "cd work",
                      g4bl_command(main_input, first_event, num_events, shlex.quote(histo_path), params)))


def render(name, subs: dict) -> str:
    text = (TEMPLATES / name).read_text()
    wanted = set(PLACEHOLDER.findall(text))
    unknown = set(subs) - wanted
    if unknown:
        raise TemplateError(f"{name}: no placeholder for {sorted(unknown)[0]}")
    missing = wanted - set(subs)
    if missing:
        raise TemplateError(f"{name}: @@{sorted(missing)[0]}@@ not supplied")
    for k, v in subs.items():
        text = text.replace(f"@@{k}@@", str(v))
    return text


def render_job(cfg, run_dir) -> str:
    return render("job.sh", {"APPTAINER": cfg.apptainer, "IMAGE": cfg.image, "RUN_DIR": run_dir})


def render_inner(cfg, *, run_id, run_dir, owner, tag, dsconf, events_per_job, main_input, params) -> str:
    return render("inner.sh", {
        "RUN_ID": run_id, "RUN_DIR": run_dir, "OWNER": owner, "TAG": tag, "DSCONF": dsconf,
        "EVENTS_PER_JOB": int(events_per_job),
        "G4BL_RECIPE": "\n".join(G4BL_RECIPE),
        "G4BL_COMMAND": g4bl_command(main_input, "$FIRST", int(events_per_job), '"$HISTO"', params),
    })


def render_beamfile_sh(cfg, *, job_py) -> str:
    return render("beamfile.sh", {"APPTAINER": cfg.apptainer, "IMAGE": cfg.image, "JOB_PY": job_py})


import json
from beamkit import beamfile as _beamfile_module

_IMPORT_LINE = "from beamkit import BeamkitError\n"


def beamfile_module_source() -> str:
    """beamfile.py as a standalone module: the one beamkit import becomes a
    local class. Nothing else in that file depends on the package."""
    src = Path(_beamfile_module.__file__).read_text()
    if _IMPORT_LINE not in src:
        raise TemplateError("beamfile.py no longer has the expected single beamkit import; update the embedding")
    return src.replace(_IMPORT_LINE, "class BeamkitError(Exception):\n    pass\n", 1)


def render_beamfile_job(*, run_dir, owner, tag, dsconf, events_per_job, njobs, plane, label, flavor, cuts) -> str:
    stem = f"{run_dir}/beamfiles/etc.{owner}.{tag}Beam-{label}.{dsconf}.0"
    return render("beamfile_job.py", {
        "RUN_DIR": run_dir, "OWNER": owner, "TAG": tag, "DSCONF": dsconf,
        "EVENTS_PER_JOB": int(events_per_job), "NJOBS": int(njobs),
        "PLANE": plane, "LABEL": label, "FLAVOR": flavor,
        "CUTS_JSON": json.dumps(cuts).replace("\\", "\\\\").replace("'", "\\'"),
        "OUT_TXT": stem + ".txt", "OUT_JSON": stem + ".json",
        "BEAMFILE_MODULE": beamfile_module_source(),
    })
