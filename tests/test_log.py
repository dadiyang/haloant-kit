"""Tests for haloant_kit.log module."""
import logging

import haloant_kit.log as log_mod
from haloant_kit.log import (
    JsonFormatter,
    TextFormatter,
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

    def test_extra_fields_passthrough(self):
        """extra={"device": ..., "screenshot": ...} should appear in JSON output."""
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="", lineno=0,
            msg="op failed", args=(), exc_info=None,
        )
        record.service = "svc"
        record.user_id = "u1"
        record.trace_id = ""
        # Simulate logger.error(msg, extra={...})
        record.device = "ABC"
        record.screenshot = "/tmp/x.png"
        import json
        output = json.loads(formatter.format(record))
        assert output["device"] == "ABC"
        assert output["screenshot"] == "/tmp/x.png"
        # Standard LogRecord attrs must NOT leak
        assert "created" not in output
        assert "pathname" not in output
        assert "lineno" not in output
        assert "args" not in output
        assert "threadName" not in output

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

    def test_file_handler_uses_text_formatter(self, tmp_path):
        """configure_logging should write TextFormatter (not JSON) to the log file."""
        _reset_logging_state()
        configure_logging(service="svc", user_id="u1", console=False, log_dir=tmp_path)
        logger = logging.getLogger("format_check")
        logger.info("hello text")
        log_file = next(tmp_path.glob("*.log"))
        content = log_file.read_text()
        # TextFormatter output has fixed columns; JSON output starts with '{'
        assert not content.strip().startswith("{"), "File log must not be JSON"
        assert "hello text" in content
        assert "[format_check]" in content
        _reset_logging_state()


class TestTextFormatter:
    """Tests for the human-readable TextFormatter."""

    def _make_record(self, msg="test message", level=logging.INFO, name="myapp",
                     **kwargs) -> logging.LogRecord:
        record = logging.LogRecord(
            name=name, level=level, pathname="", lineno=0,
            msg=msg, args=(), exc_info=None,
        )
        for k, v in kwargs.items():
            setattr(record, k, v)
        return record

    def test_basic_layout(self):
        """Output has fixed-column layout: timestamp level [logger] [user] [trace] msg."""
        formatter = TextFormatter()
        record = self._make_record(user_id="alice", trace_id="abcdef1234567890")
        line = formatter.format(record)
        # Level is left-padded to 8 chars
        assert "INFO    " in line or "INFO     " in line or line.split()[1].startswith("INFO")
        assert "[myapp]" in line
        assert "[alice]" in line
        assert "[abcdef12]" in line  # trace truncated to 8 chars
        assert "test message" in line

    def test_no_user_id_shows_dash(self):
        """When user_id is not set, column shows '-'."""
        formatter = TextFormatter()
        record = self._make_record()
        # user_id not set on record at all → should fall back to "-"
        line = formatter.format(record)
        assert "[-]" in line

    def test_empty_user_id_shows_dash(self):
        """Empty string user_id also shows '-'."""
        formatter = TextFormatter()
        record = self._make_record(user_id="")
        line = formatter.format(record)
        assert "[-]" in line

    def test_no_trace_id_shows_placeholder(self):
        """When no trace_id, show 8 dashes."""
        formatter = TextFormatter()
        record = self._make_record(user_id="u1")
        # No trace_id attribute at all
        line = formatter.format(record)
        assert "[--------]" in line

    def test_trace_id_truncated_to_8(self):
        """trace_id longer than 8 chars is truncated."""
        formatter = TextFormatter()
        record = self._make_record(user_id="u1", trace_id="1234567890abcdef")
        line = formatter.format(record)
        assert "[12345678]" in line
        assert "1234567890abcdef" not in line

    def test_otel_trace_preferred_over_legacy(self):
        """OTel otelTraceID takes priority over trace_id field."""
        formatter = TextFormatter()
        record = self._make_record(user_id="u1", trace_id="legacy000")
        record.otelTraceID = "otel1234abcdef00" * 2  # 32 hex chars
        line = formatter.format(record)
        assert "[otel1234]" in line
        assert "legacy" not in line

    def test_extra_fields_appended_as_pipe_kv(self):
        """Extra logger.xxx(msg, extra={...}) fields appear as '| k=v' suffix."""
        formatter = TextFormatter()
        record = self._make_record(user_id="u1", trace_id="tr1")
        record.symbol = "AAPL"
        record.price = 150.5
        line = formatter.format(record)
        assert "| " in line
        assert "symbol=AAPL" in line
        assert "price=150.5" in line

    def test_no_extra_fields_no_pipe(self):
        """Without extra fields, no pipe separator appears."""
        formatter = TextFormatter()
        record = self._make_record(user_id="u1", trace_id="tr1")
        line = formatter.format(record)
        assert " | " not in line

    def test_exception_appended_on_next_line(self):
        """Exception traceback is appended after the log line."""
        formatter = TextFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            exc_info = sys.exc_info()
        record = self._make_record(user_id="u1")
        record.exc_info = exc_info
        output = formatter.format(record)
        lines = output.splitlines()
        assert len(lines) > 1
        full = "\n".join(lines)
        assert "ValueError" in full
        assert "boom" in full

    def test_not_json(self):
        """Output is plain text, not JSON."""
        formatter = TextFormatter()
        record = self._make_record(user_id="u1")
        line = formatter.format(record)
        assert not line.strip().startswith("{")
