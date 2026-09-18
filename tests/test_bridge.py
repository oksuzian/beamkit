"""bridge.py against the fake prodtools servers spawned from a fake
BEAMKIT_PRODTOOLS_ROOT (see conftest.fake_prodtools_root). Every test here
exercises the real MCP client path; only the far end is canned."""
import json
import os
import subprocess

import pytest

from beamkit import bridge


def calls(root):
    path = root.parent / "calls.jsonl"
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines()]


def test_missing_root_is_a_clear_error(monkeypatch, tmp_path):
    monkeypatch.setenv("BEAMKIT_PRODTOOLS_ROOT", str(tmp_path / "nowhere"))
    bridge.reset()
    ok, detail = bridge.availability()
    assert ok is False and "BEAMKIT_PRODTOOLS_ROOT" in detail and "start_write_mcp.sh" in detail
    with pytest.raises(bridge.BridgeError, match="BEAMKIT_PRODTOOLS_ROOT"):
        bridge.tick("self", 1, False)


def test_default_root_is_the_cvmfs_release(monkeypatch):
    monkeypatch.delenv("BEAMKIT_PRODTOOLS_ROOT", raising=False)
    assert bridge.prodtools_root() == "/cvmfs/mu2e.opensciencegrid.org/bin/prodtools/current"
    assert str(bridge.launcher("write")).endswith("/mcp/scripts/start_write_mcp.sh")
    assert str(bridge.launcher("read")).endswith("/mcp/scripts/start_mcp.sh")


def test_availability_needs_both_launchers(fake_prodtools_root):
    assert bridge.availability() == (True, f"prodtools at {fake_prodtools_root}")
    (fake_prodtools_root / "mcp" / "scripts" / "start_mcp.sh").unlink()
    ok, detail = bridge.availability()
    assert ok is False and "start_mcp.sh" in detail


def _entry_json(tmp_path, njobs=3):
    path = tmp_path / "entry.json"
    path.write_text(json.dumps([{"njobs": njobs}]))
    return path


def test_push_cnf_forwards_as_keywords(fake_prodtools_root, tmp_path):
    entry = _entry_json(tmp_path)
    out = bridge.push_cnf(entry, "T", "e470313", 3, "self", False)
    assert out["campaign_id"] == 7 and out["tarball"] == "cnf.u.T.e470313.0.tar" and out["njobs"] == 3
    assert calls(fake_prodtools_root)[-1] == {
        "tool": "push_cnf", "args": {"json": str(entry), "desc": "T", "dsconf": "e470313",
                                     "slice_size": 3, "run_as": "self", "confirm": False}}


def test_push_cnf_forwards_an_explicit_dev_dir(fake_prodtools_root, tmp_path):
    bridge.push_cnf(_entry_json(tmp_path), "T", "e470313", 3, "self", False,
                    prodtools_dir="/exp/mu2e/app/users/u/prodtools")
    assert calls(fake_prodtools_root)[-1]["args"]["prodtools_dir"] == "/exp/mu2e/app/users/u/prodtools"


def test_push_cnf_sends_no_prodtools_dir_keyword_for_the_release(fake_prodtools_root, tmp_path, monkeypatch):
    """None means the cvmfs release: the keyword is absent, not None, and the
    environment is not consulted here -- identity reads it, once."""
    monkeypatch.setenv("BEAMKIT_PRODTOOLS_DIR", "/exp/mu2e/app/users/u/prodtools")
    bridge.push_cnf(_entry_json(tmp_path), "T", "e470313", 3, "self", False)
    assert "prodtools_dir" not in calls(fake_prodtools_root)[-1]["args"]


def test_tick_forwards(fake_prodtools_root):
    out = bridge.tick("mu2epro", 7, True)
    assert out["rc"] == 0
    assert calls(fake_prodtools_root)[-1] == {"tool": "run_submissions",
                                              "args": {"run_as": "mu2epro", "campaign_id": 7, "confirm": True}}


