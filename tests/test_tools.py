import json
import subprocess
from pathlib import Path

import pytest

from beamkit import tools, records, paths

SHA = "e470313" + "0" * 33


@pytest.fixture
def deck(tmp_path):
    """A git checkout that inspect_local can read; materialize is patched."""
    d = tmp_path / "deckcache" / SHA[:12]
    d.mkdir(parents=True)
    (d / "Mu2E.in").write_text("param -unset First_Event=1\n")
    return d


@pytest.fixture
def fake_bridge(monkeypatch, deck):
    calls = {"push_cnf": [], "tick": [], "cnf_exists": []}
    def push_cnf(json_path, desc, dsconf, slice_size, run_as, confirm):
        calls["push_cnf"].append(dict(json_path=str(json_path), desc=desc, dsconf=dsconf,
                                      slice_size=slice_size, run_as=run_as, confirm=confirm))
        # exactly what prodtools returns: the entry's outloc key, a glob
        return {"tarball": f"cnf.u.{desc}.{dsconf}.0.tar", "datasets": ["nts.*.root"],
                "campaign_id": 7, "njobs": 3}
    def tick(run_as, campaign_id, confirm):
        calls["tick"].append(dict(run_as=run_as, campaign_id=campaign_id, confirm=confirm))
        return {"rc": 0, "needs_attention": False, "campaign_id": campaign_id, "output": "a\nb\ntop-up: 1 slice\n"}
    def cnf_exists(name):
        calls["cnf_exists"].append(name)
        return False
    monkeypatch.setattr(tools.bridge, "push_cnf", push_cnf)
    monkeypatch.setattr(tools.bridge, "tick", tick)
    monkeypatch.setattr(tools.bridge, "cnf_exists", cnf_exists)
    monkeypatch.setattr(tools.bridge, "prodtools_info", lambda: {"root": "/pt", "commit": "c" * 40, "dev_dir": None})
    monkeypatch.setattr(tools.bridge, "campaign_status", lambda campaign_id, mine: {"campaign_id": campaign_id, "mine": mine})
    monkeypatch.setattr(tools.bridge, "dataset_files", lambda ds, loc: [{"name": f"{ds[:-5]}.00000000.root", "index": 0, "size": 1, "path": f"/pnfs/{loc}/x"}])
    from beamkit import decks
    monkeypatch.setattr(tools.decks, "materialize",
                        lambda url, ref, cache: decks.DeckPin(url, ref, SHA, str(deck), True))
    monkeypatch.setattr(tools.naming, "owner_for", lambda run_as: "u" if run_as == "self" else "mu2e")
    return calls


def _run(**kw):
    base = dict(tag="T", deck_ref=SHA, run_as="self", events_per_job=10, njobs=3)
    base.update(kw)
    return tools.run_beamline(**base)


def test_run_beamline_happy_path(fake_bridge, beamkit_home):
    out = _run()
    assert out["run_id"] == "T.e470313" and out["state"] == "submitted" and out["campaign_id"] == 7
    assert out["dsconf"] == "e470313" and out["owner"] == "u" and out["slice_size"] == 3
    assert out["deck"]["sha"] == SHA and out["deck"]["pinned"] is True
    assert out["datasets"] == ["nts.u.T.e470313.root"] and out["prodtools_datasets"] == ["nts.*.root"]
    assert fake_bridge["cnf_exists"] == ["cnf.u.T.e470313.0.tar"]
    entry_path = beamkit_home / "runs" / "T.e470313" / "entry.json"
    assert fake_bridge["push_cnf"] == [dict(json_path=str(entry_path), desc="T", dsconf="e470313",
                                            slice_size=3, run_as="self", confirm=False)]
    assert fake_bridge["tick"] == [dict(run_as="self", campaign_id=7, confirm=False)]
    entry = json.loads(entry_path.read_text())[0]
    assert entry["g4bl_dir"] == out["deck"]["dir"] and entry["njobs"] == 3
    saved = records.load("T.e470313", paths.runs_dir())
    assert saved.ticks[0]["rc"] == 0 and saved.ticks[0]["summary"].endswith("top-up: 1 slice")
    assert saved.prodtools == {"root": "/pt", "commit": "c" * 40, "dev_dir": None}


