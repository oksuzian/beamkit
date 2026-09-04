import os
import subprocess
from pathlib import Path

import pytest

from beamkit import beamfile

FIX = Path(__file__).parent / "fixtures"
ANA = beamfile.ANA_PYTHON
needs_ana = pytest.mark.skipif(not os.path.exists(ANA), reason="ana interpreter not on this host")
SCRIPT = Path(beamfile.__file__).with_name("_read_plane.py")


@needs_ana
def test_script_prints_47_rows_tsv():
    out = subprocess.run([ANA, str(SCRIPT), "Z3712", str(FIX / "plane47.root")],
                         capture_output=True, text=True, check=True).stdout
    lines = out.splitlines()
    assert len(lines) == 47
    first = lines[0].split("\t")
    assert len(first) == 11
    assert first[7:] == ["-11", "1", "9303", "9302"]
    assert float(first[0]) == pytest.approx(-60.858173, abs=1e-5)


@needs_ana
def test_script_missing_plane_exit_3():
    proc = subprocess.run([ANA, str(SCRIPT), "Z9999", str(FIX / "plane47.root")], capture_output=True, text=True)
    assert proc.returncode == 3 and "NTuple/Z9999 not in" in proc.stderr


def test_script_usage_exit_2():
    proc = subprocess.run(["python3", str(SCRIPT), "Z3712"], capture_output=True, text=True)
    assert proc.returncode == 2


@needs_ana
def test_iter_plane_rows_two_files_concatenate():
    rows = list(beamfile.iter_plane_rows([FIX / "plane47.root", FIX / "plane47.root"], "Z3712"))
    assert len(rows) == 94 and isinstance(rows[0], beamfile.Row)
    assert rows[0].PDGid == -11 and rows[0].EventID == 1 and rows[47].EventID == 1


@needs_ana
def test_iter_plane_rows_missing_plane_raises():
    with pytest.raises(beamfile.BeamfileError, match="Z9999"):
        list(beamfile.iter_plane_rows([FIX / "plane47.root"], "Z9999"))


def test_iter_plane_rows_bad_interpreter_raises(tmp_path):
    with pytest.raises(beamfile.BeamfileError, match="interpreter"):
        list(beamfile.iter_plane_rows([FIX / "plane47.root"], "Z3712", python=str(tmp_path / "nope")))


def test_iter_plane_rows_malformed_output_raises(tmp_path):
    # Interpreter-shaped stand-in: iter_plane_rows always invokes
    # [python, SCRIPT, plane, *paths], so a shell script masquerading as the
    # interpreter works fine — it ignores the SCRIPT/plane/path arguments.
    fake = tmp_path / "fake_ana"
    fake.write_text("#!/bin/bash\necho -e '1.0\\t2.0'\nexit 0\n")
    fake.chmod(0o755)
    with pytest.raises(beamfile.BeamfileError, match="Z3712"):
        list(beamfile.iter_plane_rows([FIX / "plane47.root"], "Z3712", python=str(fake)))


def test_iter_plane_rows_non_executable_interpreter_raises(tmp_path):
    fake = tmp_path / "not_executable"
    fake.write_text("not a real interpreter\n")
    fake.chmod(0o644)
    with pytest.raises(beamfile.BeamfileError) as exc:
        list(beamfile.iter_plane_rows([FIX / "plane47.root"], "Z3712", python=str(fake)))
    assert str(fake) in str(exc.value)


@needs_ana
@pytest.mark.parametrize("flavor", ["bm", "ps"])
def test_build_matches_makesource_reference(tmp_path, flavor):
    # plane47.root has no exotic particles (PDGid > 1e6) and only 1-2
    # duplicate / negative-Pz rows, so this byte-comparison does not exercise
    # those three structural cuts. They are covered directly by
    # test_structural_exotic_dropped, test_structural_pz_negative_dropped_zero_kept,
    # and test_duplicate_is_against_last_written_row in tests/test_beamfile.py.
    out = tmp_path / f"{flavor}.txt"
    stats = beamfile.build([FIX / "plane47.root"], "Z3712", beamfile.FLAVORS[flavor], out)
    assert out.read_bytes() == (FIX / f"reference_{flavor}.txt").read_bytes()
    assert stats["rows_in"] == 47
    assert stats["rows_out"] == out.read_text().count("\n") - 3
