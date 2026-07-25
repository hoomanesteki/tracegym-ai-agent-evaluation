"""MCP tool calls record and replay through the same path as any tool."""

from __future__ import annotations

from tracegym.capture.mcp import mcp_tool
from tracegym.capture.tools import Runtime
from tracegym.store import connect


def test_mcp_tool_records_then_replays_without_calling(tmp_path):
    conn = connect()
    calls = {"n": 0}

    def real_call(**kwargs):
        calls["n"] += 1
        return {"hits": [kwargs["query"]]}

    # Record live.
    live = mcp_tool(Runtime(conn, tmp_path, mode="live"), "search_docs", real_call)
    assert live(query="refunds") == {"hits": ["refunds"]}
    assert calls["n"] == 1

    # Replay frozen: the MCP client is never touched, and the span names the tool.
    frozen_rt = Runtime(conn, tmp_path, mode="frozen_strict")
    frozen = mcp_tool(frozen_rt, "search_docs", real_call)
    assert frozen(query="refunds") == {"hits": ["refunds"]}
    assert calls["n"] == 1
    assert frozen_rt.live_calls == 0
    span = conn.execute("SELECT name FROM spans WHERE kind='tool'").fetchone()
    assert span["name"] == "search_docs"
