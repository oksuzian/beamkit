import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from beamkit import BeamkitError, beamfile, nersc_templates as nt, tools
from tests.test_backends_nersc_run import BASE, SHA, deck, fake, nersc_home, _run   # noqa: F401
from tests.test_backends_nersc_status import _land, RD

FIX = Path(__file__).parent / "fixtures"
ANA = beamfile.ANA_PYTHON
needs_ana = pytest.mark.skipif(not os.path.exists(ANA), reason="ana interpreter not on this host")


def _render(run_dir):
    return nt.render_beamfile_job(run_dir=run_dir, owner="u", tag="T", dsconf="e470313", events_per_job=10,
                                  njobs=3, plane="Z3712", label="bm", flavor="bm",
                                  cuts=beamfile.resolve_cuts("bm", None))


def test_rendered_job_compiles_and_embeds_the_module():
    src = _render("/global/cfs/x")
    assert "@@" not in src and "from beamkit" not in src
    assert "def filter_rows" in src and "def write_rows" in src and "class BeamkitError" in src
    compile(src, "beamfile_job.py", "exec")


@needs_ana
def test_rendered_job_reproduces_the_reference_beam_file(tmp_path):
    rd = tmp_path / "run"
    (rd / "out").mkdir(parents=True)
    (rd / "beamfiles").mkdir()
    shutil.copy(FIX / "plane47.root", rd / "out" / "nts.u.T.e470313.00000000.root")
    job = rd / "beamfiles" / "beamfile_job.bm.py"
    job.write_text(_render(str(rd)))
    r = subprocess.run([ANA, str(job)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = rd / "beamfiles" / "etc.u.TBeam-bm.e470313.0.txt"
    assert out.read_bytes() == (FIX / "reference_bm.txt").read_bytes()
    side = json.loads((rd / "beamfiles" / "etc.u.TBeam-bm.e470313.0.json").read_text())
    assert side["n_files"] == 1 and side["pot"] == 10 and side["missing_indices"] == [1, 2]
    assert side["rows"] > 0 and side["sha256"]


def test_make_beamfile_submits_one_job_and_records_it(fake):
    _run(njobs=3)
    _land(fake, 0)
    _land(fake, 1)
    entry = tools.make_beamfile("T.e470313", "bm", "self", site="nersc")
    assert entry["state"] == "submitted" and entry["slurm_id"] == "58197743" and entry["n_files_at_submit"] == 2
    assert entry["path"] == f"{RD}/beamfiles/etc.u.TBeam-bm.e470313.0.txt"
    assert f"{RD}/beamfiles/beamfile_job.bm.py" in fake.files and f"{RD}/beamfiles/beamfile.bm.sh" in fake.files
    spec = fake.jobs["58197743"]["spec"]
    assert spec["resources"]["process_count"] == 1 and spec["attributes"]["duration"] == 604
    assert spec["arguments"] == [f"{RD}/beamfiles/beamfile.bm.sh"]
    assert tools.beamline_status("T.e470313")["record"]["beamfiles"][0]["label"] == "bm"


def test_beamfile_completion_copies_the_sidecar_into_the_record(fake):
    _run(njobs=1)
    _land(fake, 0)
    tools.make_beamfile("T.e470313", "bm", "self", site="nersc")
    side = {"run_id": "T.e470313", "flavor": "bm", "label": "bm", "cuts": {}, "plane": "Z3712",
            "path": f"{RD}/beamfiles/etc.u.TBeam-bm.e470313.0.txt", "sha256": "ab", "size": 9, "rows": 4,
            "rows_in": 5, "dropped": {}, "pot": 10, "n_files": 1, "missing_indices": [], "source_files": ["x"],
            "sam_name": None, "location": None, "created": "t"}
    fake.files[f"{RD}/beamfiles/etc.u.TBeam-bm.e470313.0.json"] = json.dumps(side).encode()
    fake.jobs["58197743"]["state"] = "completed"
    st = tools.beamline_status("T.e470313")
    bf = st["record"]["beamfiles"][0]
    assert bf["state"] == "complete" and bf["rows"] == 4 and bf["sha256"] == "ab" and bf["pot"] == 10


def test_beamfile_job_failure_is_recorded(fake):
    _run(njobs=1)
    _land(fake, 0)
    tools.make_beamfile("T.e470313", "bm", "self", site="nersc")
    fake.jobs["58197743"]["state"] = "failed"
    fake.jobs["58197743"]["exit_code"] = 3
    bf = tools.beamline_status("T.e470313")["record"]["beamfiles"][0]
    assert bf["state"] == "failed" and bf["exit_code"] == 3


def test_beamfile_job_canceled_state_is_recorded_as_failed(fake):
    """'canceled' is terminal, same as 'failed', and shares its branch in
    _refresh_beamfiles; untested until now. A canceled job wrote no
    sidecar."""
    _run(njobs=1)
    _land(fake, 0)
    tools.make_beamfile("T.e470313", "bm", "self", site="nersc")
    fake.jobs["58197743"]["state"] = "canceled"
    fake.jobs["58197743"]["exit_code"] = None
    bf = tools.beamline_status("T.e470313")["record"]["beamfiles"][0]
    assert bf["state"] == "failed" and bf["exit_code"] is None


def test_beamfile_job_failure_with_a_readable_sidecar_still_fails(fake):
    """A Slurm job can end 'failed' even though its sidecar downloads and
    parses fine (e.g. a non-zero exit after the JSON was already written);
    the entry must still land 'failed', and the submission timestamp must
    survive (it is not one of the sidecar fields copied in)."""
    _run(njobs=1)
    _land(fake, 0)
    entry = tools.make_beamfile("T.e470313", "bm", "self", site="nersc")
    side = {"run_id": "T.e470313", "flavor": "bm", "label": "bm", "cuts": {}, "plane": "Z3712",
            "path": entry["path"], "sha256": "ab", "size": 9, "rows": 4,
            "rows_in": 5, "dropped": {}, "pot": 10, "n_files": 1, "missing_indices": [], "source_files": ["x"],
            "sam_name": None, "location": None, "created": "sidecar-time"}
    fake.files[entry["sidecar"]] = json.dumps(side).encode()
    fake.jobs["58197743"]["state"] = "failed"
    fake.jobs["58197743"]["exit_code"] = 2
    bf = tools.beamline_status("T.e470313")["record"]["beamfiles"][0]
    assert bf["state"] == "failed" and bf["exit_code"] == 2 and bf["created"] == entry["created"]


def test_label_is_never_reused(fake):
    _run(njobs=1)
    _land(fake, 0)
    tools.make_beamfile("T.e470313", "bm", "self", site="nersc")
    with pytest.raises(BeamkitError, match="label 'bm' is already spent by a submitted beam-file job"):
        tools.make_beamfile("T.e470313", "bm", "self", site="nersc")


def test_no_nts_files_is_refused_with_counts(fake):
    _run(njobs=2)
    with pytest.raises(BeamkitError, match="0 of 2 nts files"):
        tools.make_beamfile("T.e470313", "bm", "self", site="nersc")


def test_location_refused_on_nersc(fake):
    """location selects a Fermilab publish target (tape/scratch); a NERSC
    beam file always lands in beamfiles/ on CFS."""
    _run(njobs=1)
    with pytest.raises(BeamkitError, match="location applies to site='fermilab' publishing only"):
        tools.make_beamfile("T.e470313", "bm", "self", site="nersc", location="scratch")


def test_publish_refused_on_nersc(fake):
    _run(njobs=1)
    with pytest.raises(BeamkitError, match="publishing is part of harvest"):
        tools.make_beamfile("T.e470313", "bm", "self", site="nersc", publish=True)


def test_site_must_match_the_record(fake):
    _run(njobs=1)
    with pytest.raises(BeamkitError, match="is a 'nersc' run; pass site='nersc'"):
        tools.make_beamfile("T.e470313", "bm", "self")
