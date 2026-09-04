import sys
import types

import pytest

from beamkit import bridge


@pytest.fixture
def fake_prodtools(monkeypatch, tmp_path):
    """Minimal stand-ins for the prodtools modules bridge imports."""
    calls = {}
    tools = types.ModuleType("prodtools_mcp_write.tools")
    def push_cnf(**kw):
        calls["push_cnf"] = kw
        return {"tarball": "cnf.u.T.e470313.0.tar", "datasets": ["nts.u.T.e470313.root"], "campaign_id": 7, "njobs": 3}
    def run_submissions(**kw):
        calls["run_submissions"] = kw
        return {"rc": 0, "needs_attention": False, "campaign_id": kw["campaign_id"], "output": "tick ok"}
    tools.push_cnf = push_cnf
    tools.run_submissions = run_submissions
    runner = types.ModuleType("prodtools_mcp_write.runner")
    runner.REPO_ROOT = str(tmp_path)
    pkg = types.ModuleType("prodtools_mcp_write")
    pkg.tools, pkg.runner = tools, runner
    status = types.ModuleType("prodtools_mcp.tools.status")
    status.campaign_status = lambda **kw: {"called": kw}
    sw = types.ModuleType("utils.samweb_wrapper")
    sw.locate_file = lambda name: "enstore:/x" if name.endswith("e470313.0.tar") else ""
    sw.file_sizes_in_dataset = lambda ds: {"nts.u.T.e470313.00000002.root": 20, "nts.u.T.e470313.00000000.root": 10}
    fr = types.ModuleType("utils.file_resolver")
    fr.dataset_dir = lambda ds, loc: f"/pnfs/{loc}/{ds}" if loc in ("scratch", "disk", "tape") else ""
    jc = types.ModuleType("utils.job_common")
    class Mu2eName:
        def __init__(self, s): self.sequencer = s.split(".")[4]; self.filename = s
        @classmethod
        def parse(cls, s): return cls(s)
        def relpathname(self): return f"aa/bb/{self.filename}"
    jc.Mu2eName = Mu2eName
    for name, mod in {"prodtools_mcp_write": pkg, "prodtools_mcp_write.tools": tools,
                      "prodtools_mcp_write.runner": runner,
                      "prodtools_mcp": types.ModuleType("prodtools_mcp"),
                      "prodtools_mcp.tools": types.ModuleType("prodtools_mcp.tools"),
                      "prodtools_mcp.tools.status": status,
                      "utils": types.ModuleType("utils"), "utils.samweb_wrapper": sw,
                      "utils.file_resolver": fr, "utils.job_common": jc}.items():
        monkeypatch.setitem(sys.modules, name, mod)
    sys.modules["prodtools_mcp.tools"].status = status
    return calls


def test_import_without_prodtools_then_call_raises(monkeypatch):
    for name in list(sys.modules):
        if name.startswith(("prodtools_mcp", "utils")):
            monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(sys, "path", [p for p in sys.path if "prodtools" not in p])
    with pytest.raises(bridge.BridgeError, match="BEAMKIT_PRODTOOLS_ROOT"):
        bridge.tick("self", 1, False)


def test_push_cnf_forwards_as_keywords(fake_prodtools, tmp_path):
    out = bridge.push_cnf(tmp_path / "entry.json", "T", "e470313", 3, "self", False)
    assert out["campaign_id"] == 7
    assert fake_prodtools["push_cnf"] == {"json": str(tmp_path / "entry.json"), "desc": "T", "dsconf": "e470313",
                                          "slice_size": 3, "run_as": "self", "confirm": False}


def test_tick_forwards(fake_prodtools):
    out = bridge.tick("mu2epro", 7, True)
    assert out["rc"] == 0
    assert fake_prodtools["run_submissions"] == {"run_as": "mu2epro", "campaign_id": 7, "confirm": True}


def test_push_file_missing_in_prodtools_is_clear_error(fake_prodtools, tmp_path):
    with pytest.raises(bridge.BridgeError, match="publish=False"):
        bridge.push_file(tmp_path / "f.txt", "scratch", ["a.root"], "self", False)


def test_push_file_present_forwards(fake_prodtools, tmp_path):
    seen = {}
    sys.modules["prodtools_mcp_write.tools"].push_file = lambda **kw: seen.update(kw) or {"name": "ok"}
    assert bridge.push_file(tmp_path / "f.txt", "tape", ("a.root", "b.root"), "self", False) == {"name": "ok"}
    assert seen == {"path": str(tmp_path / "f.txt"), "location": "tape", "parents": ["a.root", "b.root"],
                    "run_as": "self", "confirm": False}


def test_campaign_status_forwards(fake_prodtools):
    assert bridge.campaign_status(7, True) == {"called": {"campaign_id": 7, "mine": True}}


def test_cnf_exists(fake_prodtools):
    assert bridge.cnf_exists("cnf.u.T.e470313.0.tar") is True
    assert bridge.cnf_exists("cnf.u.T.e470313-001.0.tar") is False


def test_dataset_files_sorted_by_index_with_paths(fake_prodtools):
    files = bridge.dataset_files("nts.u.T.e470313.root", "scratch")
    assert files == [
        {"name": "nts.u.T.e470313.00000000.root", "index": 0, "size": 10,
         "path": "/pnfs/scratch/nts.u.T.e470313.root/aa/bb/nts.u.T.e470313.00000000.root"},
        {"name": "nts.u.T.e470313.00000002.root", "index": 2, "size": 20,
         "path": "/pnfs/scratch/nts.u.T.e470313.root/aa/bb/nts.u.T.e470313.00000002.root"},
    ]


def test_dataset_files_unknown_location(fake_prodtools):
    with pytest.raises(bridge.BridgeError, match="location"):
        bridge.dataset_files("nts.u.T.e470313.root", "resilient")


def test_prodtools_info_reports_root_and_commit(fake_prodtools, tmp_path):
    import subprocess
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    info = bridge.prodtools_info()
    assert info["root"] == str(tmp_path) and len(info["commit"]) == 40
