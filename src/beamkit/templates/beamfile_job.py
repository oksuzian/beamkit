#!/usr/bin/env python
# beamkit NERSC backend: build one BLTrackFile on the node from the run's
# nts files on CFS. Self-contained: beamkit's beamfile module is embedded
# below; uproot comes from the cvmfs ana environment this runs under.
import glob
import json
import os
import re
import sys
from datetime import datetime, timezone

RUN_DIR = "@@RUN_DIR@@"
OWNER, TAG, DSCONF = "@@OWNER@@", "@@TAG@@", "@@DSCONF@@"
EVENTS_PER_JOB, NJOBS = @@EVENTS_PER_JOB@@, @@NJOBS@@
PLANE, LABEL, FLAVOR = "@@PLANE@@", "@@LABEL@@", "@@FLAVOR@@"
CUTS = json.loads('@@CUTS_JSON@@')
OUT_TXT = "@@OUT_TXT@@"
OUT_JSON = "@@OUT_JSON@@"

# ---- beamkit/beamfile.py, embedded ----
@@BEAMFILE_MODULE@@
# ---- end beamfile.py ----

BRANCHES = ("x", "y", "z", "Px", "Py", "Pz", "t", "PDGid", "EventID", "TrackID", "ParentID")
NAME_RE = re.compile(r"^nts\." + re.escape(OWNER) + r"\." + re.escape(TAG) + r"\." + re.escape(DSCONF) + r"\.(\d{8})\.root$")


def source_files():
    out = []
    for p in glob.glob(os.path.join(RUN_DIR, "out", "nts.*.root")):
        m = NAME_RE.match(os.path.basename(p))
        if m:
            out.append((int(m.group(1)), p))
    return [p for _, p in sorted(out)]


def rows(paths):
    import uproot
    key = "NTuple/" + PLANE
    for p in paths:
        f = uproot.open(p)
        if key not in f:
            raise BeamfileError("%s not in %s" % (key, p))
        a = f[key].arrays(list(BRANCHES), library="np")
        cols = [a[b] for b in BRANCHES]
        for i in range(len(a["x"])):
            yield Row(float(cols[0][i]), float(cols[1][i]), float(cols[2][i]), float(cols[3][i]),
                      float(cols[4][i]), float(cols[5][i]), float(cols[6][i]),
                      int(cols[7][i]), int(cols[8][i]), int(cols[9][i]), int(cols[10][i]))


def main():
    cuts = validate_cuts(CUTS)
    files = source_files()
    if not files:
        print("no nts files under %s/out" % RUN_DIR, file=sys.stderr)
        return 3
    present = [int(NAME_RE.match(os.path.basename(p)).group(1)) for p in files]
    stats = write_rows(OUT_TXT, rows(files), cuts)
    side = {"run_id": TAG + "." + DSCONF, "flavor": FLAVOR, "label": LABEL, "cuts": cuts, "plane": PLANE,
            "path": OUT_TXT, "sha256": stats["sha256"], "size": stats["size"], "rows": stats["rows_out"],
            "rows_in": stats["rows_in"], "dropped": stats["dropped"],
            "pot": len(files) * EVENTS_PER_JOB, "n_files": len(files),
            "missing_indices": missing_indices(present, NJOBS),
            "source_files": [os.path.basename(p) for p in files], "sam_name": None, "location": None,
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    with open(OUT_JSON + ".part", "w") as fh:
        json.dump(side, fh, indent=2)
        fh.write("\n")
    os.replace(OUT_JSON + ".part", OUT_JSON)
    print("BK_BEAMFILE_DONE rows=%d files=%d" % (stats["rows_out"], len(files)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
