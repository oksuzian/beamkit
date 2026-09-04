"""The one-entry JSON prodtools' json2jobdef consumes for a g4bl run."""
import json
import re
from pathlib import Path

OUTLOCS = ("scratch", "disk", "tape")
# The worker sets these on every job (prodtools utils/runmu2e._g4bl_script);
# an override would silently change every job's event range or output name.
G4BL_WORKER_PARAMS = ("First_Event", "Num_Events", "histoFile", "viewer")
PARAM_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ComposeError(ValueError):
    pass


def validate_params(params) -> dict:
    if not isinstance(params, dict):
        raise ComposeError(f"params must be a dict of g4bl parameter name -> value, got {params!r}")
    for name, value in params.items():
        if not isinstance(name, str) or not PARAM_NAME_RE.match(name):
            raise ComposeError(f"params name {name!r} is not a g4bl parameter name ([A-Za-z_][A-Za-z0-9_]*)")
        if name in G4BL_WORKER_PARAMS:
            raise ComposeError(f"params must not set {name!r}: the worker owns {', '.join(G4BL_WORKER_PARAMS)}")
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ComposeError(f"params[{name!r}] must be a string or number, got {value!r}")
    return dict(params)


def _positive_int(name, v) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or v < 1:
        raise ComposeError(f"{name} must be a positive integer, got {v!r}")
    return v


def entry(*, tag, dsconf, deck_dir, main_input, events_per_job, njobs, outloc, params) -> dict:
    _positive_int("events_per_job", events_per_job)
    _positive_int("njobs", njobs)
    if outloc not in OUTLOCS:
        raise ComposeError(f"outloc must be one of {OUTLOCS}, got {outloc!r}")
    if not (Path(deck_dir) / main_input).is_file():
        raise ComposeError(f"main_input {main_input!r} not found in deck dir {deck_dir}")
    e = {
        "runner": "g4bl",
        "desc": tag,
        "dsconf": dsconf,
        "g4bl_dir": str(deck_dir),
        "main_input": main_input,
        "events_per_job": events_per_job,
        "njobs": njobs,
        "outloc": {"nts.*.root": outloc},
    }
    params = validate_params({} if params is None else params)
    if params:
        e["g4bl_params"] = params
    return e


def write_entry_json(entry: dict, path: Path) -> Path:
    path = Path(path)
    path.write_text(json.dumps([entry], indent=2) + "\n")
    return path
