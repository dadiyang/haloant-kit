# haloant-kit

Shared infrastructure for Python backend services. Logging, alerting, health checks, state persistence, resilience — the things every daemon needs but nobody wants to write twice.

## What's in the box

| Module | What it does | Lines |
|--------|-------------|-------|
| `log` | Structured text logging with daily gzip rotation, context filters, trace_id | 340 |
| `alerts` | Telegram notifications with cooldown, severity levels, and dedup | 280 |
| `health` | Heartbeat files for daemon health monitoring | 228 |
| `telegram` | Telegram message sender with proxy auto-detection | 198 |
| `config` | .env + per-user config management | 190 |
| `otel` | OpenTelemetry setup (logs-only, no collector needed) | 83 |
| `resilience` | Retry with exponential backoff and timeout | 51 |
| `state` | Atomic JSON state file read/write | 41 |

## Install

```bash
pip install haloant-kit

# With Telegram alerting
pip install haloant-kit[alerts]

# With OpenTelemetry
pip install haloant-kit[otel]

# Everything
pip install haloant-kit[all]
```

## Quick examples

### Logging

```python
from haloant_kit.log import configure_logging

configure_logging("my-service", user_id="alice", log_dir="~/.my-service/logs")
# Daily rotation, gzip compression
# File output (TextFormatter — human-readable, grep/tail friendly):
#   2026-04-01 09:43:25 INFO     [my-service] [alice] [1b2a7f0a] task completed | items=42
#   2026-04-01 09:43:35 ERROR    [my-service] [alice] [1b2a7f0a] upload failed
#   Traceback (most recent call last):
#     ...
# Console output (stderr):
#   2026-04-01 09:43:25 INFO     [my-service] task completed

# For ELK/Loki ingestion, use JsonFormatter explicitly:
# from haloant_kit.log import JsonFormatter
# handler.setFormatter(JsonFormatter())
```

### Alerting

```python
from haloant_kit.alerts import AlertManager

alerts = AlertManager(
    bot_token="123:ABC...",
    chat_id="-100123456789",
    cooldown=300,  # 5 min cooldown per alert key
)
await alerts.send("db_connection", "Database connection lost", severity="critical")
# Won't send again for the same key within 5 minutes
```

### Health checks

```python
from haloant_kit.health import HeartbeatWriter

hb = HeartbeatWriter("my-service", heartbeat_dir="~/.my-service/health")
hb.beat(status="healthy", metrics={"queue_depth": 42})
# Other processes read the heartbeat file to check if this service is alive
```

### Resilience

```python
from haloant_kit.resilience import retry_with_backoff

result = await retry_with_backoff(
    flaky_api_call,
    max_retries=3,
    base_delay=2.0,
)
```

## Why this exists

Multiple backend services (trading daemons, AI agent gateways, monitoring pipelines) kept reimplementing the same infrastructure: structured logging, Telegram alerts, health heartbeats, atomic state files. haloant-kit extracts these into a single tested package so new services start with production-grade infrastructure on day one.

## License

MIT
