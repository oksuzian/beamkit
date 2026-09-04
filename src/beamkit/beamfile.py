"""Beam files: MakeSource.py (2013) as a library. Structural cuts are fixed;
physics cuts are a validated table with bm/ps presets."""
import hashlib
import math
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable, Iterator, NamedTuple

FLAVOR_RE = re.compile(r"^[a-z][a-z0-9]{0,15}$")
CUT_KEYS = ("keep_pdg", "drop_pdg", "min_p_mev")
DROP_KEYS = ("duplicate", "exotic", "keep_pdg", "drop_pdg", "min_p_mev", "pz_negative")
EXOTIC_ABOVE = 1000000

FLAVORS = {
    "bm": {"keep_pdg": None, "drop_pdg": [2112], "min_p_mev": {22: 1.0, 11: 10.0, -11: 10.0}},
    "ps": {"keep_pdg": [2112], "drop_pdg": [], "min_p_mev": {}},
}

# Verbatim from MakeSource.py.
_COLS = ("x", "y", "z", "Px", "Py", "Pz", "t", "PDGid", "EventID", "TrackID", "ParentID", "TrackID")
_UNITS = ("mm", "mm", "mm", "MeV/c", "MeV/c", "MeV/c", "ns", "ID", "ID", "ID", "ID", "ID")
_HDR_FMT = "#{:<12} {:<12} {:<12} {:<10} {:<10} {:<10} {:<12} {:<7} {:<10} {:<10} {:<9} {:<7}\n"
HEADER = ("#BLTrackFile: Source file\n", _HDR_FMT.format(*_COLS), _HDR_FMT.format(*_UNITS))
ROW_FMT = "{:<13.3f} {:<12.3f} {:<12.3f} {:<10.3f} {:<10.3f} {:<10.3f} {:<12.3f} {:<7} {:<10} {:<10} {:<7} {:<7}\n"


class BeamfileError(ValueError):
    pass


class Row(NamedTuple):
    x: float
    y: float
    z: float
    Px: float
    Py: float
    Pz: float
    t: float
    PDGid: int
    EventID: int
    TrackID: int
    ParentID: int


def _int_key(k, where):
    if isinstance(k, bool):
        raise BeamfileError(f"{where}: PDG id {k!r} is not an int")
    if isinstance(k, int):
        return k
    if isinstance(k, str) and re.fullmatch(r"-?\d+", k):
        return int(k)
    raise BeamfileError(f"{where}: PDG id {k!r} is not an int")


def validate_cuts(cuts) -> dict:
    if not isinstance(cuts, dict):
        raise BeamfileError(f"cuts must be a dict with keys {CUT_KEYS}, got {cuts!r}")
    extra = set(cuts) - set(CUT_KEYS)
    if extra:
        raise BeamfileError(f"cuts has unknown key(s) {sorted(extra)}; allowed: {CUT_KEYS}")
    for k in CUT_KEYS:
        if k not in cuts:
            raise BeamfileError(f"cuts is missing key {k!r}")
    keep = cuts["keep_pdg"]
    if keep is not None:
        if not isinstance(keep, list):
            raise BeamfileError("cuts keep_pdg must be a list of PDG ids or None")
        keep = [_int_key(p, "keep_pdg") for p in keep]
    drop = cuts["drop_pdg"]
    if not isinstance(drop, list):
        raise BeamfileError("cuts drop_pdg must be a list of PDG ids")
    drop = [_int_key(p, "drop_pdg") for p in drop]
    if keep is not None and drop:
        raise BeamfileError("cuts drop_pdg must be [] when keep_pdg is set")
    minp = cuts["min_p_mev"]
    if not isinstance(minp, dict):
        raise BeamfileError("cuts min_p_mev must be a dict of PDG id -> floor in MeV/c")
    norm = {}
    for k, v in minp.items():
        pdg = _int_key(k, "min_p_mev")
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
            raise BeamfileError(f"min_p_mev[{k!r}] must be a non-negative number, got {v!r}")
        norm[pdg] = float(v)
    return {"keep_pdg": keep, "drop_pdg": drop, "min_p_mev": norm}


def resolve_cuts(flavor, cuts) -> dict:
    if not isinstance(flavor, str) or not FLAVOR_RE.match(flavor):
        raise BeamfileError(f"flavor {flavor!r} must match {FLAVOR_RE.pattern}")
    if cuts is None:
        if flavor not in FLAVORS:
            raise BeamfileError(f"flavor {flavor!r} is not a preset ({sorted(FLAVORS)}); pass cuts= for a custom flavor")
        return validate_cuts(FLAVORS[flavor])
    if flavor in FLAVORS:
        raise BeamfileError(f"flavor {flavor!r} is a preset name; custom cuts need their own label")
    return validate_cuts(cuts)


