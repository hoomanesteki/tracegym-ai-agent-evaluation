"""The capture proxy rejects streaming and replays frozen fixtures keyless."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from tracegym.capture.proxy import _request_key, create_app  # noqa: E402
from tracegym.store import connect, put  # noqa: E402

COMPLETION = {
    "model": "llama-3.1-8b-instant",
    "choices": [{"message": {"role": "assistant", "content": "hi"}}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 2},
}
REQUEST = {"model": "llama-3.1-8b-instant", "messages": [{"role": "user", "content": "hi"}]}


def test_streaming_is_rejected_with_400(tmp_path):
    conn = connect(check_same_thread=False)
    app = create_app(conn, tmp_path, mode="frozen")
    client = TestClient(app)
    resp = client.post("/v1/chat/completions", json={**REQUEST, "stream": True})
    assert resp.status_code == 400
    assert "stream=false" in resp.json()["error"]


def test_frozen_serves_recorded_fixture_without_network(tmp_path):
    conn = connect(check_same_thread=False)
    # Seed a fixture as if a live run had recorded it.
    key = _request_key(REQUEST)
    output_sha = put(tmp_path, COMPLETION)
    input_sha = put(tmp_path, REQUEST)
    conn.execute(
        "INSERT INTO fixtures (key, kind, fn_name, input_sha, output_sha, created_at) "
        "VALUES (?, 'llm', 'chat', ?, ?, '2026-01-01')",
        (key, input_sha, output_sha),
    )
    app = create_app(conn, tmp_path, mode="frozen")
    resp = TestClient(app).post("/v1/chat/completions", json=REQUEST)
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "hi"
    # A span was recorded for the replayed completion.
    assert conn.execute("SELECT COUNT(*) FROM spans WHERE kind='llm'").fetchone()[0] == 1


def test_frozen_miss_returns_424(tmp_path):
    conn = connect(check_same_thread=False)
    app = create_app(conn, tmp_path, mode="frozen")
    resp = TestClient(app).post("/v1/chat/completions", json=REQUEST)
    assert resp.status_code == 424
