#!/bin/bash
# beamkit NERSC backend: one srun task = one g4bl index. Runs native on the
# SLES node and enters the Mu2e EL9 image itself (the API's container block
# cannot bind two paths). Rendered by beamkit; nothing is looked up at run time.
IDX=$((BK_OFFSET + SLURM_PROCID))
exec "@@APPTAINER@@" exec -B /cvmfs -B /global/cfs --env IDX=$IDX "@@IMAGE@@" /bin/bash "@@RUN_DIR@@/inner.sh"
