import pytest

from beamkit import compose

BASE = dict(tag="T", dsconf="d", deck_dir="/deck", main_input="Mu2E.in",
            events_per_job=10, njobs=3, outloc="scratch", params={})


def test_entry_exact_shape():
    e = compose.entry(tag="MuBeam", dsconf="e470313", deck_dir="/deck", main_input="Mu2E.in",
                      events_per_job=1000, njobs=20, outloc="scratch", params={})
    assert e == {"runner": "g4bl", "desc": "MuBeam", "dsconf": "e470313",
                 "g4bl_dir": "/deck", "main_input": "Mu2E.in",
                 "events_per_job": 1000, "njobs": 20, "outloc": {"nts.*.root": "scratch"}}


def test_entry_params_passthrough():
    e = compose.entry(**dict(BASE, outloc="tape", params={"READ_Beam_File": 1, "epsMax": 0.01}))
    assert e["g4bl_params"] == {"READ_Beam_File": 1, "epsMax": 0.01}
    assert e["outloc"] == {"nts.*.root": "tape"}


def test_entry_params_none_means_no_params():
    assert "g4bl_params" not in compose.entry(**dict(BASE, params=None))


@pytest.mark.parametrize("bad", [{"First_Event": 5}, {"histoFile": "x"}, {"1bad": 1}, {"ok": True}, {"ok": [1]}, "notadict"])
def test_validate_params_refuses(bad):
    with pytest.raises(compose.ComposeError):
        compose.validate_params(bad)


def test_validate_inputs_is_the_one_rule_every_entry_value_passes():
    """runs.create calls validate_inputs before any side effect; entry itself
    re-checks nothing, so this is the single home of the rule."""
    assert compose.validate_inputs(events_per_job=10, njobs=3, outloc="scratch", params=None) == {}
    assert compose.validate_inputs(events_per_job=10, njobs=3, outloc="tape", params={"epsMax": 0.01}) == {"epsMax": 0.01}


@pytest.mark.parametrize("kw", [dict(events_per_job=0), dict(events_per_job=2.5), dict(njobs=0),
                                dict(njobs=True), dict(outloc="resilient"), dict(params=[]),
                                dict(params=0), dict(params=False), dict(params=""), dict(params=())])
def test_validate_inputs_refuses_bad_values(kw):
    base = dict(events_per_job=10, njobs=3, outloc="scratch", params={})
    base.update(kw)
    with pytest.raises(compose.ComposeError, match=next(iter(kw))):
        compose.validate_inputs(**base)
