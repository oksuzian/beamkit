"""A synchronous handle on one MCP server spawned over stdio.

beamkit's tools are synchronous functions that FastMCP runs on its own
event loop, so a nested asyncio.run is impossible; the client session
lives on a private loop in a daemon thread and every call is marshalled
there. The whole session lifetime runs in ONE task on that loop (anyio
cancel scopes must be exited by the task that entered them), so start()
schedules a serve task that opens the transport and the session, signals
ready, and waits for stop.

Knows nothing about prodtools; bridge.py owns that."""
import asyncio
import atexit
import concurrent.futures
import json
import os
import sys
import threading
from collections import deque

from beamkit import BeamkitError

START_TIMEOUT_VAR = "BEAMKIT_PRODTOOLS_START_TIMEOUT"
START_TIMEOUT_DEFAULT = 180.0
STDERR_TAIL = 20


class McpClientError(BeamkitError):
    pass


class McpToolError(McpClientError):
    """The server answered isError: message is the server's own text."""

    def __init__(self, server, tool, message):
        super().__init__(message)
        self.server, self.tool, self.message = server, tool, message


def _strip_prefix(text, tool):
    prefix = f"Error executing tool {tool}: "
    return text[len(prefix):] if text.startswith(prefix) else text


class StdioServer:
    def __init__(self, name, command, args=(), env=None, start_timeout=None):
        self.name = name
        self.command = command
        self.args = list(args)
        # the mcp client's default env is a short allowlist; beamkit's
        # children need the caller's whole environment (tokens, ledger
        # path, BEAMKIT_PRODTOOLS_DIR)
        self.env = dict(os.environ) if env is None else dict(env)
        self.start_timeout = (float(os.environ.get(START_TIMEOUT_VAR, START_TIMEOUT_DEFAULT))
                              if start_timeout is None else float(start_timeout))
        self._stderr_tail = deque(maxlen=STDERR_TAIL)
        self._pump = None
        self._lock = threading.RLock()
        self._loop = None
        self._thread = None
        self._serve_fut = None
        self._task = None
        self._stop = None
        self._session = None
        self.tools = {}
        atexit.register(self.close)

    # --- state
    @property
    def started(self):
        return self._session is not None

    @property
    def stderr_tail(self):
        return list(self._stderr_tail)

    def has_tool(self, name):
        return name in self.tools

    # --- lifecycle
    def start(self):
        with self._lock:
            if self._session is not None:
                return
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(target=self._loop.run_forever,
                                            name=f"mcp-{self.name}", daemon=True)
            self._thread.start()
            ready = concurrent.futures.Future()
            self._serve_fut = asyncio.run_coroutine_threadsafe(self._serve(ready), self._loop)
            try:
                ready.result(self.start_timeout)
            except concurrent.futures.TimeoutError:
                self._teardown()
                raise McpClientError(f"{self.name} server did not start within "
                                     f"{self.start_timeout:.0f} s ({self.command})") from None
            except Exception as e:
                self._teardown()
                if self._pump is not None:
                    self._pump.join(2)      # let the child's last stderr lines land
                tail = " | ".join(self._stderr_tail)
                raise McpClientError(f"{self.name} server did not start ({self.command}): "
                                     f"{type(e).__name__}: {e}; child stderr: {tail}") from e

    async def _serve(self, ready):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        # captured so _teardown can cancel and await the REAL task: the
        # concurrent.futures.Future from run_coroutine_threadsafe completes
        # (as cancelled) the instant .cancel() is called, whether or not the
        # underlying task has actually finished unwinding, so it cannot be
        # used to wait for the child process to actually be killed
        self._task = asyncio.current_task()
        self._stop = asyncio.Event()
        params = StdioServerParameters(command=self.command, args=self.args, env=self.env)
        errlog = self._stderr_tee()
        try:
            async with stdio_client(params, errlog=errlog) as (read, write):
                errlog.close()      # the child holds its own copy; the pump ends when it exits
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listing = await session.list_tools()
                    self.tools = {t.name: t for t in listing.tools}
                    self._session = session
                    ready.set_result(None)
                    await self._stop.wait()
        except BaseException as e:       # noqa: BLE001 - reported through ready or swallowed on stop
            if not ready.done():
                ready.set_exception(e)
        finally:
            if not errlog.closed:
                errlog.close()
            self._session = None
            self.tools = {}

    def _stderr_tee(self):
        """A pipe the child writes to; a pump thread keeps the last lines
        and forwards everything to our stderr."""
        r, w = os.pipe()

        def pump():
            with os.fdopen(r, "rb", buffering=0) as fh:
                for raw in iter(fh.readline, b""):
                    line = raw.decode("utf-8", "replace")
                    self._stderr_tail.append(line.rstrip("\n"))
                    sys.stderr.write(line)
                    sys.stderr.flush()
        self._pump = threading.Thread(target=pump, name=f"mcp-{self.name}-stderr", daemon=True)
        self._pump.start()
        return os.fdopen(w, "w")

    def close(self):
        with self._lock:
            if self._loop is None:
                return
            if self._stop is not None and self._serve_fut is not None and not self._serve_fut.done():
                self._loop.call_soon_threadsafe(self._stop.set)
                try:
                    self._serve_fut.result(10)
                except Exception:   # noqa: BLE001 - shutting down regardless
                    pass
            self._teardown()

    def _teardown(self):
        loop, thread = self._loop, self._thread
        self._session = None
        self.tools = {}
        task = self._task
        if task is not None and not task.done():
            # cancelling the concurrent.futures.Future from run_coroutine_threadsafe
            # completes it (as cancelled) the instant .cancel() is called, whether or
            # not the real asyncio task has actually unwound yet, so waiting on IT
            # gives no synchronization at all. Instead, schedule a coroutine on the
            # server's own loop that cancels the real task and awaits it, and wait
            # for THAT to finish, so stdio_client's shutdown (close stdin, wait,
            # then SIGTERM/SIGKILL) has actually run before we stop the loop out
            # from under it and the child is left running.
            async def _cancel_and_wait():
                task.cancel()
                try:
                    await task
                except BaseException:   # noqa: BLE001 - cancelled/failed, tearing down regardless
                    pass
            try:
                asyncio.run_coroutine_threadsafe(_cancel_and_wait(), loop).result(5)
            except Exception:   # noqa: BLE001 - best effort; teardown proceeds regardless
                pass
        self._serve_fut = self._stop = self._task = None
        self._loop = self._thread = None
        if loop is not None:
            loop.call_soon_threadsafe(loop.stop)
            if thread is not None:
                thread.join(5)
            if not loop.is_running():
                loop.close()

    # --- calls
    def call(self, tool, **args):
        with self._lock:
            if self._session is None:
                self.start()
            if tool not in self.tools:
                raise McpClientError(f"{self.name} server has no tool {tool!r}; it has "
                                     f"{', '.join(sorted(self.tools)) or 'none'}")
            fut = asyncio.run_coroutine_threadsafe(self._session.call_tool(tool, args), self._loop)
            try:
                res = fut.result()
            except Exception as e:      # noqa: BLE001 - the transport is gone, whatever the type
                self.close()
                raise McpClientError(f"{self.name} server exited during {tool}: "
                                     f"{type(e).__name__}: {e}") from e
        text = "".join(c.text for c in res.content if getattr(c, "type", None) == "text")
        if res.isError:
            raise McpToolError(self.name, tool, _strip_prefix(text, tool))
        if res.structuredContent is not None:
            return res.structuredContent
        try:
            return json.loads(text)
        except ValueError as e:
            raise McpClientError(f"{self.name} {tool}: result is not JSON: {text[:200]!r}") from e
