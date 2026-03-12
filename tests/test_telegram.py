"""Tests for haloant_kit.telegram module."""
import asyncio
import os
import threading
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from haloant_kit.telegram import TelegramSender, _resolve_proxy


# ---------------------------------------------------------------------------
# _resolve_proxy
# ---------------------------------------------------------------------------

class TestResolveProxy:
    def test_explicit_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("HTTPS_PROXY", "http://env-proxy:8080")
        assert _resolve_proxy("http://explicit:9090") == "http://explicit:9090"

    def test_https_proxy_env(self, monkeypatch):
        monkeypatch.setenv("HTTPS_PROXY", "http://env-proxy:8080")
        monkeypatch.delenv("HTTP_PROXY", raising=False)
        assert _resolve_proxy(None) == "http://env-proxy:8080"

    def test_http_proxy_fallback(self, monkeypatch):
        monkeypatch.delenv("HTTPS_PROXY", raising=False)
        monkeypatch.setenv("HTTP_PROXY", "http://http-proxy:8888")
        assert _resolve_proxy(None) == "http://http-proxy:8888"

    def test_no_proxy(self, monkeypatch):
        monkeypatch.delenv("HTTPS_PROXY", raising=False)
        monkeypatch.delenv("HTTP_PROXY", raising=False)
        assert _resolve_proxy(None) is None

    def test_empty_string_treated_as_none(self, monkeypatch):
        monkeypatch.delenv("HTTPS_PROXY", raising=False)
        monkeypatch.delenv("HTTP_PROXY", raising=False)
        assert _resolve_proxy("") is None


# ---------------------------------------------------------------------------
# TelegramSender construction
# ---------------------------------------------------------------------------

class TestConstruction:
    def test_basic_construction(self):
        sender = TelegramSender(bot_token="123:abc")
        assert sender is not None

    def test_proxy_url_stored(self):
        sender = TelegramSender(bot_token="123:abc", proxy_url="http://p:8080")
        assert sender._proxy_url == "http://p:8080"

    def test_no_proxy(self, monkeypatch):
        monkeypatch.delenv("HTTPS_PROXY", raising=False)
        monkeypatch.delenv("HTTP_PROXY", raising=False)
        sender = TelegramSender(bot_token="123:abc")
        assert sender._proxy_url is None


# ---------------------------------------------------------------------------
# send_message (async)
# ---------------------------------------------------------------------------

class TestSendMessage:
    @pytest.mark.asyncio
    async def test_success(self):
        sender = TelegramSender(bot_token="123:abc")
        mock_bot = AsyncMock()
        mock_bot.send_message = AsyncMock(return_value=MagicMock())
        sender._bot = mock_bot

        result = await sender.send_message(chat_id="456", text="hello")

        assert result is True
        mock_bot.send_message.assert_awaited_once_with(
            chat_id="456", text="hello", parse_mode="HTML"
        )

    @pytest.mark.asyncio
    async def test_failure_returns_false(self):
        sender = TelegramSender(bot_token="123:abc")
        mock_bot = AsyncMock()
        mock_bot.send_message = AsyncMock(side_effect=Exception("network error"))
        sender._bot = mock_bot

        result = await sender.send_message(chat_id="456", text="hello")

        assert result is False

    @pytest.mark.asyncio
    async def test_custom_parse_mode(self):
        sender = TelegramSender(bot_token="123:abc")
        mock_bot = AsyncMock()
        mock_bot.send_message = AsyncMock(return_value=MagicMock())
        sender._bot = mock_bot

        result = await sender.send_message(chat_id="456", text="*bold*", parse_mode="MarkdownV2")

        assert result is True
        mock_bot.send_message.assert_awaited_once_with(
            chat_id="456", text="*bold*", parse_mode="MarkdownV2"
        )


# ---------------------------------------------------------------------------
# send_photo (async)
# ---------------------------------------------------------------------------

