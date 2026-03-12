"""Tests for haloant_kit.alerts module."""
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from haloant_kit.alerts import (
    AlertManager,
    CRITICAL,
    INFO,
    WARNING,
    _escape_html,
)


class TestConstants:
    def test_severity_levels_exported(self):
        assert INFO == "INFO"
        assert WARNING == "WARNING"
        assert CRITICAL == "CRITICAL"


class TestEscapeHtml:
    def test_escapes_special_chars(self):
        assert _escape_html("<b>test&</b>") == "&lt;b&gt;test&amp;&lt;/b&gt;"

    def test_plain_text_unchanged(self):
        assert _escape_html("hello world") == "hello world"


# ---------------------------------------------------------------------------
# Helper: create an AlertManager whose _sender is fully mocked
# ---------------------------------------------------------------------------

def _make_am_with_mock_sender(bot_token="123:abc", chat_id="456", **kwargs):
    """Return (AlertManager, mock_sender) with TelegramSender patched out."""
    with patch("haloant_kit.alerts.TelegramSender") as MockSender:
        mock_sender_instance = MagicMock()
        mock_sender_instance.send_message = AsyncMock(return_value=True)
        mock_sender_instance.send_message_sync = MagicMock(return_value=True)
        MockSender.return_value = mock_sender_instance
        am = AlertManager(bot_token=bot_token, chat_id=chat_id, **kwargs)
    # _sender is already set on the instance; we hold a reference
    return am, mock_sender_instance


class TestAlertManagerConstruction:
    def test_default_construction(self):
        with patch("haloant_kit.alerts.TelegramSender"):
            am = AlertManager()
        assert not am.is_available()  # No bot token

    def test_with_credentials(self):
        am, _ = _make_am_with_mock_sender()
        assert am.is_available()

    def test_silent_mode(self):
        am, _ = _make_am_with_mock_sender(silent=True)
        assert not am.is_available()

    def test_env_fallback(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "env-chat")
        with patch("haloant_kit.alerts.TelegramSender") as MockSender:
            MockSender.return_value = MagicMock()
            am = AlertManager()
        assert am.is_available()

    def test_cooldowns_parameter(self):
        with patch("haloant_kit.alerts.TelegramSender"):
            cooldowns = {"heartbeat_stale": 300, "price_breach": 60}
            am = AlertManager(cooldowns=cooldowns)
        assert am._cooldowns == cooldowns

    def test_proxy_url_passed_to_sender(self):
        """proxy_url is forwarded to TelegramSender constructor."""
        with patch("haloant_kit.alerts.TelegramSender") as MockSender:
            MockSender.return_value = MagicMock()
            AlertManager(bot_token="tok", chat_id="cid", proxy_url="http://proxy:7897")
        MockSender.assert_called_once_with("tok", proxy_url="http://proxy:7897")

    def test_proxy_url_stored_on_sender(self):
        """_sender._proxy_url reflects the proxy passed in."""
        with patch("haloant_kit.alerts.TelegramSender") as MockSender:
            mock_inst = MagicMock()
            mock_inst._proxy_url = "http://proxy:7897"
            MockSender.return_value = mock_inst
            am = AlertManager(bot_token="tok", chat_id="cid", proxy_url="http://proxy:7897")
        assert am._sender._proxy_url == "http://proxy:7897"

    def test_no_token_sender_is_none(self):
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""}):
            am = AlertManager()
        assert am._sender is None


class TestCooldownLogic:
    def test_no_cooldown_always_sends(self):
        with patch("haloant_kit.alerts.TelegramSender"):
            am = AlertManager(bot_token="t", chat_id="c", cooldowns={})
        assert am._check_cooldown("any_type", INFO) is True

    def test_cooldown_blocks_repeat(self):
        with patch("haloant_kit.alerts.TelegramSender"):
            am = AlertManager(bot_token="t", chat_id="c", cooldowns={"test": 300})
        # First check passes
        assert am._check_cooldown("test", WARNING) is True
        # Record send
        am._record_sent("test")
        # Second check within cooldown fails
        assert am._check_cooldown("test", WARNING) is False

    def test_critical_bypasses_cooldown(self):
        with patch("haloant_kit.alerts.TelegramSender"):
            am = AlertManager(bot_token="t", chat_id="c", cooldowns={"test": 300})
        am._record_sent("test")
        # CRITICAL always bypasses
        assert am._check_cooldown("test", CRITICAL) is True

    def test_cooldown_key_isolation(self):
        with patch("haloant_kit.alerts.TelegramSender"):
            am = AlertManager(bot_token="t", chat_id="c", cooldowns={"test": 300})
        am._record_sent("test", cooldown_key="test:AAPL")
        # Different cooldown_key should pass
        assert am._check_cooldown("test", WARNING, cooldown_key="test:GOOG") is True
        # Same cooldown_key should fail
        assert am._check_cooldown("test", WARNING, cooldown_key="test:AAPL") is False

    def test_cooldown_expires(self):
        with patch("haloant_kit.alerts.TelegramSender"):
            am = AlertManager(bot_token="t", chat_id="c", cooldowns={"test": 1})
        am._record_sent("test")
        # Manually set last_sent to the past
        am._last_sent["test"] = time.time() - 2
        assert am._check_cooldown("test", WARNING) is True