def test_datasets_is_the_resolved_name_not_the_glob(fake_bridge):
    """prodtools copies the entry's outloc key into outputs[].dataset and
    returns it, so its `datasets` is "nts.*.root" and never a name. The
    record must carry the dataset the run really writes."""
    out = _run(run_as="mu2epro", confirm=True)
    assert out["datasets"] == ["nts.mu2e.T.e470313.root"]
    assert out["prodtools_datasets"] == ["nts.*.root"]
    saved = records.load("T.e470313", paths.runs_dir())
    assert saved.datasets == ["nts.mu2e.T.e470313.root"] and saved.prodtools_datasets == ["nts.*.root"]


def test_run_beamline_params_reach_entry(fake_bridge, beamkit_home):
    _run(params={"READ_Beam_File": 1})
    entry = json.loads((beamkit_home / "runs" / "T.e470313" / "entry.json").read_text())[0]
    assert entry["g4bl_params"] == {"READ_Beam_File": 1}


def test_slice_size_default_is_min_njobs_cap(fake_bridge):
    assert _run(njobs=20000)["slice_size"] == 10000
    assert fake_bridge["push_cnf"][0]["slice_size"] == 10000


@pytest.mark.parametrize("bad", [0, 10001, -1, 2.5, True])
def test_slice_size_out_of_range_refused_before_bridge(fake_bridge, bad):
    with pytest.raises(tools.ToolError, match="slice_size"):
        _run(slice_size=bad)
    assert fake_bridge["push_cnf"] == [] and fake_bridge["cnf_exists"] == []


def test_outloc_disk_refused_for_self_before_any_side_effect(fake_bridge, beamkit_home):
    with pytest.raises(tools.ToolError, match="storage.modify"):
        _run(outloc="disk")
    assert fake_bridge["cnf_exists"] == [] and fake_bridge["push_cnf"] == []
    assert not (beamkit_home / "runs").exists()


def test_outloc_disk_allowed_for_mu2epro(fake_bridge):
    assert _run(outloc="disk", run_as="mu2epro", confirm=True)["outloc"] == "disk"


def test_outloc_unknown_refused(fake_bridge):
    with pytest.raises(tools.ToolError, match="outloc"):
        _run(outloc="resilient")
    assert fake_bridge["cnf_exists"] == []


def test_submit_false_creates_only(fake_bridge):
    out = _run(submit=False)
    assert out["state"] == "created" and out["campaign_id"] == 7 and fake_bridge["tick"] == []


def test_push_fails_before_sam_reports_the_dsconf_free(fake_bridge, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("json2jobdef --prod --enqueue failed (rc=1)")
    monkeypatch.setattr(tools.bridge, "push_cnf", boom)
    with pytest.raises(tools.ToolError, match="is not in SAM.*is free"):
        _run()
    rec = records.load("T.e470313", paths.runs_dir())
    assert rec.state == "enqueue_failed" and rec.campaign_id is None and "rc=1" in rec.error
    assert fake_bridge["tick"] == []


def test_push_fails_after_sam_reports_the_dsconf_burned(fake_bridge, monkeypatch):
    seen = []
    def cnf_exists(name):
        seen.append(name)
        return len(seen) > 1  # free when allocated, in SAM when probed after the failure
    monkeypatch.setattr(tools.bridge, "cnf_exists", cnf_exists)
    monkeypatch.setattr(tools.bridge, "push_cnf",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("rc=1 after the push")))
    with pytest.raises(tools.ToolError, match="is in SAM.*burned"):
        _run()
    assert seen == ["cnf.u.T.e470313.0.tar", "cnf.u.T.e470313.0.tar"]


def test_push_fails_and_sam_probe_fails_says_so(fake_bridge, monkeypatch):
    from beamkit import bridge as _bridge
    seen = []
    def cnf_exists(name):
        seen.append(name)
        if len(seen) > 1:
            raise _bridge.BridgeError("samweb down")
        return False
    monkeypatch.setattr(tools.bridge, "cnf_exists", cnf_exists)
    monkeypatch.setattr(tools.bridge, "push_cnf",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("rc=1")))
    with pytest.raises(tools.ToolError, match="could not be determined.*samweb down"):
        _run()


