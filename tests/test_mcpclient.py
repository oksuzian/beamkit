"""StdioServer against tests/fake_prodtools_mcp.py as a real child process."""
import os
import sys
import time

import pytest

from beamkit import mcpclient
from beamkit.mcpclient import McpClientError, McpToolError, StdioServer

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fake(role, **env_extra):
    env = dict(os.environ, PYTHONPATH=REPO, **{k: str(v) for k, v in env_extra.items()})
    return StdioServer(f"fake-{role}", sys.executable, ["-m", "tests.fake_prodtools_mcp", role],
                       env=env, start_timeout=60)


def _fake_prodtools_pids():
    """PIDs of live `-m tests.fake_prodtools_mcp <role>` processes, found by an
    exact argv match. A substring search over the raw /proc/*/cmdline text
    would also match the shell this suite itself runs under (its command
    line, quoted whole, contains this module name too); matching against the
    parsed argv list only catches an actual `python -m tests.fake_prodtools_mcp
    ...` child."""
    pids = set()
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as fh:
                argv = fh.read().decode("utf-8", "replace").split("\0")
        except OSError:
            continue
        if "tests.fake_prodtools_mcp" in argv:
            pids.add(int(entry))
    return pids


def _assert_no_leaked_children(before, timeout=5):
    """Regression guard for the orphan-child bug fixed in _teardown: any
    fake-prodtools pid not present in `before` must be gone shortly after the
    StdioServer.close() that already ran. A bounded poll -- never an
    unbounded wait -- gives the OS a moment to finish reaping a just-killed
    process."""
    deadline = time.monotonic() + timeout
    leaked = _fake_prodtools_pids() - before
    while leaked and time.monotonic() < deadline:
        time.sleep(0.2)
        leaked = _fake_prodtools_pids() - before
    assert not leaked, f"leaked fake prodtools child process(es): {sorted(leaked)}"


@pytest.fixture
def write():
    s = fake("write")
    yield s
    s.close()


@pytest.fixture
def read():
    s = fake("read")
    yield s
    s.close()


def test_start_lists_tools_and_call_returns_the_dict(write):
    assert not write.started
    write.start()
    assert write.started and write.has_tool("push_cnf") and not write.has_tool("campaign_status")
    out = write.call("run_submissions", run_as="self", campaign_id=7, confirm=False)
    assert out == {"rc": 0, "needs_attention": False, "campaign_id": 7, "output": "tick ok"}


def test_call_starts_lazily(read):
    out = read.call("locate_file", name="cnf.u.T.e470313.0.tar")
    assert read.started and out["exists"] is True


def test_child_gets_the_parents_environment():
    s = fake("read", FAKE_PRODTOOLS_MARK="hello-child")
    try:
        assert s.call("env_probe") == {"mark": "hello-child"}
    finally:
        s.close()


def test_raising_tool_is_a_tool_error_with_the_servers_message():
    s = fake("write", FAKE_PRODTOOLS_FAIL="push_cnf")
    try:
        with pytest.raises(McpToolError) as ei:
            s.call("push_cnf", json="/tmp/e.json", desc="T", dsconf="e470313", slice_size=1,
                   run_as="self", confirm=False)
        assert ei.value.tool == "push_cnf" and ei.value.server == "fake-write"
        assert str(ei.value) == "push_cnf refused by the fake; remedy: try the other thing"
        assert not str(ei.value).startswith("Error executing tool")
    finally:
        s.close()


def test_unknown_tool_is_a_client_error(read):
    with pytest.raises(McpClientError, match="no tool 'nope'"):
        read.call("nope")


def test_child_death_closes_and_next_call_respawns():
    before = _fake_prodtools_pids()
    s = fake("read", FAKE_PRODTOOLS_DIE="list_campaigns")
    try:
        with pytest.raises(McpClientError, match="exited during list_campaigns") as ei:
            s.call("list_campaigns", mine=True)
        assert "fake prodtools: dying inside list_campaigns" in str(ei.value)
        assert not s.started
        assert s.call("locate_file", name="x.y.z.w.0.tar")["exists"] is False
        assert s.started
    finally:
        s.close()
    _assert_no_leaked_children(before)


def test_no_start_reports_the_childs_stderr():
    s = fake("read", FAKE_PRODTOOLS_NO_START="1")
    try:
        with pytest.raises(McpClientError, match="did not start.*refusing to start"):
            s.start()
        assert not s.started and "fake prodtools: refusing to start" in s.stderr_tail
    finally:
        s.close()


def test_start_timeout():
    before = _fake_prodtools_pids()
    s = fake("read", FAKE_PRODTOOLS_HANG_START="1")
    s.start_timeout = 2
    try:
        with pytest.raises(McpClientError, match="did not start within 2 s"):
            s.start()
        assert not s.started
    finally:
        s.close()
    _assert_no_leaked_children(before)


def test_start_timeout_default_and_env(monkeypatch):
    assert StdioServer("x", "true").start_timeout == mcpclient.START_TIMEOUT_DEFAULT
    monkeypatch.setenv(mcpclient.START_TIMEOUT_VAR, "7")
    assert StdioServer("x", "true").start_timeout == 7.0


def test_close_is_idempotent_and_restartable(read):
    read.start()
    read.close()
    read.close()
    assert not read.started
    assert read.call("get_server_info")["name"] == "prodtools"
    serving_lines = [l for l in read.stderr_tail if "fake prodtools read: serving" in l]
    assert len(serving_lines) == 1
