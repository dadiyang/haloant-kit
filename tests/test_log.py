"""Tests for haloant_kit.log module."""
import logging

import haloant_kit.log as log_mod
from haloant_kit.log import (
    JsonFormatter,
    ContextFilter,
    LogMetricsCollector,
    configure_logging,
    set_trace_id,
    clear_trace_id,
    get_metrics_collector,
    get_logger,
)


def _reset_logging_state():
    """Reset module-level state so configure_logging can run fresh in each test."""
    log_mod._logging_configured = False
    log_mod._metrics_collector = None
    # Remove handlers added by previous configure_logging calls
    root = logging.getLogger()
    for h in root.handlers[:]:
        if isinstance(h, (logging.handlers.TimedRotatingFileHandler,
                          LogMetricsCollector)):
            root.removeHandler(h)
        # Also remove StreamHandlers we added (not pytest's)
        if isinstance(h, logging.StreamHandler) and hasattr(h, 'filters'):
            for f in h.filters:
                if isinstance(f, ContextFilter):
                    root.removeHandler(h)
                    break


class TestJsonFormatter:
    def test_basic_format(self):
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="hello world", args=(), exc_info=None,
        )
        record.service = "svc"
        record.user_id = "u1"
        record.trace_id = ""
        import json
        output = json.loads(formatter.format(record))
        assert output["level"] == "INFO"
        assert output["msg"] == "hello world"
        assert output["service"] == "svc"
        assert output["user_id"] == "u1"

    def test_trace_id_included(self):
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="traced", args=(), exc_info=None,
        )
        record.service = "svc"
        record.user_id = ""
        record.trace_id = "abc-123"
        import json
        output = json.loads(formatter.format(record))
        assert output["trace_id"] == "abc-123"


class TestContextFilter:
    def test_injects_fields(self):
        cf = ContextFilter(service="myapp", user_id="u1")
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="test", args=(), exc_info=None,
        )
        assert cf.filter(record) is True
        assert record.service == "myapp"
        assert record.user_id == "u1"


class TestTraceId:
    def test_set_and_clear(self):
        set_trace_id("trace-1")
        from haloant_kit.log import _trace_id_var
        assert _trace_id_var.get() == "trace-1"
        clear_trace_id()
        assert _trace_id_var.get() is None


class TestLogMetricsCollector:
    def test_counts_levels(self):
        collector = LogMetricsCollector()
        # Emit warnings
        for _ in range(3):
            record = logging.LogRecord(
                name="test", level=logging.WARNING, pathname="", lineno=0,
                msg="warn", args=(), exc_info=None,
            )
            collector.emit(record)
        # Emit errors
        for _ in range(2):
            record = logging.LogRecord(
                name="test", level=logging.ERROR, pathname="", lineno=0,
                msg="err", args=(), exc_info=None,
            )
            collector.emit(record)
        metrics = collector.flush_metrics()
        assert metrics["warnings"] == 3
        assert metrics["errors"] == 2
        assert metrics["criticals"] == 0
        # After flush, counters reset
        metrics2 = collector.flush_metrics()
        assert metrics2["warnings"] == 0


class TestConfigureLogging:
    def test_configure_with_log_dir(self, tmp_path):
        _reset_logging_state()
        root = configure_logging(
            service="test-svc",
            user_id="u1",
            console=False,
            log_dir=tmp_path,
        )
        assert root is logging.getLogger()
        assert get_metrics_collector() is not None
        # Log file should be created
        log_files = list(tmp_path.glob("*.log"))
        assert len(log_files) == 1
        assert "test-svc_u1.log" in log_files[0].name
        _reset_logging_state()

    def test_configure_without_log_dir(self):
        _reset_logging_state()
        root = configure_logging(
            service="test-svc",
            console=False,
            log_dir=None,
        )
        assert root is logging.getLogger()
        assert get_metrics_collector() is not None
        _reset_logging_state()

    def test_idempotent(self, tmp_path):
        _reset_logging_state()
        r1 = configure_logging(service="svc", console=False, log_dir=tmp_path)
        r2 = configure_logging(service="svc2", console=False, log_dir=tmp_path)
        assert r1 is r2  # Second call returns same root logger, no duplicate config
        _reset_logging_state()

    def test_get_logger_alias(self):
        lg = get_logger("my.module")
        assert lg.name == "my.module"
