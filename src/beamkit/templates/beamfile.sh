#!/bin/bash
# beamkit NERSC backend: build one beam file from the run's nts files, on
# the node, with uproot from the cvmfs ana environment. One process.
exec "@@APPTAINER@@" exec -B /cvmfs -B /global/cfs "@@IMAGE@@" \
  /cvmfs/mu2e.opensciencegrid.org/env/ana/2.8.0/bin/python "@@JOB_PY@@"
