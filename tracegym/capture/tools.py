"""The capture runtime: fixtures, replay modes, and span recording.

A single ``Runtime`` object mediates every side-effecting call an agent makes,
tools via the ``@rt.tool`` decorator and LLM turns via ``rt.chat(...)``. Each call
is keyed by a hash of its inputs and recorded as a fixture plus a span:

- ``live``          always calls through, records the fixture and span.
- ``frozen_strict`` returns the recorded fixture or raises FixtureMiss; the real
                    function is never invoked. This is the CI contract: no keys,
                    no network, byte-identical outputs.
- ``frozen_record`` returns the fixture if present, otherwise calls through and
                    records it (used to backfill new fixtures).

Because an LLM fixture stores the recorded token usage and latency, frozen replay
reproduces the original cost and latency, so the gate sees real numbers offline.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tracegym.capture.llm import groq_chat, local_chat, record_from_content
from tracegym.capture.otel import llm_span_attributes, record_span, tool_span_attributes
from tracegym.config import cost_usd, load_prices
from tracegym.store.blobs import get, put
from tracegym.util.canon import canonical_json, sha256_hex


class FixtureMiss(Exception):
    """Raised in frozen_strict mode when a call has no recorded fixture."""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class Runtime:
    def __init__(
        self,
        conn,
        blob_root: str | Path,
        mode: str = "live",
        *,
        trace_id: str = "trace-0",
        provider: str = "local",
        model: str = "llama-3.1-8b-instant",
        prices: dict | None = None,
        responder: Callable[[list[dict], str], str] | None = None,
        seed: int = 0,
    ) -> None:
        if mode not in {"live", "frozen_strict", "frozen_record"}:
            raise ValueError(f"unknown mode: {mode}")
        self.conn = conn
        self.blob_root = Path(blob_root)
        self.mode = mode
        self.trace_id = trace_id
        self.provider = provider
        self.model = model
        self.prices = prices if prices is not None else _safe_prices()
        self.responder = responder
        self.seed = seed
        self.live_calls = 0  # real fn / provider invocations; stays 0 in frozen_strict
        self._seq = 0

    # -- tools -----------------------------------------------------------------

    def tool(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        """Decorate a tool so its calls are recorded and replayable.

        The wrapped tool accepts keyword arguments only; positional args raise
        TypeError so the fixture key (a hash of the kwargs) is unambiguous.
        """

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if args:
                raise TypeError(f"{fn.__name__} must be called with keyword arguments only")
            input_sha = put(self.blob_root, {"fn": fn.__name__, "kwargs": kwargs})
            key = sha256_hex(fn.__name__.encode() + canonical_json(kwargs))
            t0 = time.perf_counter()
            output, output_sha = self._resolve(
                key, "tool", fn.__name__, input_sha, lambda: fn(**kwargs)
            )
            latency_ms = (time.perf_counter() - t0) * 1000
            self._span(
                "tool",
                fn.__name__,
                input_sha,
                output_sha,
                latency_ms,
                tool_span_attributes(
                    tool_name=fn.__name__,
                    call_id=key[:16],
                    input_sha=input_sha,
                    output_sha=output_sha,
                    latency_ms=round(latency_ms, 3),
                ),
            )
            return output

        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        return wrapper

    # -- LLM -------------------------------------------------------------------

    def chat(self, messages: list[dict], model: str | None = None, **params: Any) -> dict:
        """Run one LLM turn. Returns the full record; agents read record["content"]."""
        model = model or self.model
        payload = {"messages": messages, "model": model, "params": params}
        input_sha = put(self.blob_root, payload)
        key = sha256_hex(b"chat:" + canonical_json(payload))

        def produce() -> dict:
            t0 = time.perf_counter()
            record = self._provider_chat(messages, model, params)
            record["latency_ms"] = round((time.perf_counter() - t0) * 1000, 3)
            return record

        record, output_sha = self._resolve(key, "llm", "chat", input_sha, produce)
        usage = record.get("usage", {"input_tokens": 0, "output_tokens": 0})
        provider = record.get("provider", self.provider)
        resp_model = record.get("model", model)
        cost = cost_usd(
            self.prices, provider, resp_model, usage["input_tokens"], usage["output_tokens"]
        )
        latency_ms = record.get("latency_ms", 0.0)
        self._span(
            "llm",
            "chat",
            input_sha,
            output_sha,
            latency_ms,
            llm_span_attributes(
                provider=provider,
                request_model=model,
                response_model=resp_model,
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
                cost_usd=cost,
                latency_ms=latency_ms,
                input_sha=input_sha,
                output_sha=output_sha,
            ),
        )
        return record

    def _provider_chat(self, messages: list[dict], model: str, params: dict) -> dict:
        if self.responder is not None:
            prompt = "\n".join(str(m.get("content", "")) for m in messages)
            return record_from_content(self.responder(messages, model), model, "local", prompt)
        if self.provider == "groq":
            return groq_chat(messages, model, **params)
        return local_chat(messages, model, seed=self.seed)

    # -- shared record/replay --------------------------------------------------

    def _resolve(
        self, key: str, kind: str, fn_name: str, input_sha: str, produce: Callable[[], Any]
    ) -> tuple[Any, str]:
        row = self.conn.execute("SELECT output_sha FROM fixtures WHERE key = ?", (key,)).fetchone()

        if self.mode == "frozen_strict":
            if row is None:
                raise FixtureMiss(f"no fixture for {fn_name} (key={key[:12]})")
            return get(self.blob_root, row["output_sha"]), row["output_sha"]

        if self.mode == "frozen_record" and row is not None:
            return get(self.blob_root, row["output_sha"]), row["output_sha"]

        # live, or a frozen_record miss: actually run and record.
        self.live_calls += 1
        output = produce()
        output_sha = put(self.blob_root, output)
        self.conn.execute(
            """
            INSERT OR REPLACE INTO fixtures
                (key, kind, fn_name, input_sha, output_sha, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (key, kind, fn_name, input_sha, output_sha, _now_iso()),
        )
        return output, output_sha

    def _span(
        self,
        kind: str,
        name: str,
        input_sha: str,
        output_sha: str,
        latency_ms: float,
        attributes: dict,
    ) -> None:
        self._seq += 1
        end_ns = time.time_ns()
        record_span(
            self.conn,
            span_id=f"{self.trace_id}-{self._seq:04d}",
            trace_id=self.trace_id,
            kind=kind,
            name=name,
            input_sha=input_sha,
            output_sha=output_sha,
            start_ns=end_ns - int(latency_ms * 1_000_000),
            end_ns=end_ns,
            attributes=attributes,
        )


# The spec names this ToolRuntime; keep the alias so the documented interface holds.
ToolRuntime = Runtime


def _safe_prices() -> dict:
    try:
        return load_prices()
    except Exception:
        return {"default": {"input": 0.10, "output": 0.30}}
