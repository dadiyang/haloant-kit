"""统一日志配置 — JsonFormatter + ContextFilter + daily gzip 轮转。

公共 API：
    configure_logging(service, user_id, level, console, log_dir)
    configure_daemon_logging  — backward compat alias
    set_trace_id(trace_id) / clear_trace_id()
    get_logger(name)          — backward compat alias for logging.getLogger
    get_metrics_collector()   — returns singleton LogMetricsCollector
"""
from __future__ import annotations

import gzip
import json
import logging
import logging.handlers
import shutil
import sys
import traceback
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Trace-ID context variable ─────────────────────────────────────────────────

_trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)


def set_trace_id(trace_id: str) -> None:
    """Set trace_id for the current async context / thread."""
    _trace_id_var.set(trace_id)


def clear_trace_id() -> None:
    """Clear trace_id for the current async context / thread."""
    _trace_id_var.set(None)


# ── ContextFilter ─────────────────────────────────────────────────────────────

class ContextFilter(logging.Filter):
    """Inject service, user_id, and trace_id into every log record."""

    def __init__(self, service: str, user_id: str) -> None:
        super().__init__()
        self.service = service
        self.user_id = user_id

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        record.service = self.service  # type: ignore[attr-defined]
        record.user_id = self.user_id  # type: ignore[attr-defined]
        record.trace_id = _trace_id_var.get() or ""  # type: ignore[attr-defined]
        return True


# ── JsonFormatter ─────────────────────────────────────────────────────────────

# Standard LogRecord attributes — must be excluded from extra passthrough.
_STANDARD_RECORD_ATTRS: frozenset[str] = frozenset({
    "args", "created", "exc_info", "exc_text", "filename", "funcName",
    "levelname", "levelno", "lineno", "message", "module", "msecs", "msg",
    "name", "pathname", "process", "processName", "relativeCreated",
    "stack_info", "taskName", "thread", "threadName",
})

# Custom attributes already handled explicitly in format().
_HANDLED_CUSTOM_ATTRS: frozenset[str] = frozenset({
    "trace_id", "user_id", "service", "otelTraceID", "otelSpanID",
})


class JsonFormatter(logging.Formatter):
    """Output one JSON object per line.

    Fields: ts, level, logger, msg, trace_id (if set), user_id, service,
    exc_info (if exception present).
    """

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        obj: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # OTel trace context (preferred) → legacy custom trace_id (fallback)
        otel_trace = getattr(record, "otelTraceID", "0" * 32)
        otel_span = getattr(record, "otelSpanID", "0" * 16)
        if otel_trace != "0" * 32:
            obj["trace_id"] = otel_trace
            obj["span_id"] = otel_span
        else:
            trace_id: str = getattr(record, "trace_id", "") or ""
            if trace_id:
                obj["trace_id"] = trace_id
        obj["user_id"] = getattr(record, "user_id", "")
        obj["service"] = getattr(record, "service", "")
        if record.exc_info:
            obj["exc_info"] = self.formatException(record.exc_info)
        # ── Extra fields passthrough ─────────────────────────────────────
        # Pass through any non-standard attributes set via logger.xxx(msg, extra={...})
        for key, value in record.__dict__.items():
            if key in _STANDARD_RECORD_ATTRS or key in _HANDLED_CUSTOM_ATTRS or key in obj:
                continue
            obj[key] = value
        return json.dumps(obj, ensure_ascii=False)


# ── Gzip rotator ──────────────────────────────────────────────────────────────