def test_retry_after_failed_push_reuses_the_run_dir(fake_bridge, monkeypatch):
    """The push failed before SAM, so the dsconf is free and re-allocated; the
    leftover enqueue_failed run dir must not be what refuses the retry."""
    good = tools.bridge.push_cnf
    state = {"fail": True}
    def flaky(*a, **k):
        if state["fail"]:
            raise RuntimeError("push_cnf refused prodtools_dir for run_as='mu2epro'")
        return good(*a, **k)
    monkeypatch.setattr(tools.bridge, "push_cnf", flaky)
    with pytest.raises(tools.ToolError, match="is free"):
        _run()
    assert records.load("T.e470313", paths.runs_dir()).state == "enqueue_failed"
    state["fail"] = False
    out = _run()
    assert out["run_id"] == "T.e470313" and out["state"] == "submitted" and out["campaign_id"] == 7


def test_retry_refused_once_a_campaign_exists(fake_bridge):
    _run(submit=False)
    with pytest.raises(tools.ToolError, match="never reused"):
        _run()


def test_mu2epro_with_dev_prodtools_dir_refused_before_any_side_effect(fake_bridge, monkeypatch, beamkit_home):
    monkeypatch.setenv("BEAMKIT_PRODTOOLS_DIR", "/exp/mu2e/app/users/u/prodtools")
    with pytest.raises(tools.ToolError, match="BEAMKIT_PRODTOOLS_DIR"):
        _run(run_as="mu2epro", confirm=True)
    assert fake_bridge["cnf_exists"] == [] and not (beamkit_home / "runs").exists()


def test_self_run_still_accepts_dev_prodtools_dir(fake_bridge, monkeypatch):
    monkeypatch.setenv("BEAMKIT_PRODTOOLS_DIR", "/exp/mu2e/app/users/u/prodtools")
    assert _run()["state"] == "submitted"