def filter_rows(rows: Iterable[Row], cuts: dict, drops: dict) -> Iterator[Row]:
    """MakeSource.py's loop, cut by cut, in its order. `drops` is updated in place."""
    keep = set(cuts["keep_pdg"]) if cuts["keep_pdg"] is not None else None
    drop = set(cuts["drop_pdg"])
    minp = cuts["min_p_mev"]
    last = None
    for r in rows:
        if last is not None and (r.EventID, r.TrackID) == last:
            drops["duplicate"] += 1
            continue
        if r.PDGid > EXOTIC_ABOVE:
            drops["exotic"] += 1
            continue
        if keep is not None and r.PDGid not in keep:
            drops["keep_pdg"] += 1
            continue
        if r.PDGid in drop:
            drops["drop_pdg"] += 1
            continue
        floor = minp.get(r.PDGid)
        if floor is not None and math.sqrt(r.Px * r.Px + r.Py * r.Py + r.Pz * r.Pz) < floor:
            drops["min_p_mev"] += 1
            continue
        if r.Pz < 0:
            drops["pz_negative"] += 1
            continue
        yield r
        last = (r.EventID, r.TrackID)


def format_row(r: Row) -> str:
    return ROW_FMT.format(r.x, r.y, r.z, r.Px, r.Py, r.Pz, r.t, r.PDGid, r.EventID, 1, r.ParentID, r.TrackID)


def write_rows(out_path, rows: Iterable[Row], cuts: dict) -> dict:
    out_path = Path(out_path)
    if out_path.exists():
        raise BeamfileError(f"{out_path} exists; a beam file is never overwritten")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    part = out_path.with_name(out_path.name + ".part")
    drops = dict.fromkeys(DROP_KEYS, 0)
    rows_in = rows_out = 0
    sha = hashlib.sha256()

    def counted(src):
        nonlocal rows_in
        for r in src:
            rows_in += 1
            yield r

    try:
        with open(part, "w") as fh:
            for h in HEADER:
                fh.write(h)
                sha.update(h.encode())
            for r in filter_rows(counted(rows), cuts, drops):
                line = format_row(r)
                fh.write(line)
                sha.update(line.encode())
                rows_out += 1
    except BaseException:
        try:
            os.unlink(part)
        except OSError:
            pass
        raise
    os.replace(part, out_path)
    return {"rows_in": rows_in, "rows_out": rows_out, "dropped": drops,
            "sha256": sha.hexdigest(), "size": out_path.stat().st_size}


def missing_indices(present, njobs) -> list[int]:
    return sorted(set(range(njobs)) - set(present))


ANA_PYTHON = os.environ.get("BEAMKIT_ANA_PYTHON",
                            "/cvmfs/mu2e.opensciencegrid.org/env/ana/2.8.0/bin/python")
_READ_PLANE = Path(__file__).with_name("_read_plane.py")


def iter_plane_rows(paths, plane, python=None) -> Iterator[Row]:
    """Rows of NTuple/<plane> from every file, in order, via the ana
    interpreter. Any reader failure is raised with its stderr; nothing is
    skipped."""
    python = python or ANA_PYTHON
    if not os.path.exists(python):
        raise BeamfileError(f"ana interpreter {python} not found (set BEAMKIT_ANA_PYTHON)")
    cmd = [python, str(_READ_PLANE), plane, *[str(p) for p in paths]]
    with tempfile.TemporaryFile(mode="w+") as err:
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, stdin=subprocess.DEVNULL, text=True)
        except OSError as e:
            raise BeamfileError(f"could not run ana interpreter {python!r}: {e}") from e
        try:
            for line in proc.stdout:
                try:
                    f = line.rstrip("\n").split("\t")
                    yield Row(float(f[0]), float(f[1]), float(f[2]), float(f[3]), float(f[4]), float(f[5]),
                              float(f[6]), int(f[7]), int(f[8]), int(f[9]), int(f[10]))
                except (ValueError, IndexError) as e:
                    raise BeamfileError(
                        f"_read_plane.py produced malformed output for plane {plane!r}: {line!r} ({e})") from e
        finally:
            proc.stdout.close()
            rc = proc.wait()
        if rc != 0:
            err.seek(0)
            raise BeamfileError(f"_read_plane.py failed (rc={rc}) for plane {plane!r}: {err.read().strip()}")


def build(paths, plane, cuts, out_path, python=None) -> dict:
    return write_rows(out_path, iter_plane_rows(paths, plane, python), cuts)
