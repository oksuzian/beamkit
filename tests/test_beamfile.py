import hashlib
from pathlib import Path

import pytest

from beamkit import beamfile
from beamkit.beamfile import Row

BM = {"keep_pdg": None, "drop_pdg": [2112], "min_p_mev": {22: 1.0, 11: 10.0, -11: 10.0}}
PS = {"keep_pdg": [2112], "drop_pdg": [], "min_p_mev": {}}
NOCUT = {"keep_pdg": None, "drop_pdg": [], "min_p_mev": {}}


def R(pdg, pz=1.0, ev=1, tr=1, px=0.0, py=0.0):
    return Row(0.0, 0.0, 3712.0, px, py, pz, 0.0, pdg, ev, tr, 0)


def _keep(rows, cuts):
    drops = dict.fromkeys(beamfile.DROP_KEYS, 0)
    return list(beamfile.filter_rows(rows, cuts, drops)), drops


def test_presets_match_spec():
    assert beamfile.FLAVORS == {"bm": BM, "ps": PS}


def test_validate_cuts_normalizes_string_keys():
    out = beamfile.validate_cuts({"keep_pdg": None, "drop_pdg": [2112], "min_p_mev": {"22": 1.0, "-11": 10}})
    assert out == {"keep_pdg": None, "drop_pdg": [2112], "min_p_mev": {22: 1.0, -11: 10.0}}


@pytest.mark.parametrize("bad,key", [
    ({"keep_pdg": None, "drop_pdg": []}, "min_p_mev"),
    ({"keep_pdg": None, "drop_pdg": [], "min_p_mev": {}, "extra": 1}, "extra"),
    ({"keep_pdg": [2112], "drop_pdg": [22], "min_p_mev": {}}, "drop_pdg"),
    ({"keep_pdg": None, "drop_pdg": [], "min_p_mev": {22: -1.0}}, "min_p_mev"),
    ({"keep_pdg": None, "drop_pdg": ["n"], "min_p_mev": {}}, "drop_pdg"),
    ({"keep_pdg": None, "drop_pdg": [], "min_p_mev": {"gamma": 1.0}}, "min_p_mev"),
    ({"keep_pdg": 2112, "drop_pdg": [], "min_p_mev": {}}, "keep_pdg"),
])
def test_validate_cuts_refuses_naming_key(bad, key):
    with pytest.raises(beamfile.BeamfileError, match=key):
        beamfile.validate_cuts(bad)


def test_resolve_preset():
    assert beamfile.resolve_cuts("bm", None) == BM
    assert beamfile.resolve_cuts("ps", None) == PS


def test_resolve_unknown_flavor_without_cuts_refused():
    with pytest.raises(beamfile.BeamfileError, match="preset"):
        beamfile.resolve_cuts("mine", None)


def test_resolve_custom_cuts_under_preset_name_refused():
    with pytest.raises(beamfile.BeamfileError, match="preset"):
        beamfile.resolve_cuts("bm", NOCUT)


@pytest.mark.parametrize("label", ["Mine", "1a", "a-b", "a" * 17, ""])
def test_resolve_bad_flavor_label(label):
    with pytest.raises(beamfile.BeamfileError, match="flavor"):
        beamfile.resolve_cuts(label, NOCUT)


def test_resolve_custom_ok():
    assert beamfile.resolve_cuts("nocut16chars0000", NOCUT) == NOCUT


def test_structural_exotic_dropped():
    kept, drops = _keep([R(1000010020, ev=1), R(2212, ev=2)], NOCUT)
    assert [r.PDGid for r in kept] == [2212] and drops["exotic"] == 1


def test_structural_pz_negative_dropped_zero_kept():
    kept, drops = _keep([R(2212, pz=-0.5, ev=1), R(2212, pz=0.0, ev=2), R(2212, pz=2.0, ev=3)], NOCUT)
    assert [r.Pz for r in kept] == [0.0, 2.0] and drops["pz_negative"] == 1


def test_duplicate_is_against_last_written_row():
    # MakeSource: oldID/oldEV update only when a row is WRITTEN.
    rows = [R(2212, ev=5, tr=9), R(2212, ev=5, tr=9), R(2112, ev=6, tr=1), R(2212, ev=5, tr=9)]
    kept, drops = _keep(rows, BM)
    # row1 written, row2 duplicate of it, row3 neutron dropped (not written), row4 STILL a duplicate of row1
    assert len(kept) == 1 and drops["duplicate"] == 2 and drops["drop_pdg"] == 1


