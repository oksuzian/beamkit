"""Standalone: dump one ntuple plane from g4bl output files as TSV.

Runs under the ana interpreter (uproot), never imports beamkit. One line
per entry: x y z Px Py Pz t as repr(float) so the float32 values survive
exactly, then PDGid EventID TrackID ParentID as ints (MakeSource.py used
int() on each). Files are emitted in argv order.

Exit 2: usage. Exit 3: a file lacks NTuple/<plane>.
"""
import sys

BRANCHES = ("x", "y", "z", "Px", "Py", "Pz", "t", "PDGid", "EventID", "TrackID", "ParentID")


def main(argv):
    if len(argv) < 3:
        print("usage: _read_plane.py <plane> <file.root>...", file=sys.stderr)
        return 2
    plane, paths = argv[1], argv[2:]
    import uproot
    key = f"NTuple/{plane}"
    out = sys.stdout
    for p in paths:
        f = uproot.open(p)
        if key not in f:
            print(f"_read_plane: {key} not in {p}", file=sys.stderr)
            return 3
        a = f[key].arrays(list(BRANCHES), library="np")
        n = len(a["x"])
        cols = [a[b] for b in BRANCHES]
        for i in range(n):
            floats = [repr(float(c[i])) for c in cols[:7]]
            ints = [str(int(c[i])) for c in cols[7:]]
            out.write("\t".join(floats + ints) + "\n")
    out.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
