import json
import tarfile

import pytest

from beamkit import iri, nersc_cnf


@pytest.fixture
def deck(tmp_path):
    d = tmp_path / "deck"
    (d / "Geometry").mkdir(parents=True)
    (d / ".git").mkdir()
    (d / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (d / "Mu2E.in").write_text("param -unset First_Event=1\n")
    (d / "Geometry" / "PS.txt").write_text("box\n")
    return d


def test_jobpars_shape():
    jp = nersc_cnf.jobpars(owner="u", tag="T", dsconf="e470313", main_input="Mu2E.in",
                           events_per_job=10, njobs=3, params={"epsMax": "0.01"})
    assert list(jp) == ["runner", "desc", "dsconf", "main_input", "events_per_job", "njobs", "owner",
                        "g4bl_params", "tbs"]
    assert jp["tbs"] == {"njobs": 3, "outfiles": {"g4bl": "nts.owner.T.version.sequencer.root"}}
    assert "g4bl_params" not in nersc_cnf.jobpars(owner="u", tag="T", dsconf="e", main_input="M",
                                                   events_per_job=1, njobs=1, params={})


def test_build_cnf_layout_and_vcs_exclusion(deck, tmp_path):
    jp = nersc_cnf.jobpars(owner="u", tag="T", dsconf="e470313", main_input="Mu2E.in",
                           events_per_job=10, njobs=3, params={})
    out = nersc_cnf.build_cnf(deck, jp, tmp_path / "cnf.u.T.e470313.0.tar")
    with tarfile.open(out) as t:
        names = sorted(t.getnames())
        assert names == ["jobpars.json", "work", "work/Geometry", "work/Geometry/PS.txt", "work/Mu2E.in"]
        assert json.loads(t.extractfile("jobpars.json").read()) == jp


def test_build_cnf_refuses_over_the_upload_cap(deck, tmp_path, monkeypatch):
    (deck / "big.bin").write_bytes(b"x" * 100)
    monkeypatch.setattr(iri, "UPLOAD_MAX", 50)
    jp = nersc_cnf.jobpars(owner="u", tag="T", dsconf="e", main_input="Mu2E.in",
                           events_per_job=1, njobs=1, params={})
    with pytest.raises(nersc_cnf.CnfError, match="exceeds the 50-byte upload cap"):
        nersc_cnf.build_cnf(deck, jp, tmp_path / "c.tar")
    assert not (tmp_path / "c.tar").exists()


def test_build_cnf_never_overwrites(deck, tmp_path):
    jp = nersc_cnf.jobpars(owner="u", tag="T", dsconf="e", main_input="Mu2E.in",
                           events_per_job=1, njobs=1, params={})
    out = nersc_cnf.build_cnf(deck, jp, tmp_path / "c.tar")
    with pytest.raises(nersc_cnf.CnfError, match="exists"):
        nersc_cnf.build_cnf(deck, jp, out)
