"""after_failure: what a failed push left behind. The only file that
patches bridge; everything else drives the fake prodtools servers."""
import pytest

from beamkit import bridge, identity, records
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