def test_write_failure_is_the_servers_own_message(fake_prodtools_root, monkeypatch):
    monkeypatch.setenv("FAKE_PRODTOOLS_FAIL", "run_submissions")
    bridge.reset()
    with pytest.raises(bridge.BridgeError) as ei:
        bridge.tick("self", 7, False)
    assert str(ei.value) == "run_submissions refused by the fake; remedy: try the other thing"


def test_read_error_envelope_becomes_bridge_error(fake_prodtools_root, monkeypatch):
    monkeypatch.setenv("FAKE_PRODTOOLS_FAIL", "campaign_status")
    bridge.reset()
    with pytest.raises(bridge.BridgeError, match=r"campaign_status refused by the fake.*\(internal\)"):
        bridge.campaign_status(7, True)


def test_push_file_available_follows_the_listing(fake_prodtools_root, monkeypatch):
    assert bridge.push_file_available() is True
    monkeypatch.setenv("FAKE_PRODTOOLS_OMIT", "push_file")
    bridge.reset()
    assert bridge.push_file_available() is False


def test_push_file_missing_in_prodtools_is_clear_error(fake_prodtools_root, monkeypatch, tmp_path):
    """_call refuses every absent tool by name; publishing.check_ready is
    the boundary probe that keeps a publish=True from getting this far."""
    monkeypatch.setenv("FAKE_PRODTOOLS_OMIT", "push_file")
    bridge.reset()
    with pytest.raises(bridge.BridgeError, match="no 'push_file' tool"):
        bridge.push_file(tmp_path / "f.txt", "scratch", ["a.root"], "self", False)


def test_push_file_present_forwards(fake_prodtools_root, tmp_path):
    assert bridge.push_file(tmp_path / "f.txt", "tape", ("a.root", "b.root"), "self", False) == {"name": "ok"}
    assert calls(fake_prodtools_root)[-1]["args"] == {"path": str(tmp_path / "f.txt"), "location": "tape",
                                                      "parents": ["a.root", "b.root"], "run_as": "self",
                                                      "confirm": False}


def test_campaign_status_forwards(fake_prodtools_root):
    assert bridge.campaign_status(7, True) == {"called": {"campaign_id": 7, "mine": True}}


def test_campaigns_is_the_ledger_only_listing(fake_prodtools_root):
    assert bridge.campaigns(mine=True) == [{"id": 7, "state": "complete", "tarball": "cnf.u.T.e470313.0.tar"}]
    assert calls(fake_prodtools_root)[-1]["args"] == {"state": None, "mine": True}


def test_cnf_exists(fake_prodtools_root, monkeypatch):
    monkeypatch.setenv("FAKE_PRODTOOLS_CNF_EXISTS", "cnf.u.T.e470313.0.tar")
    bridge.reset()
    assert bridge.cnf_exists("cnf.u.T.e470313.0.tar") is True
    assert bridge.cnf_exists("cnf.u.T.e470313-001.0.tar") is False


def test_dataset_files_sorted_by_index_with_paths(fake_prodtools_root):
    files = bridge.dataset_files("nts.u.T.e470313.root", "scratch")
    assert files == [
        {"name": "nts.u.T.e470313.00000000.root", "index": 0, "size": 10,
         "path": "/pnfs/scratch/nts.u.T.e470313.root/aa/bb/nts.u.T.e470313.00000000.root"},
        {"name": "nts.u.T.e470313.00000002.root", "index": 2, "size": 20,
         "path": "/pnfs/scratch/nts.u.T.e470313.root/aa/bb/nts.u.T.e470313.00000002.root"},
    ]


def test_dataset_files_unknown_location(fake_prodtools_root):
    with pytest.raises(bridge.BridgeError, match="location"):
        bridge.dataset_files("nts.u.T.e470313.root", "resilient")


