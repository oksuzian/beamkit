"""Transliteration of G4BeamlineScripts/MakeSource.py (2013, Python 2 +
PyROOT) to Python 3 + uproot, loop and format strings kept line for line.

PyROOT is not available in the ana 2.8.0 interpreter or the ops env on
this host, so the original cannot run; this is the independent oracle the
beamfile tests byte-compare against. Run once under the ana interpreter:

  /cvmfs/mu2e.opensciencegrid.org/env/ana/2.8.0/bin/python tests/tools/make_source_reference.py \
      tests/fixtures/plane47.root 0   # -> tests/fixtures/reference_ps.txt
  ... plane47.root 1                  # -> tests/fixtures/reference_bm.txt
"""
import sys
from math import fabs, sqrt
from pathlib import Path

import uproot

file_name, source = sys.argv[1], int(sys.argv[2])
suffix = {0: "ps", 1: "bm"}[source]
file_out = Path(file_name).parent / f"reference_{suffix}.txt"

tree = uproot.open(file_name)["NTuple/Z3712"]
a = tree.arrays(["x", "y", "z", "Px", "Py", "Pz", "t", "PDGid", "EventID", "TrackID", "ParentID"], library="np")
oldID = -999
oldEV = -999

with open(file_out, "w") as f:
    f.write('#BLTrackFile: Source file\n')
    f.write('#{:<12} {:<12} {:<12} {:<10} {:<10} {:<10} {:<12} {:<7} {:<10} {:<10} {:<9} {:<7}\n'.format("x", "y", "z", "Px", "Py", "Pz", "t", "PDGid", "EventID", "TrackID", "ParentID", "TrackID"))
    f.write('#{:<12} {:<12} {:<12} {:<10} {:<10} {:<10} {:<12} {:<7} {:<10} {:<10} {:<9} {:<7}\n'.format("mm", "mm", "mm", "MeV/c", "MeV/c", "MeV/c", "ns", "ID", "ID", "ID", "ID", "ID"))
    for k in range(len(a["x"])):
        x, y, z = float(a["x"][k]), float(a["y"][k]), float(a["z"][k])
        Px, Py, Pz, t = float(a["Px"][k]), float(a["Py"][k]), float(a["Pz"][k]), float(a["t"][k])
        PDGid, EventID, TrackID, ParentID = int(a["PDGid"][k]), int(a["EventID"][k]), int(a["TrackID"][k]), int(a["ParentID"][k])
        if oldID == TrackID and oldEV == EventID:
            continue
        if PDGid > 1000000:
            continue
        if PDGid != 2112 and source == 0:
            continue
        if PDGid == 2112 and source == 1:
            continue
        if PDGid == 22 and sqrt(Px * Px + Py * Py + Pz * Pz) < 1.0 and source == 1:
            continue
        if fabs(PDGid) == 11 and sqrt(Px * Px + Py * Py + Pz * Pz) < 10.0 and source == 1:
            continue
        if Pz < 0:
            continue
        f.write('{:<13.3f} {:<12.3f} {:<12.3f} {:<10.3f} {:<10.3f} {:<10.3f} {:<12.3f} {:<7} {:<10} {:<10} {:<7} {:<7}\n'.format(x, y, z, Px, Py, Pz, t, PDGid, EventID, 1, ParentID, TrackID))
        oldID = TrackID
        oldEV = EventID
print(file_out)
