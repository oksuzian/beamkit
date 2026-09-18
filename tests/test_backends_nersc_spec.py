import pytest

from beamkit import BeamkitError, backends
from beamkit.backends import nersc
from beamkit.nersc_config import NerscConfig

RUN_DIR = "/global/cfs/cdirs/m4599/Users/u/beamkit/runs/T.e470313"


@pytest.fixture
def cfg(tmp_path):
    return NerscConfig(api="https://api.iri.nersc.gov/api/v2", sfapi_dir=tmp_path, account="m4599",
                       base_dir="/global/cfs/cdirs/m4599/Users/u/beamkit", qos="debug", owner="u",
                       procs_per_node=128, image="/cvmfs/img", apptainer="/cvmfs/apptainer")


@pytest.mark.parametrize("njobs,ppn,want", [
    (1, 128, [(0, 1)]),
    (128, 128, [(0, 128)]),
    (129, 128, [(0, 128), (128, 1)]),
    (300, 128, [(0, 128), (128, 128), (256, 44)]),
    (5, 2, [(0, 2), (2, 2), (4, 1)]),
])
def test_slices(njobs, ppn, want):
    assert nersc.slices(njobs, ppn) == want


def test_slices_cover_every_index_once():
    s = nersc.slices(10000, 128)
    assert sum(c for _, c in s) == 10000 and len(s) == 79 and s[-1] == (9984, 16)


def test_duration_formula_and_cap():
    assert nersc.duration_s(1000, 172800) == 2900
    assert nersc.duration_s(1000, 1800) == 1800


@pytest.mark.parametrize("bad", [0, -1, 1.5, "3600", True])
def test_walltime_refused(bad):
    with pytest.raises(BeamkitError, match="walltime_s"):
        nersc.validate_walltime(bad)


def test_job_spec_shape(cfg):
    spec = nersc.job_spec(cfg, run_id="T.e470313", run_dir=RUN_DIR, offset=128, count=44, duration=2900)
    assert spec == {
        "name": "beamkit.T.e470313.128",
        "executable": "/bin/bash",
        "arguments": [f"{RUN_DIR}/job.sh"],
        "directory": RUN_DIR,
        "stdout_path": f"{RUN_DIR}/slurm/128.out",
        "stderr_path": f"{RUN_DIR}/slurm/128.err",
        "inherit_environment": False,
        "environment": {"BK_OFFSET": "128"},
        "resources": {"node_count": 1, "process_count": 44, "processes_per_node": 44,
                      "cpu_cores_per_process": 1, "exclusive_node_use": False},
        "attributes": {"duration": 2900, "queue_name": "shared", "account": "m4599",
                       "custom_attributes": {"constraint": "cpu", "licenses": "cvmfs", "module": "cvmfs"}},
    }


@pytest.mark.parametrize("count,exclusive,queue", [
    (128, True, "debug"),    # full slice: whole node, configured qos
    (65, True, "debug"),     # partial but over the shared cap: whole node
    (64, False, "shared"),   # at the cap: shared, per-core charge
    (2, False, "shared"),
    (1, False, "shared"),    # the beam-file job
])
def test_job_spec_partial_slice_goes_shared(cfg, count, exclusive, queue):
    spec = nersc.job_spec(cfg, run_id="T.e470313", run_dir=RUN_DIR, offset=0, count=count, duration=1000)
    assert spec["resources"]["exclusive_node_use"] is exclusive
    assert spec["attributes"]["queue_name"] == queue


def test_job_spec_honours_shared_config(cfg):
    from dataclasses import replace
    c = replace(cfg, shared_qos="debug", shared_max_procs=8)
    assert nersc.job_spec(c, run_id="T.e470313", run_dir=RUN_DIR, offset=0, count=8, duration=1)["attributes"]["queue_name"] == "debug"
    assert nersc.job_spec(c, run_id="T.e470313", run_dir=RUN_DIR, offset=0, count=9, duration=1)["resources"]["exclusive_node_use"] is True


def test_get_returns_the_backend_module():
    assert backends.get("nersc") is nersc
    with pytest.raises(BeamkitError, match="site must be one of"):
        backends.get("ornl")


def test_block_type_is_the_backend_s_block_class():
    assert backends.block_type("nersc") is nersc.Block
