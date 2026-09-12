#!/bin/bash
# beamkit NERSC backend, inside fnal-wn-el9. Per-index log first, so 128
# tasks on one node never interleave; it lands whether or not g4bl succeeds.
SEQ=$(printf %08d "$IDX")
exec > "@@RUN_DIR@@/out/log.@@OWNER@@.@@TAG@@.@@DSCONF@@.$SEQ.log" 2>&1
set -x
W=/tmp/bk.@@RUN_ID@@.$IDX; mkdir -p "$W"; cd "$W" || exit 2
tar xf "@@RUN_DIR@@"/cnf.*.tar || exit 2
FIRST=$((IDX*@@EVENTS_PER_JOB@@+1))
HISTO="$W/nts.@@OWNER@@.@@TAG@@.@@DSCONF@@.$SEQ.root"
@@G4BL_RECIPE@@
cd work
@@G4BL_COMMAND@@
rc=$?
[ $rc -eq 0 ] && mv "$HISTO" "@@RUN_DIR@@/out/"
sha256sum "@@RUN_DIR@@/out/nts.@@OWNER@@.@@TAG@@.@@DSCONF@@.$SEQ.root" 2>/dev/null
echo "BK_DONE idx=$IDX rc=$rc"
rm -rf "$W"
exit $rc
