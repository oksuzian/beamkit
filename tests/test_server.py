import inspect

from beamkit import records, server, tools


def test_tool_table_covers_every_public_tool():
    assert set(server.TOOLS) == {"run_beamline", "make_recoveries", "beamline_status",
                                 "list_beamline_runs", "beamline_outputs", "make_beamfile",
                                 "get_server_info", "submit_run", "fetch_outputs"}
    for name in server.TOOLS:
        fn = getattr(tools, name)
        assert callable(fn) and fn.__doc__, f"{name}: MCPServer describes it by its docstring"


def test_every_tool_parameter_is_annotated():
    """MCPServer builds each tool's schema from tools.py's own annotations; an
    unannotated parameter reaches the client as a string."""
    for name in server.TOOLS:
        for p in inspect.signature(getattr(tools, name)).parameters.values():
            assert p.annotation is not inspect.Parameter.empty, f"{name}({p.name}) has no annotation"


def test_run_as_is_required_wherever_a_call_can_write():
    """An omitted run_as silently defaulting to 'self' is the kind of
    mistake that looks like it worked."""
    for name in ("run_beamline", "make_recoveries", "make_beamfile"):
        p = inspect.signature(getattr(tools, name)).parameters["run_as"]
        assert p.default is inspect.Parameter.empty, f"{name}: run_as has a default"


def test_instructions_name_the_gates():
    assert "confirm" in server.INSTRUCTIONS and "ledger" in server.INSTRUCTIONS and "10000" in server.INSTRUCTIONS


def test_instructions_name_every_state():
    """The state vocabulary a client reads is records.STATES itself, not a
    second hand-maintained copy."""
    for state in records.STATES:
        assert state in server.INSTRUCTIONS, state


def test_instructions_name_the_nersc_path():
    for word in ("site=\"nersc\"", "nersc.toml", "CFS", "no recovery", "submit_run"):
        assert word in server.INSTRUCTIONS, word
