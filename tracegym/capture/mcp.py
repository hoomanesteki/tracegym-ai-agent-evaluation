"""Capture MCP (Model Context Protocol) tool calls like any other tool.

An MCP tool is just a tool. Wrapping the MCP client call in ``Runtime.tool`` gives
it a fixture key, a span, latency, and keyless replay for free, so MCP tool calls
automatically join every tool metric (latency, call success, selection, redundancy,
trajectory, context fidelity). The MCP SDK is needed only to *record*; frozen
replay of MCP fixtures needs no MCP dependency at all.

    from tracegym.capture.mcp import mcp_tool
    search = mcp_tool(runtime, "search_docs", lambda **kw: client.call_tool("search_docs", kw))
    hits = search(query="refunds")   # recorded live, replayed frozen, scored as a tool
"""

from collections.abc import Callable
from typing import Any


def mcp_tool(runtime, name: str, call: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap an MCP tool invocation so it records and replays through ``runtime``.

    ``call`` performs the real MCP request (keyword args in, JSON-able result out)
    and is invoked only when recording; in frozen mode the fixture is served and
    ``call`` is never touched.
    """

    def fn(**kwargs: Any) -> Any:
        return call(**kwargs)

    fn.__name__ = name
    return runtime.tool(fn)
