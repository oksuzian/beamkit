from pathlib import Path

import pytest

from beamkit import nersc_templates as nt
from beamkit.nersc_config import NerscConfig

GOLD = Path(__file__).parent / "fixtures" / "nersc"
RUN_DIR = "/global/cfs/cdirs/m4599/Users/u/beamkit/runs/T.e470313"


@pytest.fixture
def cfg(tmp_path):
    return NerscConfig(api="https://api.iri.nersc.gov/api/v2", sfapi_dir=tmp_path, account="m4599",
                       base_dir="/global/cfs/cdirs/m4599/Users/u/beamkit", qos="regular", owner="u",
                       procs_per_node=128,
                       image="/cvmfs/singularity.opensciencegrid.org/fermilab/fnal-wn-el9:latest",
                       apptainer="/cvmfs/oasis.opensciencegrid.org/mis/apptainer/current/bin/apptainer")


def test_g4bl_script_matches_the_prodtools_shape():
    s = nt.g4bl_script("Mu2E.in", 11, 10, "/x/nts.root", {"epsMax": "0.01", "Beam_File": "a b"})
    assert s == ("unset SPACK_ENV PYTHONHOME PYTHONPATH PYTHONNOUSERSITE\n"
                 "source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh > /dev/null 2>&1\n"
                 'eval "$(spack load --sh g4beamline)"\n'
                 "cd work\n"
                 "g4bl Mu2E.in viewer=none First_Event=11 Num_Events=10 histoFile=/x/nts.root "
                 "Beam_File='a b' epsMax=0.01")


def test_g4bl_command_passes_first_event_and_histo_arg_through_unquoted():
    """first_event and histo_arg arrive pre-rendered by the caller (a shell
    variable reference here); g4bl_command must not quote them itself, only
    main_input and params."""
    c = nt.g4bl_command("Mu2E.in", "$FIRST", 10, '"$HISTO"', {"epsMax": "0.01"})
    assert c == 'g4bl Mu2E.in viewer=none First_Event=$FIRST Num_Events=10 histoFile="$HISTO" epsMax=0.01'


def test_g4bl_command_quotes_main_input_and_params_with_whitespace():
    c = nt.g4bl_command("Mu2E.in", 11, 10, "/x/nts.root", {"Beam_File": "a b"})
    assert c == "g4bl Mu2E.in viewer=none First_Event=11 Num_Events=10 histoFile=/x/nts.root Beam_File='a b'"


def test_render_refuses_a_leftover_placeholder():
    with pytest.raises(nt.TemplateError, match="@@IMAGE@@"):
        nt.render("job.sh", {"APPTAINER": "/a", "RUN_DIR": "/r"})


def test_render_refuses_an_unknown_key():
    with pytest.raises(nt.TemplateError, match="EXTRA"):
        nt.render("job.sh", {"APPTAINER": "/a", "RUN_DIR": "/r", "IMAGE": "/i", "EXTRA": "x"})


def test_job_sh_golden(cfg):
    assert nt.render_job(cfg, RUN_DIR) == (GOLD / "job.sh.golden").read_text()


def test_inner_sh_golden(cfg):
    got = nt.render_inner(cfg, run_id="T.e470313", run_dir=RUN_DIR, owner="u", tag="T", dsconf="e470313",
                          events_per_job=10, main_input="Mu2E.in", params={"epsMax": "0.01"})
    assert got == (GOLD / "inner.sh.golden").read_text()


def test_beamfile_sh_golden(cfg):
    got = nt.render_beamfile_sh(cfg, job_py=f"{RUN_DIR}/beamfiles/beamfile_job.bm.py")
    assert got == (GOLD / "beamfile.sh.golden").read_text()


def test_inner_sh_has_no_braces_that_bash_would_misread(cfg):
    got = nt.render_inner(cfg, run_id="T.e470313", run_dir=RUN_DIR, owner="u", tag="T", dsconf="e470313",
                          events_per_job=10, main_input="Mu2E.in", params=None)
    assert "@@" not in got and "${" not in got


def test_inner_sh_quotes_a_params_value_with_whitespace(cfg):
    """The node's rendering must match prodtools' shlex-quoted g4bl line for
    any params value, not only for the literal case the contract probe
    checks -- render_inner used to drop quoting via quote=str."""
    got = nt.render_inner(cfg, run_id="T.e470313", run_dir=RUN_DIR, owner="u", tag="T", dsconf="e470313",
                          events_per_job=10, main_input="Mu2E.in", params={"Beam_File": "a b"})
    lines = [l for l in got.splitlines() if l.startswith("g4bl ")]
    assert len(lines) == 1 and "Beam_File='a b'" in lines[0]