def test_bm_cuts_momentum_floor():
    rows = [R(22, pz=0.5, ev=1), R(22, pz=1.5, ev=2), R(11, pz=9.9, ev=3), R(-11, pz=10.1, ev=4),
            R(2112, ev=5), R(-13, pz=5.0, ev=6)]
    kept, drops = _keep(rows, BM)
    assert [r.PDGid for r in kept] == [22, -11, -13]
    assert drops == {"duplicate": 0, "exotic": 0, "keep_pdg": 0, "drop_pdg": 1, "min_p_mev": 2, "pz_negative": 0}


def test_momentum_is_magnitude():
    # |p| = sqrt(0.6^2+0.8^2+0) = 1.0, not below 1.0 -> kept
    kept, _ = _keep([R(22, pz=0.0, px=0.6, py=0.8)], BM)
    assert len(kept) == 1


def test_ps_keeps_only_neutrons():
    kept, drops = _keep([R(2112, ev=1), R(2212, ev=2), R(22, ev=3)], PS)
    assert [r.PDGid for r in kept] == [2112] and drops["keep_pdg"] == 2


def test_header_and_row_format_verbatim():
    assert beamfile.HEADER[0] == "#BLTrackFile: Source file\n"
    assert beamfile.HEADER[1] == ("#x            y            z            Px         Py         Pz         "
                                  "t            PDGid   EventID    TrackID    ParentID  TrackID\n")
    assert beamfile.HEADER[2] == ("#mm           mm           mm           MeV/c      MeV/c      MeV/c      "
                                  "ns           ID      ID         ID         ID        ID     \n")
    row = Row(-60.858173, 88.61356, 2332.5605, -0.0494989, 4.877285, 1.1932682, 8.6547747, -11, 1, 9303, 9302)
    assert beamfile.format_row(row) == ("-60.858       88.614       2332.561     -0.049     4.877      1.193      "
                                        "8.655        -11     1          1          9302    9303   \n")


def test_write_rows_stats_and_sha(tmp_path):
    out = tmp_path / "b.txt"
    stats = beamfile.write_rows(out, [R(2212, ev=1), R(2112, ev=2), R(2212, ev=3)], BM)
    text = out.read_text()
    assert text.startswith("".join(beamfile.HEADER)) and text.count("\n") == 5
    assert stats["rows_in"] == 3 and stats["rows_out"] == 2 and stats["dropped"]["drop_pdg"] == 1
    assert stats["sha256"] == hashlib.sha256(out.read_bytes()).hexdigest() and stats["size"] == out.stat().st_size
    assert not (tmp_path / "b.txt.part").exists()


def test_write_rows_mid_stream_error_leaves_no_part(tmp_path):
    def bad_rows():
        yield R(2212, ev=1)
        raise beamfile.BeamfileError("reader died")

    out = tmp_path / "b.txt"
    with pytest.raises(beamfile.BeamfileError, match="reader died"):
        beamfile.write_rows(out, bad_rows(), BM)
    assert not out.exists()
    assert not (tmp_path / "b.txt.part").exists()


def test_write_rows_refuses_existing(tmp_path):
    out = tmp_path / "b.txt"
    out.write_text("x")
    with pytest.raises(beamfile.BeamfileError, match="exists"):
        beamfile.write_rows(out, [], BM)


def test_missing_indices():
    assert beamfile.missing_indices([0, 2, 3], 5) == [1, 4]
    assert beamfile.missing_indices([0, 1, 2], 3) == []


def test_split_chunks_conserves_rows_and_order(tmp_path):
    src = tmp_path / "b.txt"
    beamfile.write_rows(src, [R(2212, ev=i) for i in range(1, 11)], NOCUT)
    chunks = beamfile.split_chunks(src, 3, tmp_path / "chunks")
    assert [c.name for c in chunks] == ["chunk_0.txt", "chunk_1.txt", "chunk_2.txt"]
    data = []
    for c in chunks:
        lines = c.read_text().splitlines()
        assert lines[:3] == [h.rstrip("\n") for h in beamfile.HEADER]
        data += lines[3:]
    assert [int(l.split()[8]) for l in data] == list(range(1, 11))
    assert [len(c.read_text().splitlines()) - 3 for c in chunks] == [4, 4, 2]
