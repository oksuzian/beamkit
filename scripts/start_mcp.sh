#!/usr/bin/env bash
# Start the beamkit MCP stdio server on prodtools' MCP environment.
# All setup output goes to stderr: stdout is the JSON-RPC channel.
set -euo pipefail

BEAMKIT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
: "${BEAMKIT_PRODTOOLS_ROOT:?set BEAMKIT_PRODTOOLS_ROOT to a prodtools checkout whose mcp/.venv is installed}"
MCP_ROOT="$BEAMKIT_PRODTOOLS_ROOT/mcp"
[[ -f "$MCP_ROOT/scripts/_mcp_env.sh" ]] || { echo "beamkit: no $MCP_ROOT/scripts/_mcp_env.sh" >&2; exit 1; }
. "$MCP_ROOT/scripts/_mcp_env.sh"
export PYTHONPATH="$BEAMKIT_ROOT/src:$PYTHONPATH"

if [[ "${1:-}" == "--check" ]]; then
  "$PYTHON_BIN" - <<'PY'
import asyncio, sys
from beamkit.server import create_mcp_server, TOOL_NAMES
from beamkit import bridge
registered = sorted(t.name for t in asyncio.run(create_mcp_server().list_tools()))
if registered != sorted(TOOL_NAMES):
    raise SystemExit(f"tool registration mismatch: {registered} != {sorted(TOOL_NAMES)}")
info = bridge.prodtools_info()
print("OK: interpreter", sys.executable)
print("OK: tools", ", ".join(registered))
print("OK: prodtools", info["root"], info["commit"])
PY
  exit 0
fi

exec "$PYTHON_BIN" -m beamkit.server
