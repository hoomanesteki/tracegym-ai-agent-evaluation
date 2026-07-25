"""OpenAI-compatible capture proxy.

Point any agent's ``base_url`` at this server and its LLM traffic is recorded as
fixtures and GenAI spans, then replayable with no keys. Scope is fenced on
purpose: streaming is rejected with a clear 400 (deterministic replay of token
streams is a later problem), so demo agents set ``stream=false``.

Modes:
- ``live``   forward to TG_UPSTREAM_BASE_URL, record the fixture and span.
- ``frozen`` serve the recorded response for a matching request; 424 on a miss.

Needs the ``proxy`` extra: uv sync --extra proxy
"""

# NOTE: no `from __future__ import annotations` here on purpose. FastAPI resolves
# the route's `request: Request` annotation at registration time, and Request is
# imported lazily inside create_app (to keep fastapi an optional extra). Stringized
# annotations would not resolve against the module globals, so we keep them real.

import os
import time
from datetime import UTC
from pathlib import Path

from tracegym.capture.otel import llm_span_attributes, record_span
from tracegym.config import cost_usd
from tracegym.store.blobs import get, put
from tracegym.util.canon import canonical_json, sha256_hex

STREAM_REJECTED = {
    "error": "tracegym v1 does not support streaming; set stream=false",
}


def _request_key(body: dict) -> str:
    """Stable key for a request, ignoring the streaming flag."""
    normalized = {k: v for k, v in body.items() if k != "stream"}
    return sha256_hex(b"proxy:" + canonical_json(normalized))


def create_app(
    conn,
    blob_root: str | Path,
    *,
    mode: str = "live",
    upstream_base_url: str | None = None,
    prices: dict | None = None,
):
    """Build the FastAPI app. Imported lazily so the base package needs no web deps."""
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from starlette.requests import Request

    blob_root = Path(blob_root)
    upstream = upstream_base_url or os.environ.get("TG_UPSTREAM_BASE_URL", "")
    prices = prices if prices is not None else {"default": {"input": 0.10, "output": 0.30}}
    app = FastAPI(title="TraceGym capture proxy")
    seq = {"n": 0}

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok", "mode": mode}

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        body = await request.json()
        if body.get("stream"):
            return JSONResponse(status_code=400, content=STREAM_REJECTED)

        key = _request_key(body)
        model = body.get("model", "unknown")
        input_sha = put(blob_root, body)

        row = conn.execute("SELECT output_sha FROM fixtures WHERE key = ?", (key,)).fetchone()

        if mode == "frozen":
            if row is None:
                return JSONResponse(
                    status_code=424,
                    content={"error": f"no fixture for this request (key={key[:12]})"},
                )
            data = get(blob_root, row["output_sha"])
            _record(conn, blob_root, key, model, input_sha, row["output_sha"], data, prices, seq)
            return JSONResponse(status_code=200, content=data)

        # live: forward upstream, record, return
        import httpx

        if not upstream:
            return JSONResponse(
                status_code=500,
                content={"error": "TG_UPSTREAM_BASE_URL is not set for live mode"},
            )
        headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() in {"authorization", "content-type"}
        }
        async with httpx.AsyncClient(timeout=120) as client:
            upstream_resp = await client.post(
                f"{upstream.rstrip('/')}/chat/completions", json=body, headers=headers
            )
        data = upstream_resp.json()
        if upstream_resp.status_code == 200:
            output_sha = put(blob_root, data)
            conn.execute(
                """
                INSERT OR REPLACE INTO fixtures
                    (key, kind, fn_name, input_sha, output_sha, created_at)
                VALUES (?, 'llm', 'chat', ?, ?, ?)
                """,
                (key, input_sha, output_sha, _now()),
            )
            _record(conn, blob_root, key, model, input_sha, output_sha, data, prices, seq)
            conn.commit()
        return JSONResponse(status_code=upstream_resp.status_code, content=data)

    return app


def _record(conn, blob_root, key, model, input_sha, output_sha, data, prices, seq):
    """Write one LLM span for a captured or replayed completion."""
    usage = data.get("usage", {})
    in_tok = usage.get("prompt_tokens", 0)
    out_tok = usage.get("completion_tokens", 0)
    resp_model = data.get("model", model)
    cost = cost_usd(prices, "proxy", resp_model, in_tok, out_tok)
    seq["n"] += 1
    now = time.time_ns()
    record_span(
        conn,
        span_id=f"proxy-{seq['n']:05d}",
        trace_id=f"proxy-{key[:12]}",
        kind="llm",
        name="chat",
        input_sha=input_sha,
        output_sha=output_sha,
        start_ns=now,
        end_ns=now,
        attributes=llm_span_attributes(
            provider="proxy",
            request_model=model,
            response_model=resp_model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=cost,
            latency_ms=0.0,
            input_sha=input_sha,
            output_sha=output_sha,
        ),
    )


def _now() -> str:
    from datetime import datetime

    return datetime.now(UTC).isoformat()
