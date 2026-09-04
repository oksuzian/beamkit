import ast
import inspect

from beamkit import server, tools


def test_tool_names_cover_every_public_tool():
    assert set(server.TOOL_NAMES) == {"run_beamline", "make_recoveries", "beamline_status",
                                      "list_beamline_runs", "beamline_outputs", "make_beamfile",
                                      "get_server_info"}
    for name, fn in server.TOOL_FUNCTIONS.items():
        assert fn is getattr(tools, name)


def _mcp_tool_name(decorator):
    """The tool name a `@mcp.tool(name=..., description=...)` decorator
    registers, or None if this isn't that decorator."""
    if not isinstance(decorator, ast.Call):
        return None
    func = decorator.func
    if not (isinstance(func, ast.Attribute) and func.attr == "tool"
            and isinstance(func.value, ast.Name) and func.value.id == "mcp"):
        return None
    for kw in decorator.keywords:
        if kw.arg == "name" and isinstance(kw.value, ast.Constant):
            return kw.value.value
    return None


def _wrapper_functions():
    """Parse server.py's own source (no import of create_mcp_server, so no
    dependency on the mcp package) and return {tool_name: ast.FunctionDef}
    for every inner function decorated with @mcp.tool(name=...) directly in
    create_mcp_server's body."""
    tree = ast.parse(inspect.getsource(server))
    create = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "create_mcp_server")
    found = {}
    for node in create.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            name = _mcp_tool_name(dec)
            if name:
                found[name] = node
    return found


def _wrapper_params(fn_node):
    """(name, has_default, default_ast_node_or_None) per wrapper parameter,
    in declaration order. Covers positional and keyword-only args; none of
    the wrappers use *args/**kwargs."""
    args = fn_node.args
    positional = args.posonlyargs + args.args
    pad = [None] * (len(positional) - len(args.defaults))
    default_nodes = pad + list(args.defaults)
    out = [(a.arg, d is not None, d) for a, d in zip(positional, default_nodes)]
    out += [(a.arg, d is not None, d) for a, d in zip(args.kwonlyargs, args.kw_defaults)]
    return out


def _resolve_default(node):
    """A comparable value for a wrapper's default expression: its literal
    value, or - for a bare Name like the imported DEFAULT_DECK_URL - the
    runtime object that name is bound to in server's own module namespace.
    Falls back to comparing by the name itself when neither resolves."""
    try:
        return ("value", ast.literal_eval(node))
    except (ValueError, TypeError, SyntaxError):
        pass
    if isinstance(node, ast.Name) and hasattr(server, node.id):
        return ("value", getattr(server, node.id))
    if isinstance(node, ast.Name):
        return ("name", node.id)
    return ("name", ast.dump(node))


# A wrapper parameter is "promoted" when tools.py defaults it but the
# wrapper requires it. Only this one is intentional: run_beamline's
# run_as is required at the MCP boundary on purpose, because an omitted
# run_as silently defaulting to "self" is exactly the kind of mistake
# that looks like it worked. Any other promotion is undeclared drift,
# not a design choice, and must fail loudly.
ALLOWED_PROMOTIONS = {("run_beamline", "run_as")}


def test_wrapper_signatures_match_tools():
    """The @mcp.tool closures inside create_mcp_server are hand-written, not
    generated from TOOL_FUNCTIONS - they are the only interface an MCP
    client actually calls, and nothing else in the suite inspects their
    parameter lists (building the real server needs the mcp package, which
    the dev venv does not have). This parses server.py's own source with
    ast - no import of create_mcp_server, no mcp dependency - and checks
    each @mcp.tool(name=...) wrapper against inspect.signature(tools.<name>):
    same parameter names, a wrapper is never looser than the real function
    (never defaults something the real function requires), matching default
    values where both sides default a parameter, and matching order - except
    for the (tool, parameter) pairs listed in ALLOWED_PROMOTIONS, where the
    wrapper deliberately drops a default tools.py provides, making that
    parameter required at the MCP boundary. Only an allowlisted pair may
    drop a default; any other parameter that loses its default is treated as
    drift and fails the test by name. An allowlisted parameter is also
    excluded from the order check, since Python's own required-before-
    optional rule is what moves it earlier in the wrapper's parameter list."""
    wrappers = _wrapper_functions()
    assert set(wrappers) == set(server.TOOL_NAMES), \
        f"@mcp.tool names {sorted(wrappers)} != TOOL_NAMES {sorted(server.TOOL_NAMES)}"

    for name, node in wrappers.items():
        real = inspect.signature(getattr(tools, name)).parameters
        wrapper_params = _wrapper_params(node)
        wrapper_names = [n for n, _, _ in wrapper_params]
        assert set(wrapper_names) == set(real), (
            f"{name}: wrapper parameters {wrapper_names} do not match "
            f"tools.{name}'s parameters {list(real)}")

        promoted = set()
        for pname, has_default, default_node in wrapper_params:
            real_default = real[pname].default
            real_has_default = real_default is not inspect.Parameter.empty
            if not real_has_default:
                assert not has_default, (
                    f"{name}: wrapper gives {pname!r} a default, but "
                    f"tools.{name} requires {pname!r}")
                continue
            if not has_default:
                assert (name, pname) in ALLOWED_PROMOTIONS, (
                    f"{name}: wrapper drops the default for {pname!r}, but "
                    f"tools.{name} defaults it — this makes {pname!r} required "
                    f"at the MCP boundary; if intentional, add "
                    f"({name!r}, {pname!r}) to ALLOWED_PROMOTIONS")
                promoted.add(pname)
                continue
            wrapper_val = _resolve_default(default_node)
            real_val = ("value", real_default)
            assert wrapper_val == real_val, (
                f"{name}: default for {pname!r} is {wrapper_val} in the "
                f"wrapper but {real_val} in tools.{name}")

        wrapper_order = [n for n in wrapper_names if n not in promoted]
        real_order = [n for n in real if n not in promoted]
        assert wrapper_order == real_order, (
            f"{name}: parameter order {wrapper_order} does not match "
            f"tools.{name}'s order {real_order}")


def test_instructions_name_the_gates():
    assert "confirm" in server.INSTRUCTIONS and "ledger" in server.INSTRUCTIONS and "10000" in server.INSTRUCTIONS