class TestSendPhoto:
    @pytest.mark.asyncio
    async def test_success(self, tmp_path):
        photo_file = tmp_path / "test.png"
        photo_file.write_bytes(b"fake-png-data")

        sender = TelegramSender(bot_token="123:abc")
        mock_bot = AsyncMock()
        mock_bot.send_photo = AsyncMock(return_value=MagicMock())
        sender._bot = mock_bot

        result = await sender.send_photo(chat_id="456", photo_path=str(photo_file))

        assert result is True
        mock_bot.send_photo.assert_awaited_once()
        call_kwargs = mock_bot.send_photo.call_args
        assert call_kwargs.kwargs.get("chat_id") == "456" or call_kwargs.args[0] == "456"

    @pytest.mark.asyncio
    async def test_with_caption(self, tmp_path):
        photo_file = tmp_path / "chart.png"
        photo_file.write_bytes(b"fake-png-data")

        sender = TelegramSender(bot_token="123:abc")
        mock_bot = AsyncMock()
        mock_bot.send_photo = AsyncMock(return_value=MagicMock())
        sender._bot = mock_bot

        result = await sender.send_photo(
            chat_id="456", photo_path=str(photo_file), caption="My chart"
        )

        assert result is True
        call_kwargs = mock_bot.send_photo.call_args
        assert "My chart" in str(call_kwargs)

    @pytest.mark.asyncio
    async def test_failure_returns_false(self, tmp_path):
        photo_file = tmp_path / "test.png"
        photo_file.write_bytes(b"fake-png-data")

        sender = TelegramSender(bot_token="123:abc")
        mock_bot = AsyncMock()
        mock_bot.send_photo = AsyncMock(side_effect=Exception("upload failed"))
        sender._bot = mock_bot

        result = await sender.send_photo(chat_id="456", photo_path=str(photo_file))

        assert result is False


# ---------------------------------------------------------------------------
# send_message_sync
# ---------------------------------------------------------------------------

class TestSendMessageSync:
    def test_success(self):
        sender = TelegramSender(bot_token="123:abc")
        mock_bot = AsyncMock()
        mock_bot.send_message = AsyncMock(return_value=MagicMock())
        sender._bot = mock_bot

        result = sender.send_message_sync(chat_id="456", text="sync hello")

        assert result is True
        mock_bot.send_message.assert_awaited_once_with(
            chat_id="456", text="sync hello", parse_mode="HTML"
        )

    def test_failure_returns_false(self):
        sender = TelegramSender(bot_token="123:abc")
        mock_bot = AsyncMock()
        mock_bot.send_message = AsyncMock(side_effect=Exception("timeout"))
        sender._bot = mock_bot

        result = sender.send_message_sync(chat_id="456", text="fail me")

        assert result is False

    def test_hard_timeout_returns_false(self):
        """Thread blocks beyond _HARD_TIMEOUT => returns False without hanging test."""
        import haloant_kit.telegram as tg_module

        sender = TelegramSender(bot_token="123:abc")

        # Reduce _HARD_TIMEOUT so the test only waits 0.5s instead of 15s.
        original_timeout = tg_module._HARD_TIMEOUT
        tg_module._HARD_TIMEOUT = 0.5

        async def _infinite():
            # Block forever so the thread join times out.
            await asyncio.sleep(9999)

        try:
            # Pass a blocking coroutine directly to _run_sync.
            result = sender._run_sync(_infinite())
            assert result is False
        finally:
            tg_module._HARD_TIMEOUT = original_timeout


# ---------------------------------------------------------------------------
# send_photo_sync
# ---------------------------------------------------------------------------

class TestSendPhotoSync:
    def test_success(self, tmp_path):
        photo_file = tmp_path / "chart.png"
        photo_file.write_bytes(b"fake-png-data")

        sender = TelegramSender(bot_token="123:abc")
        mock_bot = AsyncMock()
        mock_bot.send_photo = AsyncMock(return_value=MagicMock())
        sender._bot = mock_bot

        result = sender.send_photo_sync(chat_id="456", photo_path=str(photo_file))

        assert result is True