def test_tick_fails_leaves_recoverable_record(fake_bridge, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("submissions run failed (rc=1)")
    monkeypatch.setattr(tools.bridge, "tick", boom)
    with pytest.raises(tools.ToolError, match="make_recoveries"):
        _run()
    rec = records.load("T.e470313", paths.runs_dir())
    assert rec.state == "created" and rec.campaign_id == 7 and "rc=1" in rec.error


def test_mu2epro_without_confirm_refused_before_any_side_effect(fake_bridge, beamkit_home):
    with pytest.raises(tools.ToolError, match="confirm"):
        _run(run_as="mu2epro")
    assert fake_bridge["cnf_exists"] == [] and not (beamkit_home / "runs").exists()


def test_mu2epro_confirmed_uses_mu2e_owner(fake_bridge):
    out = _run(run_as="mu2epro", confirm=True)
    assert out["owner"] == "mu2e" and fake_bridge["cnf_exists"] == ["cnf.mu2e.T.e470313.0.tar"]
    assert fake_bridge["push_cnf"][0]["confirm"] is True


def test_deck_dir_self_only(fake_bridge, deck):
    with pytest.raises(tools.ToolError, match="self"):
        _run(deck_ref=None, deck_dir=str(deck), run_as="mu2epro", confirm=True)


def test_deck_dir_requires_git_checkout(fake_bridge, deck):
    with pytest.raises(tools.ToolError, match="git"):
        _run(deck_ref=None, deck_dir=str(deck))


def test_deck_dir_git_checkout_pinned_false(fake_bridge, deck):
    subprocess.run(["git", "init", "-q", str(deck)], check=True)
    subprocess.run(["git", "-C", str(deck), "-c", "user.name=t", "-c", "user.email=t@t", "add", "."], check=True)
    subprocess.run(["git", "-C", str(deck), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x"], check=True)
    out = _run(deck_ref=None, deck_dir=str(deck))
    assert out["deck"]["pinned"] is False and out["deck"]["dirty"] is False and len(out["dsconf"]) == 7


def test_deck_ref_and_deck_dir_both_is_error(fake_bridge, deck):
    with pytest.raises(tools.ToolError, match="one of"):
        _run(deck_dir=str(deck))


def test_neither_deck_ref_nor_deck_dir_is_error(fake_bridge):
    with pytest.raises(tools.ToolError, match="deck_ref"):
        _run(deck_ref=None)


def test_dsconf_collision_suffix(fake_bridge, monkeypatch):
    monkeypatch.setattr(tools.bridge, "cnf_exists", lambda n: n == "cnf.u.T.e470313.0.tar")
    assert _run()["run_id"] == "T.e470313-001"


def test_run_dir_already_present_is_error(fake_bridge, beamkit_home):
    (beamkit_home / "runs" / "T.e470313").mkdir(parents=True)
    with pytest.raises(tools.ToolError, match="exists"):
        _run()


def test_make_recoveries_appends_tick(fake_bridge):
    _run()
    out = tools.make_recoveries("T.e470313", "self")
    assert out["rc"] == 0 and out["campaign_id"] == 7 and out["ledger_wide"] is True
    assert len(records.load("T.e470313", paths.runs_dir()).ticks) == 2
    assert fake_bridge["tick"][-1] == dict(run_as="self", campaign_id=7, confirm=False)


def test_make_recoveries_without_campaign_is_error(fake_bridge, monkeypatch):
    monkeypatch.setattr(tools.bridge, "push_cnf", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(tools.ToolError):
        _run()
    with pytest.raises(tools.ToolError, match="no campaign"):
        tools.make_recoveries("T.e470313", "self")


def test_make_recoveries_unknown_run(fake_bridge):
    with pytest.raises(tools.ToolError, match="no run record"):
        tools.make_recoveries("Nope.1234567", "self")


def test_beamline_status_merges_record_and_campaign(fake_bridge):
    _run()
    out = tools.beamline_status("T.e470313")
    assert out["record"]["run_id"] == "T.e470313"
    assert out["campaign"] == {"campaign_id": 7, "mine": True}


def test_beamline_status_created_without_campaign(fake_bridge, monkeypatch):
    monkeypatch.setattr(tools.bridge, "push_cnf", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(tools.ToolError):
        _run()
    out = tools.beamline_status("T.e470313")
    assert out["campaign"] is None and out["record"]["state"] == "enqueue_failed"


def test_list_beamline_runs(fake_bridge):
    _run()
    _run(tag="U")
    out = tools.list_beamline_runs()
    assert {r["run_id"] for r in out["runs"]} == {"T.e470313", "U.e470313"}
    assert out["records_dir"] == str(paths.runs_dir())


def test_beamline_outputs(fake_bridge):
    _run()
    out = tools.beamline_outputs("T.e470313")
    assert out["dataset"] == "nts.u.T.e470313.root" and out["location"] == "scratch"
    assert out["files"][0]["name"] == "nts.u.T.e470313.00000000.root" and out["n_files"] == 1


def test_get_server_info(fake_bridge, beamkit_home):
    info = tools.get_server_info()
    assert info["name"] == "beamkit" and info["prodtools"]["commit"] == "c" * 40
    assert info["prodtools"]["dev_dir"] is None
    assert info["records_dir"] == str(beamkit_home / "runs") and info["slice_max"] == 10000


def test_run_beamline_refuses_non_dict_params(fake_bridge):
    with pytest.raises(tools.ToolError, match="params"):
        _run(params=[])
    assert fake_bridge["push_cnf"] == []


def test_tick_needing_attention_is_a_state_not_a_footnote(fake_bridge, monkeypatch):
    """prodtools rc=2 means held rows or exhausted recoveries. Filing it into
    ticks[] and then writing state="submitted" made a run in trouble list
    exactly like a healthy one."""
    monkeypatch.setattr(tools.bridge, "tick", lambda run_as, campaign_id, confirm:
                        {"rc": 2, "needs_attention": True, "campaign_id": campaign_id, "output": "held: 3\n"})
    out = _run()
    assert out["state"] == "needs_attention"
    assert [r["run_id"] for r in tools.list_beamline_runs(state="needs_attention")["runs"]] == ["T.e470313"]
    assert tools.list_beamline_runs(state="submitted")["runs"] == []


def test_clean_tick_after_attention_returns_to_submitted(fake_bridge, monkeypatch):
    monkeypatch.setattr(tools.bridge, "tick", lambda run_as, campaign_id, confirm:
                        {"rc": 2, "needs_attention": True, "campaign_id": campaign_id, "output": ""})
    _run()
    monkeypatch.setattr(tools.bridge, "tick", lambda run_as, campaign_id, confirm:
                        {"rc": 0, "needs_attention": False, "campaign_id": campaign_id, "output": ""})
    out = tools.make_recoveries("T.e470313", "self")
    assert out["needs_attention"] is False
    assert records.load("T.e470313", paths.runs_dir()).state == "submitted"
