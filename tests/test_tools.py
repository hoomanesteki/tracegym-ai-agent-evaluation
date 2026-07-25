"""Fixture record/replay for tools and chat, and span recording."""

from __future__ import annotations

import json

import pytest

from tracegym.capture.tools import FixtureMiss, Runtime
from tracegym.store import connect


def test_live_records_a_fixture(tmp_path):
    conn = connect()
    rt = Runtime(conn, tmp_path, mode="live")

    @rt.tool
    def add(a, b):
        return {"sum": a + b}

    assert add(a=2, b=3) == {"sum": 5}
    row = conn.execute("SELECT fn_name, kind FROM fixtures").fetchone()
    assert row["fn_name"] == "add"
    assert row["kind"] == "tool"


def test_frozen_strict_replays_without_calling_the_function(tmp_path):
    conn = connect()
    calls = {"n": 0}

    def build(rt):
        @rt.tool
        def search(q):
            calls["n"] += 1
            return {"hits": [q]}

        return search

    build(Runtime(conn, tmp_path, mode="live"))(q="cats")
    assert calls["n"] == 1

    frozen = Runtime(conn, tmp_path, mode="frozen_strict")
    out = build(frozen)(q="cats")
    assert out == {"hits": ["cats"]}
    assert calls["n"] == 1  # the real function was not called again
    assert frozen.live_calls == 0


def test_frozen_strict_unknown_args_raises_fixture_miss(tmp_path):
    conn = connect()
    frozen = Runtime(conn, tmp_path, mode="frozen_strict")

    @frozen.tool
    def search(q):
        return {"hits": [q]}

    with pytest.raises(FixtureMiss):
        search(q="never-recorded")


def test_key_is_kwarg_order_invariant(tmp_path):
    conn = connect()

    @Runtime(conn, tmp_path, mode="live").tool
    def f(a, b):
        return {"r": [a, b]}

    f(a=1, b=2)

    # Reversed kwarg order must hit the same fixture (canonical JSON sorts keys).
    @Runtime(conn, tmp_path, mode="frozen_strict").tool
    def f(b, a):  # noqa: F811 - deliberately redefining with reordered params
        return {"r": [a, b]}

    assert f(b=2, a=1) == {"r": [1, 2]}


def test_tool_call_records_a_span(tmp_path):
    conn = connect()

    @Runtime(conn, tmp_path, mode="live").tool
    def ping(x):
        return {"x": x}

    ping(x=1)
    span = conn.execute("SELECT name, output_sha FROM spans WHERE kind = 'tool'").fetchone()
    assert span is not None
    assert span["name"] == "ping"
    assert span["output_sha"]


def test_positional_args_are_rejected(tmp_path):
    conn = connect()

    @Runtime(conn, tmp_path, mode="live").tool
    def f(a):
        return a

    with pytest.raises(TypeError):
        f(1)


def test_chat_frozen_replays_with_recorded_usage_and_cost(tmp_path):
    conn = connect()
    live = Runtime(conn, tmp_path, mode="live", provider="local")
    rec = live.chat([{"role": "user", "content": "hello"}], model="llama-3.1-8b-instant")
    assert rec["content"]
    assert rec["usage"]["output_tokens"] >= 1

    frozen = Runtime(conn, tmp_path, mode="frozen_strict", provider="local")
    rec2 = frozen.chat([{"role": "user", "content": "hello"}], model="llama-3.1-8b-instant")
    assert rec2["content"] == rec["content"]
    assert frozen.live_calls == 0

    span = conn.execute("SELECT attributes FROM spans WHERE kind = 'llm'").fetchone()
    attrs = json.loads(span["attributes"])
    assert attrs["gen_ai.provider.name"] == "local"
    assert attrs["tracegym.cost_usd"] >= 0
    assert "gen_ai.system" not in attrs  # deprecated name must never be emitted
