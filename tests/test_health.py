"""Tests for haloant_kit.health module."""
import json
import os

import pytest

from haloant_kit.health import (
    write_heartbeat,
    read_heartbeat,
    get_process_memory_mb,
    check_service,
)


class TestWriteAndReadHeartbeat:
    def test_write_then_read(self, tmp_path):
        write_heartbeat("test-svc", "u1", data_dir=tmp_path)
        hb = read_heartbeat("test-svc", "u1", data_dir=tmp_path)
        assert hb is not None
        assert hb["service"] == "test-svc"
        assert hb["pid"] == os.getpid()
        assert hb["status"] == "running"
        assert "heartbeat" in hb

    def test_write_with_metrics(self, tmp_path):
        metrics = {"cpu_pct": 12.5, "items_processed": 100}
        write_heartbeat("test-svc", "u2", metrics=metrics, data_dir=tmp_path)
        hb = read_heartbeat("test-svc", "u2", data_dir=tmp_path)
        assert hb["metrics"]["cpu_pct"] == 12.5
        assert hb["metrics"]["items_processed"] == 100

    def test_write_degraded_status(self, tmp_path):
        write_heartbeat("test-svc", "u3", status="degraded", data_dir=tmp_path)
        hb = read_heartbeat("test-svc", "u3", data_dir=tmp_path)
        assert hb["status"] == "degraded"

    def test_read_missing_returns_none(self, tmp_path):
        assert read_heartbeat("nonexistent", "u1", data_dir=tmp_path) is None

    def test_read_corrupt_returns_none(self, tmp_path):
        path = tmp_path / "health_corrupt_u1.json"
        path.write_text("not valid json{{{")
        assert read_heartbeat("corrupt", "u1", data_dir=tmp_path) is None


class TestGetProcessMemoryMb:
    def test_current_process(self):
        mem = get_process_memory_mb()
        # On Linux, should return a positive number
        if mem is not None:
            assert mem > 0

    def test_nonexistent_pid(self):
        # PID 99999999 almost certainly doesn't exist
        mem = get_process_memory_mb(99999999)
        assert mem is None

    def test_zero_pid(self):
        mem = get_process_memory_mb(0)
        assert mem is None

    def test_negative_pid(self):
        mem = get_process_memory_mb(-1)
        assert mem is None


class TestCheckService:
    def test_healthy_service(self, tmp_path):
        write_heartbeat("healthy", "u1", data_dir=tmp_path)
        result = check_service("healthy", "u1",
                               warn_threshold=60, critical_threshold=300,
                               data_dir=tmp_path)
        assert result["status"] == "ok"
        assert result["pid_alive"] is True
        assert result["heartbeat_age_s"] is not None
        assert result["heartbeat_age_s"] < 5  # Just written

    def test_missing_heartbeat(self, tmp_path):
        result = check_service("missing", "u1",
                               warn_threshold=60, critical_threshold=300,
                               data_dir=tmp_path)
        assert result["status"] == "unknown"
        assert result["detail"] == "No heartbeat file"

    def test_stale_warning(self, tmp_path):
        # Write heartbeat, then manually backdate it
        write_heartbeat("stale", "u1", data_dir=tmp_path)
        hb_path = tmp_path / "health_stale_u1.json"
        hb = json.loads(hb_path.read_text())
        from datetime import datetime, timezone, timedelta
        old_time = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
        hb["heartbeat"] = old_time
        hb_path.write_text(json.dumps(hb))

        result = check_service("stale", "u1",
                               warn_threshold=60, critical_threshold=300,
                               data_dir=tmp_path)
        assert result["status"] == "warning"
        assert "stale" in result["detail"].lower()

    def test_stale_critical(self, tmp_path):
        write_heartbeat("stale-crit", "u1", data_dir=tmp_path)
        hb_path = tmp_path / "health_stale-crit_u1.json"
        hb = json.loads(hb_path.read_text())
        from datetime import datetime, timezone, timedelta
        old_time = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
        hb["heartbeat"] = old_time
        hb_path.write_text(json.dumps(hb))

        result = check_service("stale-crit", "u1",
                               warn_threshold=60, critical_threshold=300,
                               data_dir=tmp_path)
        assert result["status"] == "critical"

    def test_invalid_thresholds_zero(self, tmp_path):
        with pytest.raises(ValueError, match="Thresholds must be positive"):
            check_service("svc", "u1", warn_threshold=0, critical_threshold=300,
                          data_dir=tmp_path)

    def test_invalid_thresholds_negative(self, tmp_path):
        with pytest.raises(ValueError, match="Thresholds must be positive"):
            check_service("svc", "u1", warn_threshold=-10, critical_threshold=300,
                          data_dir=tmp_path)

    def test_invalid_thresholds_warn_ge_critical(self, tmp_path):
        with pytest.raises(ValueError, match="warn_threshold.*must be less than"):
            check_service("svc", "u1", warn_threshold=300, critical_threshold=300,
                          data_dir=tmp_path)