class TestSendSync:
    def test_silent_mode_logs_only(self):
        am, mock_sender = _make_am_with_mock_sender(silent=True)
        result = am.send_sync("test", INFO, "title")
        assert result is False
        mock_sender.send_message_sync.assert_not_called()

    def test_no_credentials_logs_only(self):
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""}):
            am = AlertManager()
        result = am.send_sync("test", INFO, "title")
        assert result is False

    def test_successful_send(self):
        am, mock_sender = _make_am_with_mock_sender()
        mock_sender.send_message_sync.return_value = True

        result = am.send_sync("test", INFO, "Test Alert", "some detail")
        assert result is True
        mock_sender.send_message_sync.assert_called_once()
        call_args = mock_sender.send_message_sync.call_args
        # First positional arg is chat_id
        assert call_args[0][0] == "456"
        assert "Test Alert" in call_args[0][1]

    def test_send_failure_increments_counter(self):
        am, mock_sender = _make_am_with_mock_sender()
        mock_sender.send_message_sync.return_value = False

        result = am.send_sync("test", INFO, "title")
        assert result is False
        assert am._consecutive_failures == 1

    def test_three_failures_trigger_critical_log(self, caplog):
        am, mock_sender = _make_am_with_mock_sender()
        mock_sender.send_message_sync.return_value = False

        import logging
        with caplog.at_level(logging.CRITICAL):
            am.send_sync("test", INFO, "title")
            am.send_sync("test", INFO, "title")
            am.send_sync("test", INFO, "title")

        assert any("ALERT_CHANNEL_FAILED" in r.message for r in caplog.records)

    def test_success_resets_failure_counter(self):
        am, mock_sender = _make_am_with_mock_sender()
        mock_sender.send_message_sync.return_value = False
        am.send_sync("test", INFO, "title")
        assert am._consecutive_failures == 1

        mock_sender.send_message_sync.return_value = True
        am.send_sync("test2", INFO, "title")
        assert am._consecutive_failures == 0

    def test_cooldown_blocks_send(self):
        am, mock_sender = _make_am_with_mock_sender(cooldowns={"test": 300})
        mock_sender.send_message_sync.return_value = True

        am.send_sync("test", WARNING, "first")
        assert mock_sender.send_message_sync.call_count == 1

        am.send_sync("test", WARNING, "second")
        # Still only called once — cooldown blocked the second
        assert mock_sender.send_message_sync.call_count == 1

    def test_critical_bypasses_cooldown(self):
        am, mock_sender = _make_am_with_mock_sender(cooldowns={"test": 300})
        mock_sender.send_message_sync.return_value = True

        am.send_sync("test", WARNING, "first")
        am.send_sync("test", CRITICAL, "urgent")
        # CRITICAL sends despite cooldown
        assert mock_sender.send_message_sync.call_count == 2


class TestSendAsync:
    @pytest.mark.asyncio
    async def test_silent_mode_returns_false(self):
        am, mock_sender = _make_am_with_mock_sender(silent=True)
        result = await am.send("test", INFO, "title")
        assert result is False
        mock_sender.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_credentials_returns_false(self):
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""}):
            am = AlertManager()
        result = await am.send("test", INFO, "title")
        assert result is False

    @pytest.mark.asyncio
    async def test_successful_send(self):
        am, mock_sender = _make_am_with_mock_sender()
        mock_sender.send_message.return_value = True

        result = await am.send("test", INFO, "Test Alert", "detail here")
        assert result is True
        mock_sender.send_message.assert_called_once()
        call_args = mock_sender.send_message.call_args
        assert call_args[0][0] == "456"
        assert "Test Alert" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_send_via_sender(self):
        """AlertManager routes through _sender.send_message."""
        am, mock_sender = _make_am_with_mock_sender()
        mock_sender.send_message = AsyncMock(return_value=True)

        await am.send("alert_type", WARNING, "Something bad")
        mock_sender.send_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_failure_increments_counter(self):
        am, mock_sender = _make_am_with_mock_sender()
        mock_sender.send_message = AsyncMock(return_value=False)

        result = await am.send("test", INFO, "title")
        assert result is False
        assert am._consecutive_failures == 1


class TestIsAvailable:
    def test_returns_true_when_sender_and_chat(self):
        am, _ = _make_am_with_mock_sender()
        assert am.is_available() is True

    def test_returns_false_when_no_token(self):
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""}):
            am = AlertManager()
        assert am.is_available() is False

    def test_returns_false_when_no_chat_id(self):
        with patch("haloant_kit.alerts.TelegramSender") as MockSender:
            MockSender.return_value = MagicMock()
            am = AlertManager(bot_token="tok", chat_id="")
        assert am.is_available() is False

    def test_returns_false_when_silent(self):
        am, _ = _make_am_with_mock_sender(silent=True)
        assert am.is_available() is False


class TestClearWarning:
    def test_clear_removes_tracker(self):
        with patch("haloant_kit.alerts.TelegramSender"):
            am = AlertManager()
        am._warning_tracker["test_key"] = (time.time(), 0)
        am.clear_warning("test_key")
        assert "test_key" not in am._warning_tracker

    def test_clear_missing_key_no_error(self):
        with patch("haloant_kit.alerts.TelegramSender"):
            am = AlertManager()
        am.clear_warning("nonexistent")  # Should not raise
