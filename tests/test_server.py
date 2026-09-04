import inspect

from beamkit import server, tools


def test_tool_names_cover_every_public_tool():
    assert set(server.TOOL_NAMES) == {"run_beamline", "make_recoveries", "beamline_status",
                                      "list_beamline_runs", "beamline_outputs", "make_beamfile",
                                      "get_server_info"}
    for name, fn in server.TOOL_FUNCTIONS.items():
        assert fn is getattr(tools, name)


def test_registered_wrappers_match_tool_signatures():
    """create_mcp_server needs the real mcp package; the wrappers it builds
    are generated from TOOL_FUNCTIONS, so a signature drift shows up here."""
    for name, fn in server.TOOL_FUNCTIONS.items():
        params = inspect.signature(fn).parameters
        for p in params.values():
            assert p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY), f"{name}: {p}"


def test_instructions_name_the_gates():
    assert "confirm" in server.INSTRUCTIONS and "ledger" in server.INSTRUCTIONS and "10000" in server.INSTRUCTIONS
