"""The OTel SpanExporter really ingests OpenTelemetry SDK spans into the store."""

from __future__ import annotations

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from tracegym.capture.otel import KIND, PROVIDER_NAME, REQUEST_MODEL, SQLiteSpanExporter
from tracegym.store import connect


def test_sqlite_span_exporter_persists_genai_spans():
    conn = connect()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(SQLiteSpanExporter(conn)))
    tracer = provider.get_tracer("test")

    with tracer.start_as_current_span("chat") as span:
        span.set_attribute(KIND, "llm")
        span.set_attribute(PROVIDER_NAME, "gemini")
        span.set_attribute(REQUEST_MODEL, "gemini-2.5-flash")
    provider.force_flush()

    row = conn.execute("SELECT name, kind FROM spans WHERE kind = 'llm'").fetchone()
    assert row is not None
    assert row["name"] == "chat"