def _gzip_rotator(source: str, dest: str) -> None:
    """Gzip-compress rotated log file and remove original."""
    gz_dest = dest + ".gz"
    with open(source, "rb") as f_in, gzip.open(gz_dest, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    Path(source).unlink()


def _gzip_namer(name: str) -> str:
    """Return .gz suffix for rotated file name (without compressing again)."""
    return name + ".gz"


# ── LogMetricsCollector ───────────────────────────────────────────────────────

class LogMetricsCollector(logging.Handler):
    """Count WARNING+ log records for watchdog integration.

    flush_metrics() returns and resets counters.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self._warnings = 0
        self._errors = 0
        self._criticals = 0
        # top_errors: list of (msg, count) tuples, limited to 10
        self._error_counts: dict[str, int] = {}

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno == logging.WARNING:
            self._warnings += 1
        elif record.levelno == logging.ERROR:
            self._errors += 1
            key = record.getMessage()[:200]
            self._error_counts[key] = self._error_counts.get(key, 0) + 1
        elif record.levelno >= logging.CRITICAL:
            self._criticals += 1
            key = record.getMessage()[:200]
            self._error_counts[key] = self._error_counts.get(key, 0) + 1

    def flush_metrics(self) -> dict:
        """Return and reset accumulated metrics."""
        top_errors = sorted(
            self._error_counts.items(), key=lambda x: x[1], reverse=True
        )[:10]
        result = {
            "warnings": self._warnings,
            "errors": self._errors,
            "criticals": self._criticals,
            "top_errors": top_errors,
        }
        # Reset counters
        self._warnings = 0
        self._errors = 0
        self._criticals = 0
        self._error_counts = {}
        return result


# ── Module-level singleton ────────────────────────────────────────────────────

_metrics_collector: LogMetricsCollector | None = None

# True after the first successful configure_logging() call.
# Guards against re-configuration (idempotency) independently of root.handlers,
# so tests can reset this flag without fighting pytest's LogCaptureHandler.
_logging_configured: bool = False


def get_metrics_collector() -> LogMetricsCollector | None:
    """Return the singleton LogMetricsCollector (None if configure_logging not called)."""
    return _metrics_collector


# ── configure_logging ─────────────────────────────────────────────────────────

_NOISY_LOGGERS = ("ib_async.client", "httpx", "httpcore")

# Human-readable console format (mirrors old LOG_FORMAT for backward compat)
LOG_FORMAT = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging(
    service: str,
    user_id: str = "",
    level: int = logging.INFO,
    console: bool = True,
    log_dir: Path | str | None = None,
) -> logging.Logger:
    """Configure root logger with JSON file handler + optional console handler.

    Idempotent: a module-level flag prevents duplicate configuration even when
    pytest's LogCaptureHandler is already attached to root before the test body runs.

    Args:
        service:  Service name (used in JSON field and log filename).
        user_id:  User identifier (injected into every log record). Optional.
        level:    Root log level (default INFO).
        console:  Whether to attach a human-readable console handler (stderr).
        log_dir:  Directory for log files. Required — no default data dir.
                  If None, only console logging is configured (no file handler).

    Returns the root logger.
    """
    global _metrics_collector, _logging_configured

    root = logging.getLogger()
    if _logging_configured:
        return root

    root.setLevel(level)

    ctx_filter = ContextFilter(service=service, user_id=user_id)

    # ── File handler (JSON, daily rotation, gzip) ─────────────────────────
    if log_dir is not None:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{service}_{user_id}.log" if user_id else f"{service}.log"
        log_file = log_dir / filename
        fh = logging.handlers.TimedRotatingFileHandler(
            log_file,
            when="midnight",
            utc=True,
            backupCount=90,
            encoding="utf-8",
        )
        fh.rotator = _gzip_rotator
        fh.namer = _gzip_namer
        fh.setFormatter(JsonFormatter())
        fh.addFilter(ctx_filter)
        root.addHandler(fh)

    # ── Console handler (human-readable) ──────────────────────────────────
    if console:
        ch = logging.StreamHandler(sys.stderr)
        ch.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT))
        ch.addFilter(ctx_filter)
        root.addHandler(ch)

    # ── Noisy library suppression ─────────────────────────────────────────
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    # ── Metrics collector (singleton) ─────────────────────────────────────
    _metrics_collector = LogMetricsCollector()
    root.addHandler(_metrics_collector)

    _logging_configured = True
    return root


# ── Backward compat aliases ───────────────────────────────────────────────────

def configure_daemon_logging(
    service: str,
    user_id: str = "",
    data_dir: Path | str | None = None,
    level: int = logging.INFO,
    **_kwargs: Any,
) -> logging.Logger:
    """Backward compat wrapper for configure_logging.

    Old signature accepted data_dir, max_bytes, backup_count.
    New implementation ignores max_bytes/backup_count and maps data_dir -> log_dir.
    """
    log_dir: Path | str | None = None
    if data_dir is not None:
        log_dir = Path(data_dir) / "logs"
    return configure_logging(service=service, user_id=user_id, level=level, log_dir=log_dir)


def get_logger(name: str) -> logging.Logger:
    """Backward compat alias for logging.getLogger(name)."""
    return logging.getLogger(name)
