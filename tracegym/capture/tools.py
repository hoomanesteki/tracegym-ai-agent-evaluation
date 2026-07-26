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
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tracegym.capture.llm import groq_chat, local_chat, record_from_content
from tracegym.capture.otel import (
    agent_span_attributes,
    llm_span_attributes,
    record_span,
    tool_span_attributes,
)
from tracegym.config import cost_usd, load_prices
from tracegym.store.blobs import get, put
from tracegym.util.canon import canonical_json, scrub, sha256_hex


def _fixture_key(prefix: str, payload: object) -> str:
    """Hash a call to its fixture key after scrubbing volatile fields.

    Scrubbing timestamps and UUIDs before hashing means a prompt or tool argument
    that legitimately embeds "now" still maps to the same recorded fixture on
    replay, instead of a spurious FixtureMiss.
    """
    return sha256_hex(scrub(prefix + canonical_json(payload).decode("utf-8")).encode("utf-8"))


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
        self._clock_ns = 0  # deterministic per-run clock, advanced by recorded latency
        self._agent_stack: list[str] = []  # ids of the open agent spans, outermost first

    # -- agents ----------------------------------------------------------------

    @contextmanager
    def agent(self, name: str) -> Iterator[None]:
        """Group every span emitted in this block under one agent span.

        The spans of the tools and LLM turns called inside (and any nested agent)
        point at this span through parent_id, so a multi-agent trajectory, an
        orchestrator delegating to sub-agents, is reconstructable from the spans
        table alone. Spans emitted outside any agent block keep a null parent, so
        single-agent traces are unchanged.
        """
        self._seq += 1
        span_id = f"{self.trace_id}-{self._seq:04d}"
        parent_id = self._agent_stack[-1] if self._agent_stack else None
        start_ns = self._clock_ns
        self._agent_stack.append(span_id)
        status = "OK"
        try:
            yield
        except Exception:
            status = "ERROR"
            raise
        finally:
            self._agent_stack.pop()
            record_span(
                self.conn,
                span_id=span_id,
                trace_id=self.trace_id,
                parent_id=parent_id,
                kind="agent",
                name=name,
                input_sha=None,
                output_sha=None,
                start_ns=start_ns,
                end_ns=self._clock_ns,  # advanced by the child spans that ran inside
                attributes=agent_span_attributes(name=name),
                status=status,
            )

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
            key = _fixture_key(fn.__name__ + ":", kwargs)
            output, output_sha, latency_ms = self._resolve(
                key, "tool", fn.__name__, input_sha, lambda: fn(**kwargs)
            )
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
                    latency_ms=latency_ms,
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
        key = _fixture_key("chat:", payload)

        def produce() -> dict:
            return self._provider_chat(messages, model, params)

        record, output_sha, latency_ms = self._resolve(key, "llm", "chat", input_sha, produce)
        usage = record.get("usage", {"input_tokens": 0, "output_tokens": 0})
        provider = record.get("provider", self.provider)
        resp_model = record.get("model", model)
        cost = cost_usd(
            self.prices, provider, resp_model, usage["input_tokens"], usage["output_tokens"]
        )
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
    ) -> tuple[Any, str, float]:
        """Return (output, output_sha, latency_ms).

        Latency is measured once at record time and stored on the fixture, so
        frozen replay reports the recorded latency, not the replay timing. The
        live output is re-read through the blob store so the recording run and
        frozen replay hand the agent an identically JSON-normalized object.
        """
        row = self.conn.execute(
            "SELECT output_sha, latency_ms FROM fixtures WHERE key = ?", (key,)
        ).fetchone()

        if self.mode == "frozen_strict":
            if row is None:
                raise FixtureMiss(f"no fixture for {fn_name} (key={key[:12]})")
            return get(self.blob_root, row["output_sha"]), row["output_sha"], row["latency_ms"]

        if self.mode == "frozen_record" and row is not None:
            return get(self.blob_root, row["output_sha"]), row["output_sha"], row["latency_ms"]

        # live, or a frozen_record miss: actually run, time it, and record.
        self.live_calls += 1
        t0 = time.perf_counter()
        output = produce()
        latency_ms = round((time.perf_counter() - t0) * 1000, 3)
        output_sha = put(self.blob_root, output)
        self.conn.execute(
            """
            INSERT OR REPLACE INTO fixtures
                (key, kind, fn_name, input_sha, output_sha, latency_ms, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (key, kind, fn_name, input_sha, output_sha, latency_ms, _now_iso()),
        )
        # Re-read so live callers see the same JSON-normalized object frozen replay
        # returns (tuples become lists, int keys become strings, etc.).
        return get(self.blob_root, output_sha), output_sha, latency_ms

    def _span(
        self,
        kind: str,
        name: str,
        input_sha: str,
        output_sha: str,
        latency_ms: float,
        attributes: dict,
    ) -> None:
        # A per-run cumulative clock derived from recorded latencies, so span
        # timestamps and their ordering are deterministic under frozen replay
        # rather than wall-clock dependent.
        self._seq += 1
        start_ns = self._clock_ns
        end_ns = start_ns + int(latency_ms * 1_000_000)
        self._clock_ns = end_ns
        record_span(
            self.conn,
            span_id=f"{self.trace_id}-{self._seq:04d}",
            trace_id=self.trace_id,
            parent_id=self._agent_stack[-1] if self._agent_stack else None,
            kind=kind,
            name=name,
            input_sha=input_sha,
            output_sha=output_sha,
            start_ns=start_ns,
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
