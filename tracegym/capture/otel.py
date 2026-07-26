"""OpenTelemetry GenAI semantic conventions and the SQLite span sink.

Two things live here:

1. The attribute names we emit. The GenAI semconv moved to its own repo and is
   still Development status, so we pin the version in tracegym.yaml and use the
   current names, notably gen_ai.provider.name (the deprecated gen_ai.system is
   intentionally never emitted).

2. A real opentelemetry SpanExporter that writes finished spans into the
   `spans` table. Anyone already instrumented with the OTel SDK can pipe their
   agent's spans straight into TraceGym; the internal recorder writes the same
   rows without going through the SDK, which keeps replay deterministic.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

# --- GenAI semantic-convention attribute names (pinned; Development status) ---
OPERATION_NAME = "gen_ai.operation.name"
PROVIDER_NAME = "gen_ai.provider.name"  # successor of the deprecated gen_ai.system
REQUEST_MODEL = "gen_ai.request.model"
RESPONSE_MODEL = "gen_ai.response.model"
USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
TOOL_NAME = "gen_ai.tool.name"
TOOL_CALL_ID = "gen_ai.tool.call.id"
AGENT_NAME = "gen_ai.agent.name"

# TraceGym-namespaced attributes (cost is not a gen_ai.* field).
COST_USD = "tracegym.cost_usd"
KIND = "tracegym.kind"
INPUT_SHA = "tracegym.input_sha"
OUTPUT_SHA = "tracegym.output_sha"
LATENCY_MS = "tracegym.latency_ms"


def llm_span_attributes(
    *,
    provider: str,
    request_model: str,
    response_model: str | None,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    latency_ms: float,
    input_sha: str,
    output_sha: str,
) -> dict:
    """Assemble the attribute dict for one LLM span."""
    return {
        KIND: "llm",
        OPERATION_NAME: "chat",
        PROVIDER_NAME: provider,
        REQUEST_MODEL: request_model,
        RESPONSE_MODEL: response_model or request_model,
        USAGE_INPUT_TOKENS: input_tokens,
        USAGE_OUTPUT_TOKENS: output_tokens,
        COST_USD: cost_usd,
        LATENCY_MS: latency_ms,
        INPUT_SHA: input_sha,
        OUTPUT_SHA: output_sha,
    }


def tool_span_attributes(
    *, tool_name: str, call_id: str, input_sha: str, output_sha: str, latency_ms: float
) -> dict:
    """Assemble the attribute dict for one tool span."""
    return {
        KIND: "tool",
        TOOL_NAME: tool_name,
        TOOL_CALL_ID: call_id,
        LATENCY_MS: latency_ms,
        INPUT_SHA: input_sha,
        OUTPUT_SHA: output_sha,
    }


def agent_span_attributes(*, name: str) -> dict:
    """Assemble the attribute dict for one agent span (an orchestrator or sub-agent)."""
    return {KIND: "agent", AGENT_NAME: name, OPERATION_NAME: "invoke_agent"}


def record_span(
    conn: sqlite3.Connection,
    *,
    span_id: str,
    trace_id: str,
    kind: str,
    name: str,
    input_sha: str | None,
    output_sha: str | None,
    start_ns: int,
    end_ns: int,
    attributes: dict,
    parent_id: str | None = None,
    status: str = "OK",
) -> None:
    """Insert one span row. Used by the internal recorder (no OTel SDK needed)."""
    conn.execute(
        """
        INSERT OR REPLACE INTO spans
            (id, trace_id, parent_id, kind, name, input_sha, output_sha,
             start_ns, end_ns, status, attributes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            span_id,
            trace_id,
            parent_id,
            kind,
            name,
            input_sha,
            output_sha,
            start_ns,
            end_ns,
            status,
            json.dumps(attributes, sort_keys=True),
        ),
    )


class SQLiteSpanExporter(SpanExporter):
    """OTel SpanExporter that persists spans into TraceGym's `spans` table.

    Lets an externally-instrumented agent feed TraceGym without the proxy: attach
    this exporter to your TracerProvider and every span lands in the store.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            for span in spans:
                ctx = span.get_span_context()
                attrs = dict(span.attributes or {})
                kind = attrs.get(KIND) or _infer_kind(attrs)
                parent = span.parent.span_id if span.parent else None
                record_span(
                    self._conn,
                    span_id=format(ctx.span_id, "016x"),
                    trace_id=format(ctx.trace_id, "032x"),
                    parent_id=format(parent, "016x") if parent else None,
                    kind=kind,
                    name=span.name,
                    input_sha=attrs.get(INPUT_SHA),
                    output_sha=attrs.get(OUTPUT_SHA),
                    start_ns=span.start_time or 0,
                    end_ns=span.end_time or 0,
                    attributes=attrs,
                    status=span.status.status_code.name if span.status else "UNSET",
                )
            self._conn.commit()
            return SpanExportResult.SUCCESS
        except Exception:
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:  # pragma: no cover - lifecycle hook
        pass


def _infer_kind(attrs: dict) -> str:
    if TOOL_NAME in attrs:
        return "tool"
    if OPERATION_NAME in attrs or REQUEST_MODEL in attrs:
        return "llm"
    return "agent"
