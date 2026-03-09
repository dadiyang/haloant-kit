"""Service heartbeat library — standardized health monitoring for all daemons.

Each daemon periodically writes a heartbeat file with status, metrics, and
timestamps. A watchdog task reads these files to detect stale services and alert.

Heartbeat files live in {data_dir}/health_{service}_{user_id}.json.

Public API:
    write_heartbeat(service, user_id, ...)
    read_heartbeat(service, user_id, ...)
    get_process_memory_mb(pid)
    check_service(service, user_id, warn_threshold, critical_threshold, ...)
"""

import json
import logging
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Import DATA_DIR from config — the only allowed internal dependency
from haloant_kit.config import DATA_DIR


def _heartbeat_path(service: str, user_id: str, data_dir: Path | None = None) -> Path:
    """Return path to heartbeat file.

    Args:
        data_dir: Override data directory. Defaults to DATA_DIR from config.
    """
    base = data_dir if data_dir is not None else DATA_DIR
    return base / f"health_{service}_{user_id}.json"


def write_heartbeat(
    service: str,
    user_id: str,
    metrics: dict | None = None,
    status: str = "running",
    data_dir: Path | None = None,
) -> None:
    """Write a heartbeat file atomically (tmp + rename).

    Args:
        service: Service name (e.g., "trade_monitor").
        user_id: User ID (or "shared" for services without user scope).
        metrics: Optional dict of service-specific metrics.
        status: "running" or "degraded".
        data_dir: Override data directory. Defaults to DATA_DIR from config.
    """
    base = data_dir if data_dir is not None else DATA_DIR
    base.mkdir(parents=True, exist_ok=True)

    data = {
        "service": service,
        "pid": os.getpid(),
        "started_at": getattr(write_heartbeat, f"_start_{service}_{user_id}", None),
        "heartbeat": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "metrics": metrics or {},
    }
    # Record started_at on first call
    start_key = f"_start_{service}_{user_id}"
    if not hasattr(write_heartbeat, start_key):
        setattr(write_heartbeat, start_key, data["heartbeat"])
        data["started_at"] = data["heartbeat"]

    path = _heartbeat_path(service, user_id, data_dir)
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(dir=str(base), suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, str(path))
    except Exception as e:
        logger.error("Failed to write heartbeat for %s: %s", service, e)
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def read_heartbeat(
    service: str,
    user_id: str,
    data_dir: Path | None = None,
) -> dict | None:
    """Read and parse a heartbeat file. Returns None if missing or corrupt.

    Args:
        data_dir: Override data directory. Defaults to DATA_DIR from config.
    """
    path = _heartbeat_path(service, user_id, data_dir)
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def get_process_memory_mb(pid: int | None = None) -> float | None:
    """Read VmRSS from /proc/{pid}/status. Returns MB or None on failure.

    VmRSS is the resident set size — actual physical memory in use.
    Uses /proc filesystem (Linux only). Returns None on non-Linux or on error.
    """
    if pid is None:
        pid = os.getpid()
    # Guard: pid must be positive
    if pid <= 0:
        return None
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    # Format: "VmRSS:     12345 kB"
                    return int(line.split()[1]) / 1024  # kB -> MB
    except (OSError, ValueError, IndexError):
        return None
    return None


# Restart grace period before declaring a dead process as critical
_RESTART_GRACE_SECONDS = 120


def check_service(
    service: str,
    user_id: str,
    warn_threshold: float,
    critical_threshold: float,
    data_dir: Path | None = None,
) -> dict:
    """Check a single service: PID alive + heartbeat freshness.

    Args:
        service: Service name.
        user_id: User ID (or "shared" for services without user scope).
        warn_threshold: Seconds of staleness before warning.
        critical_threshold: Seconds of staleness before critical.
        data_dir: Override data directory. Defaults to DATA_DIR from config.

    Returns:
        {
            "service": str,
            "pid_alive": bool | None,   # None if no heartbeat/PID
            "heartbeat_age_s": float | None,
            "status": "ok" | "warning" | "critical" | "unknown",
            "detail": str,
            "metrics": dict,
        }

    Raises ValueError if thresholds are invalid.
    """
    # Guard: thresholds must be positive
    if warn_threshold <= 0 or critical_threshold <= 0:
        raise ValueError(
            f"Thresholds must be positive: warn={warn_threshold}, critical={critical_threshold}"
        )
    if warn_threshold >= critical_threshold:
        raise ValueError(
            f"warn_threshold ({warn_threshold}) must be less than critical_threshold ({critical_threshold})"
        )

    hb = read_heartbeat(service, user_id, data_dir)
    now = time.time()

    result: dict = {
        "service": service,
        "pid_alive": None,
        "heartbeat_age_s": None,
        "status": "unknown",
        "detail": "No heartbeat file",
        "metrics": {},
    }

    if hb is None:
        return result

    result["metrics"] = hb.get("metrics", {})

    # Check PID
    pid = hb.get("pid")
    if pid:
        try:
            os.kill(pid, 0)
            result["pid_alive"] = True
        except (ProcessLookupError, PermissionError):
            result["pid_alive"] = False
        except OSError:
            result["pid_alive"] = None

    # Check heartbeat freshness
    hb_time = hb.get("heartbeat")
    if hb_time:
        try:
            hb_dt = datetime.fromisoformat(hb_time)
            age = now - hb_dt.timestamp()
            result["heartbeat_age_s"] = age

            if result["pid_alive"] is False:
                if age < _RESTART_GRACE_SECONDS:
                    result["status"] = "warning"
                    result["detail"] = f"Process dead (pid={pid}), may be restarting"
                else:
                    result["status"] = "critical"
                    result["detail"] = f"Process dead (pid={pid})"
            elif age > critical_threshold:
                result["status"] = "critical"
                result["detail"] = f"Heartbeat stale ({age / 60:.0f}min)"
            elif age > warn_threshold:
                result["status"] = "warning"
                result["detail"] = f"Heartbeat stale ({age / 60:.0f}min)"
            else:
                result["status"] = "ok"
                hb_status = hb.get("status", "running")
                result["detail"] = f"Healthy ({hb_status})"
        except (ValueError, TypeError):
            result["detail"] = "Invalid heartbeat timestamp"

    return result
