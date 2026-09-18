"""after_failure (what a failed push left behind) and the publish step of
make_beamfile, tested without building anything. The only file that patches
bridge; everything else drives the fake prodtools servers."""
import pytest

from beamkit import BeamkitError, bridge, identity, records
from beamkit.backends import fermilab


def _rec(state="enqueue_failed"):
    return records.RunRecord(run_id="T.e470313", tag="T", dsconf="e470313", owner="u", run_as="self", deck={},
                             params={}, events_per_job=10, njobs=3, outloc="scratch", slice_size=3, state=state,
                             site="fermilab", block=fermilab.Block(prodtools={}))


IDENT = identity.Identity(run_as="self", owner="u", dev_dir=None)


def test_not_in_sam_means_the_dsconf_is_free(monkeypatch):
    monkeypatch.setattr(bridge, "cnf_exists", lambda n: False)
    assert "dsconf 'e470313' is free" in fermilab.after_failure(_rec(), IDENT)


def test_in_sam_without_campaign_means_burned(monkeypatch):
    monkeypatch.setattr(bridge, "cnf_exists", lambda n: True)
    monkeypatch.setattr(bridge, "campaigns", lambda mine: [])
    assert "is burned and the next call allocates the next suffix" in fermilab.after_failure(_rec(), IDENT)


def test_in_sam_with_campaign_is_adopted(monkeypatch):
    monkeypatch.setattr(bridge, "cnf_exists", lambda n: True)
    monkeypatch.setattr(bridge, "campaigns",
                        lambda mine: [{"id": 9, "state": "active", "tarball": "cnf.u.T.e470313.0.tar"}])
    rec = _rec()
    out = fermilab.after_failure(rec, IDENT)
    assert rec.block.campaign_id == 9 and rec.state == "created" and "campaign 9 exists" in out


def test_probe_failures_say_so(monkeypatch):
    monkeypatch.setattr(bridge, "cnf_exists", lambda n: (_ for _ in ()).throw(bridge.BridgeError("sam down")))
    assert "could not be determined" in fermilab.after_failure(_rec(), IDENT)
    monkeypatch.setattr(bridge, "cnf_exists", lambda n: True)
    monkeypatch.setattr(bridge, "campaigns", lambda mine: (_ for _ in ()).throw(bridge.BridgeError("locked")))
    assert "the ledger could not be read" in fermilab.after_failure(_rec(), IDENT)


# --- the publish step: discard only what THIS call created

@pytest.fixture
def built(tmp_path):
    out = tmp_path / "T.e470313.bm.txt"
    out.write_text("#BLTrackFile\nrow\n")
    return out


def test_publish_links_to_the_sam_name_and_pushes(built, tmp_path):
    calls = []
    staged = tmp_path / "etc.u.TBeam-bm.e470313.0.txt"
    fermilab._publish(built, staged, "scratch", ["a.root", "b.root"], "self", False,
                      push=lambda *a: calls.append(a))
    assert calls == [(staged, "scratch", ["a.root", "b.root"], "self", False)]
    assert staged.stat().st_ino == built.stat().st_ino


def test_push_failure_discards_link_and_file(built, tmp_path):
    staged = tmp_path / "etc.u.TBeam-bm.e470313.0.txt"

    def down(*a):
        raise RuntimeError("push down")
    with pytest.raises(BeamkitError, match="discarded.*retried.*push down"):
        fermilab._publish(built, staged, "scratch", [], "self", False, push=down)
    assert not staged.exists() and not built.exists()


def test_link_collision_keeps_the_file_this_call_did_not_create(built, tmp_path):
    staged = tmp_path / "etc.u.TBeam-bm.e470313.0.txt"
    staged.write_text("published earlier\n")
    calls = []
    with pytest.raises(BeamkitError, match="discarded"):
        fermilab._publish(built, staged, "scratch", [], "self", False, push=lambda *a: calls.append(a))
    assert calls == [] and staged.read_text() == "published earlier\n" and not built.exists()


def test_publish_ready_refuses_an_unknown_location():
    with pytest.raises(BeamkitError, match="location"):
        fermilab._publish_ready("resilient")


def test_publish_ready_needs_push_file(monkeypatch):
    monkeypatch.setattr(bridge, "push_file_available", lambda: False)
    with pytest.raises(BeamkitError, match="publish=False"):
        fermilab._publish_ready("scratch")
    monkeypatch.setattr(bridge, "push_file_available",
                        lambda: (_ for _ in ()).throw(bridge.BridgeError("prodtools is not importable here")))
    with pytest.raises(bridge.BridgeError, match="not importable"):
        fermilab._publish_ready("scratch")
    monkeypatch.setattr(bridge, "push_file_available", lambda: True)
    fermilab._publish_ready("tape")
