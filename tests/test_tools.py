import json
import subprocess

import pytest

from beamkit import BeamkitError, bridge, identity, paths, records, tools
from beamkit.backends import fermilab as fermilab_backend

SHA = "e470313" + "0" * 33


@pytest.fixture
def deck(tmp_path):
    """A git checkout that inspect_local can read; materialize is patched."""
    d = tmp_path / "deckcache" / SHA[:12]
    d.mkdir(parents=True)
    (d / "Mu2E.in").write_text("param -unset First_Event=1\n")
    return d


@pytest.fixture
def prodtools(fake_prodtools_root, monkeypatch, deck):
    """The real bridge against the fake prodtools servers. Only what is not
    prodtools stays patched: the deck checkout and the account name."""
    from beamkit import decks
    monkeypatch.setattr(decks, "materialize",
                        lambda url, ref, cache: decks.DeckPin(url, ref, SHA, str(deck), True))
    monkeypatch.setattr(identity, "_username", lambda: "u")
    return fake_prodtools_root


def calls(root, tool):
    """The fake's call log for one tool, oldest first."""
    log = root.parent / "calls.jsonl"
    if not log.exists():
        return []
    return [json.loads(l)["args"] for l in log.read_text().splitlines() if json.loads(l)["tool"] == tool]


def knob(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(f"FAKE_PRODTOOLS_{k}", v)
    bridge.reset()


def _run(**kw):
    base = dict(tag="T", deck_ref=SHA, run_as="self", events_per_job=10, njobs=3)
    base.update(kw)
    return tools.run_beamline(**base)


def test_run_beamline_happy_path(prodtools, beamkit_home, monkeypatch):
    knob(monkeypatch, TICK=json.dumps({"output": "a\nb\ntop-up: 1 slice"}))
    out = _run()
    assert out["run_id"] == "T.e470313" and out["state"] == "submitted" and out["fermilab"]["campaign_id"] == 7
    assert out["dsconf"] == "e470313" and out["owner"] == "u" and out["slice_size"] == 3
    assert out["deck"]["sha"] == SHA and out["deck"]["pinned"] is True
    assert out["datasets"] == ["nts.u.T.e470313.root"]
    assert calls(prodtools, "locate_file") == [{"name": "cnf.u.T.e470313.0.tar"}]
    entry_path = beamkit_home / "runs" / "T.e470313" / "entry.json"
    assert calls(prodtools, "push_cnf") == [dict(json=str(entry_path), desc="T", dsconf="e470313",
                                                 slice_size=3, run_as="self", confirm=False)]
    assert calls(prodtools, "run_submissions") == [dict(run_as="self", campaign_id=7, confirm=False)]
    entry = json.loads(entry_path.read_text())[0]
    assert entry["g4bl_dir"] == out["deck"]["dir"] and entry["njobs"] == 3
    saved = records.load("T.e470313", paths.runs_dir())
    assert saved.block.ticks[0]["rc"] == 0 and saved.block.ticks[0]["summary"].endswith("top-up: 1 slice")
    assert saved.block.prodtools == {"root": str(prodtools), "commit": None, "dev_dir": None}


def test_datasets_is_the_resolved_name_not_the_glob(prodtools):
    """prodtools copies the entry's outloc key into outputs[].dataset and
    returns it, so its `datasets` is "nts.*.root" and never a name. The
    record must carry the dataset the run really writes."""
    out = _run(run_as="mu2epro", confirm=True)
    assert out["datasets"] == ["nts.mu2e.T.e470313.root"]
    saved = records.load("T.e470313", paths.runs_dir())
    assert saved.datasets == ["nts.mu2e.T.e470313.root"]


def test_run_beamline_params_reach_entry(prodtools, beamkit_home):
    _run(params={"READ_Beam_File": 1})
    entry = json.loads((beamkit_home / "runs" / "T.e470313" / "entry.json").read_text())[0]
    assert entry["g4bl_params"] == {"READ_Beam_File": 1}


def test_slice_size_default_is_min_njobs_cap(prodtools):
    assert _run(njobs=20000)["slice_size"] == 10000
    assert calls(prodtools, "push_cnf")[0]["slice_size"] == 10000


@pytest.mark.parametrize("bad", [0, 10001, -1, 2.5, True])
def test_slice_size_out_of_range_refused_before_bridge(prodtools, bad):
    with pytest.raises(BeamkitError, match="slice_size"):
        _run(slice_size=bad)
    assert calls(prodtools, "push_cnf") == [] and calls(prodtools, "locate_file") == []


def test_outloc_disk_refused_for_self_before_any_side_effect(prodtools, beamkit_home):
    with pytest.raises(BeamkitError, match="storage.modify"):
        _run(outloc="disk")
    assert calls(prodtools, "locate_file") == [] and calls(prodtools, "push_cnf") == []
    assert not (beamkit_home / "runs").exists()


def test_outloc_disk_allowed_for_mu2epro(prodtools):
    assert _run(outloc="disk", run_as="mu2epro", confirm=True)["outloc"] == "disk"


def test_outloc_unknown_refused(prodtools):
    with pytest.raises(BeamkitError, match="outloc"):
        _run(outloc="resilient")
    assert calls(prodtools, "locate_file") == []


def test_submit_false_creates_only(prodtools):
    out = _run(submit=False)
    assert out["state"] == "created" and out["fermilab"]["campaign_id"] == 7
    assert calls(prodtools, "run_submissions") == []


def test_push_fails_before_sam_reports_the_dsconf_free(prodtools, monkeypatch):
    knob(monkeypatch, FAIL="push_cnf")
    with pytest.raises(BeamkitError, match="is not in SAM.*is free"):
        _run()
    rec = records.load("T.e470313", paths.runs_dir())
    assert rec.state == "enqueue_failed" and rec.block.campaign_id is None
    assert "push_cnf refused by the fake" in rec.error
    assert calls(prodtools, "run_submissions") == []


def test_push_fails_after_the_campaign_was_created_adopts_it(prodtools, monkeypatch):
    """prodtools' _ENQUEUE_RECOVERY: the cnf can be in SAM and the campaign
    created when push_cnf still raises. beamkit used to report only 'the
    dsconf is burned', so the retry allocated -001 and the campaign it had
    just created was orphaned with no beamkit run pointing at it.

    The dsconf-allocation probe (naming.allocate_dsconf, via
    fermilab.taken) and the after-failure probe (fermilab.after_failure)
    ask bridge.cnf_exists the SAME question about the SAME cnf name at two
    different points of one call; CNF_LANDS_ON_FAIL makes the fake's
    push_cnf mark the tarball as landed right before it raises, so the
    first probe still sees 'free' (the mark does not exist yet) and the
    second sees 'landed' -- no client-side probe stub needed."""
    knob(monkeypatch, FAIL="push_cnf", CNF_LANDS_ON_FAIL="1",
        CAMPAIGNS='[{"id": 7, "state": "active", "tarball": "cnf.u.T.e470313.0.tar"}]')
    with pytest.raises(BeamkitError, match="campaign 7 exists.*make_recoveries"):
        _run()
    rec = records.load("T.e470313", paths.runs_dir())
    assert rec.state == "created" and rec.block.campaign_id == 7 and rec.block.tarball == "cnf.u.T.e470313.0.tar"
    assert rec.datasets == ["nts.u.T.e470313.root"] and "push_cnf refused by the fake" in rec.error
    out = tools.make_recoveries("T.e470313", "self")
    assert out["campaign_id"] == 7 and records.load("T.e470313", paths.runs_dir()).state == "submitted"
    # a second run_beamline is a NEW run on the next suffix, not a retry of this one: the
    # landed marker from the first run still makes the base dsconf look taken
    monkeypatch.delenv("FAKE_PRODTOOLS_FAIL", raising=False)
    bridge.reset()
    assert _run()["run_id"] == "T.e470313-001"
    assert records.load("T.e470313", paths.runs_dir()).block.campaign_id == 7


def test_retry_after_failed_push_reuses_the_run_dir(prodtools, monkeypatch):
    """The push failed before SAM, so the dsconf is free and re-allocated; the
    leftover enqueue_failed run dir must not be what refuses the retry."""
    knob(monkeypatch, FAIL="push_cnf")
    with pytest.raises(BeamkitError, match="is free"):
        _run()
    assert records.load("T.e470313", paths.runs_dir()).state == "enqueue_failed"
    monkeypatch.delenv("FAKE_PRODTOOLS_FAIL", raising=False)
    bridge.reset()
    out = _run()
    assert out["run_id"] == "T.e470313" and out["state"] == "submitted" and out["fermilab"]["campaign_id"] == 7


def test_retry_refused_once_a_campaign_exists(prodtools):
    _run(submit=False)
    with pytest.raises(BeamkitError, match="never reused"):
        _run()


def test_mu2epro_with_dev_prodtools_dir_refused_before_any_side_effect(prodtools, monkeypatch, beamkit_home):
    monkeypatch.setenv("BEAMKIT_PRODTOOLS_DIR", "/exp/mu2e/app/users/u/prodtools")
    with pytest.raises(BeamkitError, match="BEAMKIT_PRODTOOLS_DIR"):
        _run(run_as="mu2epro", confirm=True)
    assert calls(prodtools, "locate_file") == [] and not (beamkit_home / "runs").exists()


def test_self_run_ships_the_dev_prodtools_dir_and_records_it(prodtools, monkeypatch):
    monkeypatch.setenv("BEAMKIT_PRODTOOLS_DIR", "/exp/mu2e/app/users/u/prodtools")
    out = _run()
    assert out["state"] == "submitted"
    assert calls(prodtools, "push_cnf")[0]["prodtools_dir"] == "/exp/mu2e/app/users/u/prodtools"
    assert out["fermilab"]["prodtools"]["dev_dir"] == "/exp/mu2e/app/users/u/prodtools"


def test_release_run_sends_no_prodtools_dir(prodtools):
    out = _run()
    assert "prodtools_dir" not in calls(prodtools, "push_cnf")[0] and out["fermilab"]["prodtools"]["dev_dir"] is None


def test_tick_fails_leaves_recoverable_record(prodtools, monkeypatch):
    knob(monkeypatch, FAIL="run_submissions")
    with pytest.raises(BeamkitError, match="make_recoveries"):
        _run()
    rec = records.load("T.e470313", paths.runs_dir())
    assert rec.state == "created" and rec.block.campaign_id == 7
    assert "run_submissions refused by the fake" in rec.error


def test_mu2epro_without_confirm_refused_before_any_side_effect(prodtools, beamkit_home):
    with pytest.raises(BeamkitError, match="confirm"):
        _run(run_as="mu2epro")
    assert calls(prodtools, "locate_file") == [] and not (beamkit_home / "runs").exists()


def test_mu2epro_confirmed_uses_mu2e_owner(prodtools):
    out = _run(run_as="mu2epro", confirm=True)
    assert out["owner"] == "mu2e" and calls(prodtools, "locate_file") == [{"name": "cnf.mu2e.T.e470313.0.tar"}]
    assert calls(prodtools, "push_cnf")[0]["confirm"] is True


def test_deck_dir_self_only(prodtools, deck):
    with pytest.raises(BeamkitError, match="self"):
        _run(deck_ref=None, deck_dir=str(deck), run_as="mu2epro", confirm=True)


def test_deck_dir_requires_git_checkout(prodtools, deck):
    with pytest.raises(BeamkitError, match="git"):
        _run(deck_ref=None, deck_dir=str(deck))


def test_deck_dir_git_checkout_pinned_false(prodtools, deck):
    subprocess.run(["git", "init", "-q", str(deck)], check=True)
    subprocess.run(["git", "-C", str(deck), "-c", "user.name=t", "-c", "user.email=t@t", "add", "."], check=True)
    subprocess.run(["git", "-C", str(deck), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x"], check=True)
    out = _run(deck_ref=None, deck_dir=str(deck))
    assert out["deck"]["pinned"] is False and out["deck"]["dirty"] is False and len(out["dsconf"]) == 7


def test_deck_ref_and_deck_dir_both_is_error(prodtools, deck):
    with pytest.raises(BeamkitError, match="one of"):
        _run(deck_dir=str(deck))


def test_neither_deck_ref_nor_deck_dir_is_error(prodtools):
    with pytest.raises(BeamkitError, match="deck_ref"):
        _run(deck_ref=None)


def test_dsconf_collision_suffix(prodtools, monkeypatch):
    knob(monkeypatch, CNF_EXISTS="cnf.u.T.e470313.0.tar")
    assert _run()["run_id"] == "T.e470313-001"


def test_run_dir_already_present_is_error(prodtools, beamkit_home):
    d = beamkit_home / "runs" / "T.e470313"
    d.mkdir(parents=True)
    (d / "run.json").write_text("{}")
    with pytest.raises(BeamkitError, match="exists"):
        _run()


def test_run_dir_present_without_a_record_is_claimable(prodtools, beamkit_home):
    """A run dir with no run.json corresponds to nothing anywhere else (the
    record is written before any remote effect, see records.claim_run_dir),
    so it is not a collision -- unlike a dir that already carries one."""
    (beamkit_home / "runs" / "T.e470313").mkdir(parents=True)
    out = _run()
    assert out["run_id"] == "T.e470313" and out["state"] == "submitted"


def test_make_recoveries_appends_tick(prodtools, monkeypatch):
    knob(monkeypatch, CAMPAIGNS='[{"id": 7, "state": "active", "tarball": "cnf.u.T.e470313.0.tar"}]')
    _run()
    out = tools.make_recoveries("T.e470313", "self")
    assert out["rc"] == 0 and out["campaign_id"] == 7
    assert len(records.load("T.e470313", paths.runs_dir()).block.ticks) == 2
    assert calls(prodtools, "run_submissions")[-1] == dict(run_as="self", campaign_id=7, confirm=False)


def test_make_recoveries_without_campaign_is_error(prodtools, monkeypatch):
    knob(monkeypatch, FAIL="push_cnf")
    with pytest.raises(BeamkitError):
        _run()
    with pytest.raises(BeamkitError, match="no campaign"):
        tools.make_recoveries("T.e470313", "self")


def test_make_recoveries_unknown_run(prodtools):
    with pytest.raises(BeamkitError, match="no run record"):
        tools.make_recoveries("Nope.1234567", "self")


def test_beamline_status_merges_record_and_campaign(prodtools):
    _run()
    out = tools.beamline_status("T.e470313")
    assert out["record"]["run_id"] == "T.e470313"
    assert out["status"] == {"called": {"campaign_id": 7, "mine": True}}


def test_beamline_status_created_without_campaign(prodtools, monkeypatch):
    knob(monkeypatch, FAIL="push_cnf")
    with pytest.raises(BeamkitError):
        _run()
    out = tools.beamline_status("T.e470313")
    assert out["status"] is None and out["record"]["state"] == "enqueue_failed"


def test_list_beamline_runs(prodtools):
    _run()
    _run(tag="U")
    out = tools.list_beamline_runs()
    assert {r["run_id"] for r in out["runs"]} == {"T.e470313", "U.e470313"}
    assert out["records_dir"] == str(paths.runs_dir())


def test_beamline_outputs(prodtools, monkeypatch):
    monkeypatch.setenv("FAKE_PRODTOOLS_FILES", json.dumps([["nts.u.T.e470313.00000000.root", 1]]))
    bridge.reset()
    _run()
    out = tools.beamline_outputs("T.e470313")
    assert out["dataset"] == "nts.u.T.e470313.root" and out["location"] == "scratch"
    assert out["files"][0]["name"] == "nts.u.T.e470313.00000000.root" and out["n_files"] == 1


def test_get_server_info(prodtools, beamkit_home):
    info = tools.get_server_info()
    assert info["name"] == "beamkit"
    assert info["prodtools"] == {"root": str(prodtools), "commit": None}
    assert info["dev_dir"] is None
    assert info["records_dir"] == str(beamkit_home / "runs") and info["slice_max"] == 10000


def test_run_beamline_refuses_non_dict_params(prodtools):
    with pytest.raises(BeamkitError, match="params"):
        _run(params=[])
    assert calls(prodtools, "push_cnf") == []


def test_tick_needing_attention_is_a_state_not_a_footnote(prodtools, monkeypatch):
    """prodtools rc=2 means held rows or exhausted recoveries. Filing it into
    ticks[] and then writing state="submitted" made a run in trouble list
    exactly like a healthy one."""
    knob(monkeypatch, TICK=json.dumps({"rc": 2, "needs_attention": True, "output": "held: 3\n"}))
    out = _run()
    assert out["state"] == "needs_attention"
    assert [r["run_id"] for r in tools.list_beamline_runs(state="needs_attention")["runs"]] == ["T.e470313"]
    assert tools.list_beamline_runs(state="submitted")["runs"] == []


def test_clean_tick_after_attention_returns_to_submitted(prodtools, monkeypatch):
    knob(monkeypatch, TICK=json.dumps({"rc": 2, "needs_attention": True, "output": ""}))
    _run()
    knob(monkeypatch, TICK=json.dumps({"rc": 0, "needs_attention": False, "output": ""}))
    out = tools.make_recoveries("T.e470313", "self")
    assert out["needs_attention"] is False
    assert records.load("T.e470313", paths.runs_dir()).state == "submitted"


@pytest.mark.parametrize("kw", [dict(events_per_job=0), dict(events_per_job=2.5), dict(njobs=0), dict(njobs=True)])
def test_bad_counts_refused_before_the_deck_fetch_and_the_sam_probe(prodtools, monkeypatch, beamkit_home, kw):
    """events_per_job used to be checked only by compose.entry, after the
    deck was materialized and a cnf_exists probe had hit SAM. Every caller
    input is refused before either."""
    from beamkit import decks
    fetched = []
    monkeypatch.setattr(decks, "materialize", lambda *a: fetched.append(a))
    with pytest.raises(BeamkitError, match=next(iter(kw))):
        _run(**kw)
    assert fetched == [] and calls(prodtools, "locate_file") == []
    assert not (beamkit_home / "runs").exists()


def test_campaign_njobs_disagreeing_with_the_request_is_refused_before_the_tick(prodtools, monkeypatch):
    """push_cnf returns the njobs the campaign actually holds. It was
    discarded, and missing_indices was later computed from the requested
    count. A disagreement is recorded as the campaign's truth and refused
    loudly before anything is submitted; the campaign exists, so
    make_recoveries can still submit it once the cause is understood.

    The fake's push_cnf reads njobs off the entry file it is given, so the
    disagreement is produced by bumping the entry compose.entry writes,
    not by patching bridge."""
    good_entry = fermilab_backend.compose.entry
    monkeypatch.setattr(fermilab_backend.compose, "entry", lambda **kw: dict(good_entry(**kw), njobs=4))
    with pytest.raises(BeamkitError, match="holds 4 jobs.*asked for 3.*make_recoveries"):
        _run()
    rec = records.load("T.e470313", paths.runs_dir())
    assert rec.block.campaign_id == 7 and rec.njobs == 4 and rec.state == "created"
    assert calls(prodtools, "run_submissions") == []


def test_make_recoveries_ticks_the_campaign_while_it_is_active(prodtools, monkeypatch):
    knob(monkeypatch, CAMPAIGNS='[{"id": 7, "state": "active", "tarball": "cnf.u.T.e470313.0.tar"}]')
    _run()
    out = tools.make_recoveries("T.e470313", "self")
    assert out["tick_scope"] == "campaign" and out["campaign_state"] == "active"
    assert calls(prodtools, "run_submissions")[-1]["campaign_id"] == 7
    assert calls(prodtools, "list_campaigns")[-1]["mine"] is True


def test_make_recoveries_uses_the_bare_tick_once_the_campaign_is_complete(prodtools, monkeypatch):
    """prodtools flips a campaign to 'complete' the moment its last slice is
    submitted, while its rows still verify, and refuses a scoped tick on it
    as 'not active'. The bare tick is the only form that reaches those
    rows; its top-up also feeds every other active campaign in the ledger,
    and the result says so."""
    _run()
    knob(monkeypatch, CAMPAIGNS='[{"id": 7, "state": "complete", "tarball": "cnf.u.T.e470313.0.tar"}]')
    out = tools.make_recoveries("T.e470313", "self")
    assert out["tick_scope"] == "ledger" and out["campaign_state"] == "complete"
    assert calls(prodtools, "run_submissions")[-1] == dict(run_as="self", campaign_id=None, confirm=False)
    assert "every active campaign" in out["note"]


def test_make_recoveries_refuses_a_campaign_missing_from_the_ledger(prodtools, monkeypatch):
    _run()
    knob(monkeypatch, CAMPAIGNS="[]")
    with pytest.raises(BeamkitError, match="campaign 7 is not in"):
        tools.make_recoveries("T.e470313", "self")
    assert len(calls(prodtools, "run_submissions")) == 1


def test_make_recoveries_refuses_the_other_identity(prodtools):
    """The ledger consulted follows the record's identity and the tick runs
    as the caller's; the two must be the same account."""
    _run()
    with pytest.raises(BeamkitError, match="run_as='self'"):
        tools.make_recoveries("T.e470313", "mu2epro", confirm=True)
    assert len(calls(prodtools, "run_submissions")) == 1


def test_fermilab_site_refuses_a_walltime(prodtools):
    with pytest.raises(BeamkitError, match="walltime_s applies to site='nersc' only"):
        _run(walltime_s=3600)
    assert calls(prodtools, "push_cnf") == []


def test_submit_run_refuses_a_fermilab_record(prodtools):
    """submit_run dispatches on rec.site; a Fermilab record reaches the
    Fermilab backend's own refusal instead of the NERSC backend at all --
    the Fermilab path submits through make_recoveries."""
    _run()
    with pytest.raises(BeamkitError, match="is a 'fermilab' run; the Fermilab path submits through make_recoveries"):
        tools.submit_run("T.e470313", "self")


def test_get_server_info_reports_backends(prodtools, beamkit_home):
    info = tools.get_server_info()
    assert info["backends"]["fermilab"]["available"] is True
    assert info["backends"]["nersc"]["available"] is False
    assert info["backends"]["nersc"]["config"] == str(beamkit_home / "nersc.toml")
    assert "nersc.toml" in info["backends"]["nersc"]["detail"]


def test_get_server_info_without_prodtools_still_answers(monkeypatch, tmp_path):
    monkeypatch.setenv("BEAMKIT_PRODTOOLS_ROOT", str(tmp_path / "nope"))
    info = tools.get_server_info()
    assert info["backends"]["fermilab"]["available"] is False
    assert "BEAMKIT_PRODTOOLS_ROOT" in info["backends"]["fermilab"]["detail"]
    assert info["prodtools"] is None


def test_get_server_info_spawns_no_prodtools_child(fake_prodtools_root, beamkit_home):
    tools.get_server_info()
    from beamkit import bridge
    assert bridge._servers == {}
