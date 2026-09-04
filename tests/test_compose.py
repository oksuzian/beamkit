import json

import pytest

from beamkit import compose


@pytest.fixture
def deck(tmp_path):
    d = tmp_path / "deck"
    d.mkdir()
    (d / "Mu2E.in").write_text("x\n")
    return d


def test_entry_exact_shape(deck):
    e = compose.entry(tag="MuBeam", dsconf="e470313", deck_dir=deck, main_input="Mu2E.in",
                      events_per_job=1000, njobs=20, outloc="scratch", params={})
    assert e == {"runner": "g4bl", "desc": "MuBeam", "dsconf": "e470313",
                 "g4bl_dir": str(deck), "main_input": "Mu2E.in",
                 "events_per_job": 1000, "njobs": 20, "outloc": {"nts.*.root": "scratch"}}


def test_entry_params_passthrough(deck):
    e = compose.entry(tag="T", dsconf="d", deck_dir=deck, main_input="Mu2E.in",
                      events_per_job=1, njobs=1, outloc="tape", params={"READ_Beam_File": 1, "epsMax": 0.01})
    assert e["g4bl_params"] == {"READ_Beam_File": 1, "epsMax": 0.01}
    assert e["outloc"] == {"nts.*.root": "tape"}


@pytest.mark.parametrize("bad", [{"First_Event": 5}, {"histoFile": "x"}, {"1bad": 1}, {"ok": True}, {"ok": [1]}, "notadict"])
def test_validate_params_refuses(bad):
    with pytest.raises(compose.ComposeError):
        compose.validate_params(bad)


@pytest.mark.parametrize("kw", [dict(events_per_job=0), dict(njobs=0), dict(njobs=True), dict(events_per_job=2.5), dict(outloc="resilient")])
def test_entry_refuses_bad_values(deck, kw):
    base = dict(tag="T", dsconf="d", deck_dir=deck, main_input="Mu2E.in", events_per_job=10, njobs=3, outloc="scratch", params={})
    base.update(kw)
    with pytest.raises(compose.ComposeError):
        compose.entry(**base)


def test_entry_refuses_missing_main_input(deck):
    with pytest.raises(compose.ComposeError, match="main_input"):
        compose.entry(tag="T", dsconf="d", deck_dir=deck, main_input="Other.in",
                      events_per_job=10, njobs=3, outloc="scratch", params={})


def test_write_entry_json_is_a_list(deck, tmp_path):
    e = compose.entry(tag="T", dsconf="d", deck_dir=deck, main_input="Mu2E.in",
                      events_per_job=10, njobs=3, outloc="scratch", params={})
    p = compose.write_entry_json(e, tmp_path / "entry.json")
    assert json.loads(p.read_text()) == [e]