def test_dataset_files_refuses_composite_sequencer(fake_prodtools_root, monkeypatch):
    monkeypatch.setenv("FAKE_PRODTOOLS_FILES", json.dumps([["nts.u.T.e470313.001430_00000052.root", 5]]))
    bridge.reset()
    with pytest.raises(bridge.BridgeError, match="001430_00000052"):
        bridge.dataset_files("nts.u.T.e470313.root", "scratch")


def test_missing_new_tool_names_the_prodtools_needed(fake_prodtools_root, monkeypatch):
    monkeypatch.setenv("FAKE_PRODTOOLS_OMIT", "locate_file,dataset_files")
    bridge.reset()
    with pytest.raises(bridge.BridgeError, match="no 'locate_file' tool.*locate_file and dataset_files"):
        bridge.cnf_exists("cnf.u.T.e470313.0.tar")
    with pytest.raises(bridge.BridgeError, match="no 'dataset_files' tool"):
        bridge.dataset_files("nts.u.T.e470313.root", "scratch")


def test_children_are_lazy_and_reused(fake_prodtools_root):
    assert bridge._servers == {}
    bridge.campaign_status(7, False)
    assert set(bridge._servers) == {"read"} and bridge._servers["read"].started
    bridge.tick("self", 7, False)
    assert set(bridge._servers) == {"read", "write"}
    first = bridge._servers["read"]
    bridge.campaigns(mine=False)
    assert bridge._servers["read"] is first


def test_child_death_respawns_on_the_next_call(fake_prodtools_root, monkeypatch):
    monkeypatch.setenv("FAKE_PRODTOOLS_DIE", "list_campaigns")
    monkeypatch.setenv("FAKE_PRODTOOLS_CNF_EXISTS", "cnf.u.T.e470313.0.tar")
    bridge.reset()
    with pytest.raises(bridge.BridgeError, match="exited during list_campaigns"):
        bridge.campaigns(mine=True)
    assert bridge.cnf_exists("cnf.u.T.e470313.0.tar") is True


def test_child_that_will_not_start_reports_its_stderr(fake_prodtools_root, monkeypatch):
    monkeypatch.setenv("FAKE_PRODTOOLS_NO_START", "1")
    bridge.reset()
    with pytest.raises(bridge.BridgeError, match="did not start.*refusing to start"):
        bridge.campaigns(mine=True)


def test_prodtools_info_reports_root_and_commit(fake_prodtools_root):
    subprocess.run(["git", "init", "-q", str(fake_prodtools_root)], check=True)
    subprocess.run(["git", "-C", str(fake_prodtools_root), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    info = bridge.prodtools_info()
    assert info["root"] == str(fake_prodtools_root) and len(info["commit"]) == 40


def test_prodtools_info_without_git_reports_commit_none(fake_prodtools_root):
    assert bridge.prodtools_info() == {"root": str(fake_prodtools_root), "commit": None}


def test_prodtools_info_spawns_nothing(fake_prodtools_root):
    bridge.prodtools_info()
    bridge.availability()
    assert bridge._servers == {}


@pytest.mark.parametrize("tool, call", [
    ("list_campaigns", lambda: bridge.campaigns(mine=True)),
    ("locate_file", lambda: bridge.cnf_exists("cnf.u.T.e470313.0.tar")),
    ("dataset_files", lambda: bridge.dataset_files("nts.u.T.e470313.root", "scratch")),
])
def test_missing_result_key_is_a_bridge_error(fake_prodtools_root, monkeypatch, tool, call):
    monkeypatch.setenv("FAKE_PRODTOOLS_SHAPE", tool)
    bridge.reset()
    with pytest.raises(bridge.BridgeError, match="returned no"):
        call()


def test_calls_table_covers_every_remote_call():
    assert set(bridge.CALLS) == {"push_cnf", "run_submissions", "push_file", "campaign_status",
                                 "list_campaigns", "locate_file", "dataset_files"}
    kind, always, optional = bridge.CALLS["push_cnf"]
    assert kind == "write" and "json" in always and optional == frozenset({"prodtools_dir"})
