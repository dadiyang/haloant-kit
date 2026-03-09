"""OpenTelemetry initialization — logs-only mode, no external collector.

Reusable across projects: copy this file + call setup_otel() after configure_logging().

    from haloant_kit.otel import setup_otel
    setup_otel("trade_monitor", user_id="irons")
"""
from __future__ import annotations

import logging

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.resources import Resource

logger = logging.getLogger(__name__)

_initialized = False


def setup_otel(service_name: str, **extra_attrs: str) -> None:
    """Initialize OpenTelemetry with logs-only mode.

    Creates a real TracerProvider (spans get valid trace/span IDs) but no
    SpanExporter — spans exist only for context propagation + log injection.
    Auto-instruments httpx, sqlite3, and stdlib logging.

    Args:
        service_name: e.g. "trade_monitor", "order_watcher"
        **extra_attrs: additional Resource attributes, e.g. user_id="irons"
    """
    global _initialized
    if _initialized:
        return
    _initialized = True

    attrs = {"service.name": service_name, **extra_attrs}
    provider = TracerProvider(resource=Resource.create(attrs))
    trace.set_tracer_provider(provider)

    # --- Auto-instrument (each is optional — fail gracefully) ---

    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        HTTPXClientInstrumentor().instrument()
    except Exception as e:
        logger.warning("OTel httpx instrument failed: %s", e)

    try:
        from opentelemetry.instrumentation.sqlite3 import SQLite3Instrumentor
        SQLite3Instrumentor().instrument()
    except Exception as e:
        logger.warning("OTel sqlite3 instrument failed: %s", e)

    try:
        from opentelemetry.instrumentation.logging import LoggingInstrumentor

        def _inject_trace_attrs(span, record):
            """log_hook: inject otelTraceID/otelSpanID into log record.

            Using log_hook instead of set_logging_format=True to avoid
            basicConfig() adding a duplicate StreamHandler.
            """
            ctx = span.get_span_context()
            record.otelTraceID = format(ctx.trace_id, "032x")
            record.otelSpanID = format(ctx.span_id, "016x")

        LoggingInstrumentor().instrument(
            set_logging_format=False,
            log_hook=_inject_trace_attrs,
        )
        # LoggingInstrumentor adds a LoggingHandler (for OTLP log export)
        # to root logger. We only need the record factory (trace ID injection),
        # not the handler. Remove it to avoid duplicate/empty output.
        root = logging.getLogger()
        root.handlers = [
            h for h in root.handlers
            if type(h).__name__ != "LoggingHandler"
        ]
    except Exception as e:
        logger.warning("OTel logging instrument failed: %s", e)

    logger.info("OpenTelemetry initialized: %s", attrs)
