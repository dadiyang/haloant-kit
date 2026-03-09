"""Tests for haloant_kit.alerts module."""
import time
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from haloant_kit.alerts import (
    AlertManager,
    INFO,
    WARNING,
    CRITICAL,
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


class TestAlertManagerConstruction:
    def test_default_construction(self):
        am = AlertManager()
        assert not am.is_available()  # No bot token

    def test_with_credentials(self):
        am = AlertManager(bot_token="123:abc", chat_id="456")
        assert am.is_available()

    def test_silent_mode(self):
        am = AlertManager(bot_token="123:abc", chat_id="456", silent=True)
        assert not am.is_available()

    def test_env_fallback(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "env-chat")
        am = AlertManager()
        assert am.is_available()

    def test_cooldowns_parameter(self):
        cooldowns = {"heartbeat_stale": 300, "price_breach": 60}
        am = AlertManager(cooldowns=cooldowns)
        assert am._cooldowns == cooldowns


class TestCooldownLogic:
    def test_no_cooldown_always_sends(self):
        am = AlertManager(bot_token="t", chat_id="c", cooldowns={})
        assert am._check_cooldown("any_type", INFO) is True

    def test_cooldown_blocks_repeat(self):
        am = AlertManager(bot_token="t", chat_id="c", cooldowns={"test": 300})
        # First check passes
        assert am._check_cooldown("test", WARNING) is True
        # Record send
        am._record_sent("test")
        # Second check within cooldown fails
        assert am._check_cooldown("test", WARNING) is False

    def test_critical_bypasses_cooldown(self):
        am = AlertManager(bot_token="t", chat_id="c", cooldowns={"test": 300})
        am._record_sent("test")
        # CRITICAL always bypasses
        assert am._check_cooldown("test", CRITICAL) is True

    def test_cooldown_key_isolation(self):
        am = AlertManager(bot_token="t", chat_id="c", cooldowns={"test": 300})
        am._record_sent("test", cooldown_key="test:AAPL")
        # Different cooldown_key should pass
        assert am._check_cooldown("test", WARNING, cooldown_key="test:GOOG") is True
        # Same cooldown_key should fail
        assert am._check_cooldown("test", WARNING, cooldown_key="test:AAPL") is False

    def test_cooldown_expires(self):
        am = AlertManager(bot_token="t", chat_id="c", cooldowns={"test": 1})
        am._record_sent("test")
        # Manually set last_sent to the past
        am._last_sent["test"] = time.time() - 2
        assert am._check_cooldown("test", WARNING) is True


class TestSendSync:
    def test_silent_mode_logs_only(self):
        am = AlertManager(bot_token="t", chat_id="c", silent=True)
        result = am.send_sync("test", INFO, "title")
        assert result is False

    def test_no_credentials_logs_only(self):
        am = AlertManager()
        result = am.send_sync("test", INFO, "title")
        assert result is False

    @patch("httpx.post")
    def test_successful_send(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        am = AlertManager(bot_token="123:abc", chat_id="456")
        result = am.send_sync("test", INFO, "Test Alert", "some detail")
        assert result is True
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert "456" in str(call_kwargs)


class TestSendAsync:
    @pytest.mark.asyncio
    async def test_silent_mode_returns_false(self):
        am = AlertManager(bot_token="t", chat_id="c", silent=True)
        result = await am.send("test", INFO, "title")
        assert result is False

    @pytest.mark.asyncio
    async def test_no_credentials_returns_false(self):
        am = AlertManager()
        result = await am.send("test", INFO, "title")
        assert result is False


class TestClearWarning:
    def test_clear_removes_tracker(self):
        am = AlertManager()
        am._warning_tracker["test_key"] = (time.time(), 0)
        am.clear_warning("test_key")
        assert "test_key" not in am._warning_tracker

    def test_clear_missing_key_no_error(self):
        am = AlertManager()
        am.clear_warning("nonexistent")  # Should not raise
