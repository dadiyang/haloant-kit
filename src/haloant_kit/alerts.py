"""Generic AlertManager — Telegram notifications with cooldown and severity levels.

Provides project-agnostic alerting via Telegram sendMessage API. Projects pass
their own cooldown tables at construction time; no project-specific config
loading or hardcoded defaults.

Uses TelegramSender (python-telegram-bot) — proxy-aware, hard-timeout-protected.
"""

import asyncio
import logging
import os
import threading
import time

from haloant_kit.telegram import TelegramSender, _HARD_TIMEOUT

logger = logging.getLogger(__name__)

# Severity levels
INFO = "INFO"
WARNING = "WARNING"
CRITICAL = "CRITICAL"

_SEVERITY_EMOJI = {
    INFO: "\u2139\ufe0f",       # info
    WARNING: "\u26a0\ufe0f",    # warning
    CRITICAL: "\U0001f6a8",     # critical
}


def _escape_html(text: str) -> str:
    """Escape HTML special characters for Telegram HTML parse mode."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class AlertManager:
    """Send alerts via Telegram with per-type cooldown.

    Falls back to logging-only if Telegram is not configured or unavailable.
    Set silent=True to suppress all Telegram sends (log-only mode, for backtest).

    Args:
        bot_token: Telegram bot token. Falls back to $TELEGRAM_BOT_TOKEN.
        chat_id: Telegram chat ID. Falls back to $TELEGRAM_CHAT_ID.
        cooldowns: Mapping of alert_type -> cooldown seconds. Default empty
            dict (no cooldown = every alert is sent). CRITICAL always bypasses.
        silent: If True, never send Telegram (log-only mode).
        proxy_url: Optional HTTP/HTTPS proxy URL, passed through to TelegramSender.
    """

    def __init__(
        self,
        bot_token: str = "",
        chat_id: str = "",
        cooldowns: dict[str, int] | None = None,
        silent: bool = False,
        proxy_url: str | None = None,
    ):
        self._silent = silent
        self._cooldowns: dict[str, int] = cooldowns if cooldowns is not None else {}
        self._bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self._chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        self._last_sent: dict[str, float] = {}  # alert_type -> unix timestamp
        # Channel self-check: consecutive send failure count
        self._consecutive_failures: int = 0
        # Escalation tracker: cooldown_key -> (first_seen_ts, escalation_count)
        self._warning_tracker: dict[str, tuple[float, int]] = {}

        self._sender: TelegramSender | None = (
            TelegramSender(self._bot_token, proxy_url=proxy_url)
            if self._bot_token else None
        )

        if self._silent:
            logger.info("AlertManager: silent mode enabled, alerts will be log-only")
        elif not self._bot_token or not self._chat_id:
            logger.warning("AlertManager: Telegram not configured, alerts will be log-only")

    def _check_cooldown(self, alert_type: str, severity: str,
                        cooldown_key: str | None = None) -> bool:
        """Return True if this alert should be sent (not in cooldown).

        Args:
            cooldown_key: Optional sub-key for per-entity cooldown (e.g.,
                "price_breach:NVDA:190.44"). If None, uses alert_type.
        """
        if severity == CRITICAL:
            return True
        # Use base alert_type for cooldown duration lookup
        cooldown = self._cooldowns.get(alert_type, 0)
        if cooldown == 0:
            return True
        key = cooldown_key or alert_type
        last = self._last_sent.get(key, 0)
        elapsed = time.time() - last
        if elapsed < cooldown:
            logger.info(
                "Cooldown active: type=%s key=%s (%.0fs/%.0fs remaining)",
                alert_type, key, cooldown - elapsed, cooldown,
            )
            return False
        return True

    def _record_sent(self, alert_type: str, cooldown_key: str | None = None):
        key = cooldown_key or alert_type
        self._last_sent[key] = time.time()

    def _update_warning_tracker(
        self, alert_type: str, severity: str, cooldown_key: str | None = None,
    ):
        """Track WARNING first-occurrence for escalation; clear on CRITICAL/INFO."""
        track_key = cooldown_key or alert_type
        if severity == WARNING:
            if track_key not in self._warning_tracker:
                self._warning_tracker[track_key] = (time.time(), 0)
        elif severity in (CRITICAL, INFO):
            self._warning_tracker.pop(track_key, None)
            # Convention: xxx_recovered INFO auto-clears xxx WARNING tracker.
            if alert_type.endswith("_recovered"):
                base_key = alert_type.removesuffix("_recovered")
                self._warning_tracker.pop(base_key, None)

    async def send(
        self,
        alert_type: str,
        severity: str,
        title: str,
        detail: str = "",
        cooldown_key: str | None = None,
    ) -> bool:
        """Send an alert. Always logs; sends Telegram if cooldown allows.

        Args:
            cooldown_key: Optional per-entity cooldown key (e.g.,
                "price_breach:NVDA:190.44") for independent cooldown tracking.

        Returns True if Telegram message was sent successfully.
        """
        emoji = _SEVERITY_EMOJI.get(severity, "")
        log_msg = f"[ALERT:{severity}] {title}"
        if detail:
            log_msg += f" | {detail}"

        # Always log
        if severity == CRITICAL:
            logger.critical(log_msg)
        elif severity == WARNING:
            logger.warning(log_msg)
        else:
            logger.info(log_msg)

        # Check cooldown
        if not self._check_cooldown(alert_type, severity, cooldown_key):
            return False

        # Silent mode (e.g. backtest) — log only, never send Telegram
        if self._silent:
            return False

        # Try Telegram
        if not self._sender or not self._chat_id:
            return False

        # Build HTML message (no Markdown escaping issues)
        safe_title = _escape_html(title)
        text = f"{emoji} <b>{severity}</b>: {safe_title}"
        if detail:
            text += f"\n\n{_escape_html(detail)}"

        self._update_warning_tracker(alert_type, severity, cooldown_key)

        ok = await self._sender.send_message(self._chat_id, text)
        if ok:
            self._record_sent(alert_type, cooldown_key)
            self._consecutive_failures = 0
            return True
        else:
            self._consecutive_failures += 1
            if self._consecutive_failures >= 3:
                logger.critical(
                    "[ALERT_CHANNEL_FAILED] Telegram send failed %d consecutive times, channel may be unavailable",
                    self._consecutive_failures,
                )
            return False

    def is_available(self) -> bool:
        """Channel Protocol: whether this channel can send messages."""
        return bool(self._sender and self._chat_id and not self._silent)

    def clear_warning(self, cooldown_key: str) -> None:
        """Explicitly remove a WARNING from the escalation tracker.

        Use this when a condition resolves but the recovery event uses a
        different cooldown_key than the original WARNING.
        """
        self._warning_tracker.pop(cooldown_key, None)

    async def escalate_stale_warnings(
        self,
        delay: float = 1800,
        max_escalations: int = 2,
    ) -> int:
        """Escalate WARNING alerts that haven't resolved within `delay` seconds.

        Called once per watchdog cycle. Sends a CRITICAL alert for each WARNING
        that has been continuously active beyond the escalation delay.

        Args:
            delay: Seconds before a WARNING escalates (default 30 min).
            max_escalations: Maximum escalations per key (default 2).

        Returns:
            Number of alerts escalated this cycle.
        """
        if self._silent:
            return 0

        now = time.time()
        escalated = 0

        for key, (first_seen, count) in list(self._warning_tracker.items()):
            if count >= max_escalations:
                continue
            age = now - first_seen
            if age < delay:
                continue

            # Escalate: send CRITICAL bypassing cooldown via a fresh escalation key
            mins = int(age / 60)
            new_count = count + 1
            self._warning_tracker[key] = (first_seen, new_count)

            await self.send(
                "escalation",
                CRITICAL,
                f"Alert escalated: [{key}] active for {mins} minutes without resolution",
                f"Original WARNING escalated to CRITICAL (escalation #{new_count})",
                cooldown_key=f"escalation:{key}:{new_count}",
            )
            escalated += 1

        return escalated

    def send_sync(
        self,
        alert_type: str,
        severity: str,
        title: str,
        detail: str = "",
        cooldown_key: str | None = None,
    ) -> bool:
        """Synchronous version of send() — delegates to send() via thread.

        Uses the same thread + asyncio.wait_for pattern as TelegramSender._run_sync
        to guarantee return within _HARD_TIMEOUT seconds.
        """
        coro = self.send(alert_type, severity, title, detail, cooldown_key)
        result: dict = {"ok": False}

        async def _with_timeout():
            return await asyncio.wait_for(coro, timeout=_HARD_TIMEOUT)

        def _worker():
            try:
                result["ok"] = asyncio.run(_with_timeout())
            except asyncio.TimeoutError:
                logger.warning("AlertManager.send_sync timed out after %ds", _HARD_TIMEOUT)
            except Exception as e:
                logger.warning("AlertManager.send_sync failed: %r", e)

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()
        thread.join(timeout=_HARD_TIMEOUT + 2)

        if thread.is_alive():
            logger.warning("AlertManager.send_sync blocked >%ds", _HARD_TIMEOUT)
            return False

        return result["ok"]
