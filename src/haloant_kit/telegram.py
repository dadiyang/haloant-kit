"""TelegramSender — unified Telegram message sender backed by python-telegram-bot.

Proxy priority: explicit proxy_url argument > HTTPS_PROXY env > HTTP_PROXY env > direct.

Uses HTTPXRequest so the same proxy/timeout knobs that work for httpx work here.
Thread-level hard timeout on sync calls defends against Clash TUN scenarios where
the socket connect succeeds but the upstream hangs indefinitely.
"""

import asyncio
import logging
import os
import threading

from telegram import Bot
from telegram.request import HTTPXRequest

logger = logging.getLogger(__name__)

_HARD_TIMEOUT = 15  # seconds — thread-level safety net for sync calls


def _resolve_proxy(explicit: str | None) -> str | None:
    """Return the proxy URL to use, in priority order.

    Priority: explicit argument > HTTPS_PROXY env > HTTP_PROXY env > None.
    Empty string is treated as absent (same as None).
    """
    if explicit:
        return explicit
    return os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or None


class TelegramSender:
    """Unified Telegram message sender backed by python-telegram-bot.

    Supports both async and sync callers. Sync variants use a daemon thread with
    a hard timeout to prevent indefinite hangs in proxy/TUN environments.

    Args:
        bot_token: Telegram bot token.
        proxy_url: Optional HTTP/HTTPS proxy URL. Falls back to HTTPS_PROXY /
            HTTP_PROXY environment variables if not provided.
    """

    def __init__(self, bot_token: str, proxy_url: str | None = None):
        self._proxy_url = _resolve_proxy(proxy_url)
        request = HTTPXRequest(
            proxy=self._proxy_url,
            connect_timeout=10.0,
            read_timeout=30.0,
            write_timeout=30.0,
        )
        self._bot = Bot(token=bot_token, request=request)

    # ------------------------------------------------------------------
    # Async API
    # ------------------------------------------------------------------

    async def send_message(
        self,
        chat_id: int | str,
        text: str,
        parse_mode: str = "HTML",
    ) -> bool:
        """Send a text message.

        Returns True on success, False on any error (logged at WARNING level).

        Uses explicit initialize/shutdown to ensure the internal httpx client
        is properly initialised for the *current* event loop.  This is essential
        when the caller drives each send via ``asyncio.run()`` (which creates
        and destroys a fresh loop every time).

        The send result is tracked separately from cleanup — if the API call
        succeeds but ``shutdown()`` raises, we still return True (the message
        was delivered).  This prevents downstream cooldown logic from treating
        a cleanup error as a send failure.
        """
        sent = False
        try:
            await self._bot.initialize()
            try:
                await self._bot.send_message(
                    chat_id=chat_id, text=text, parse_mode=parse_mode
                )
                sent = True
            finally:
                try:
                    await self._bot.shutdown()
                except Exception as e:
                    logger.debug("Bot shutdown error (ignored, sent=%s): %r", sent, e)
        except Exception as e:
            if not sent:
                logger.warning("TelegramSender.send_message failed: %r", e)
        return sent

    async def send_photo(
        self,
        chat_id: int | str,
        photo_path: str,
        caption: str = "",
        parse_mode: str = "HTML",
    ) -> bool:
        """Send a photo from a local file path.

        Returns True on success, False on any error (logged at WARNING level).
        """
        sent = False
        try:
            await self._bot.initialize()
            try:
                with open(photo_path, "rb") as fh:
                    await self._bot.send_photo(
                        chat_id=chat_id,
                        photo=fh,
                        caption=caption or None,
                        parse_mode=parse_mode if caption else None,
                    )
                sent = True
            finally:
                try:
                    await self._bot.shutdown()
                except Exception as e:
                    logger.debug("Bot shutdown error (ignored, sent=%s): %r", sent, e)
        except Exception as e:
            if not sent:
                logger.warning("TelegramSender.send_photo failed: %r", e)
        return sent

    # ------------------------------------------------------------------
    # Sync API (thin wrappers around _run_sync)
    # ------------------------------------------------------------------

    def send_message_sync(
        self,
        chat_id: int | str,
        text: str,
        parse_mode: str = "HTML",
    ) -> bool:
        """Synchronous variant of send_message with a hard 15-second timeout."""
        return self._run_sync(self.send_message(chat_id, text, parse_mode))

    def send_photo_sync(
        self,
        chat_id: int | str,
        photo_path: str,
        caption: str = "",
        parse_mode: str = "HTML",
    ) -> bool:
        """Synchronous variant of send_photo with a hard 15-second timeout."""
        return self._run_sync(self.send_photo(chat_id, photo_path, caption, parse_mode))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_sync(self, coro) -> bool:
        """Run an async coroutine in a daemon thread with a hard timeout.

        httpx.Timeout relies on socket-level timeouts, which can be bypassed
        when traffic goes through a transparent proxy (e.g. Clash TUN). The
        proxy accepts the connection instantly, then the upstream hangs —
        httpx never raises ConnectTimeout.

        Defence: asyncio.wait_for cancels the coroutine after _HARD_TIMEOUT,
        so the thread exits cleanly. The outer join is a safety net only.
        """
        result: dict = {"ok": False, "error": None}

        async def _with_timeout():
            return await asyncio.wait_for(coro, timeout=_HARD_TIMEOUT)

        def _worker():
            try:
                result["ok"] = asyncio.run(_with_timeout())
            except asyncio.TimeoutError:
                result["error"] = TimeoutError(
                    f"coroutine timed out after {_HARD_TIMEOUT}s"
                )
            except Exception as e:
                result["error"] = e

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()
        thread.join(timeout=_HARD_TIMEOUT + 2)  # grace for asyncio cleanup

        if thread.is_alive():
            logger.warning(
                "TelegramSender sync call blocked >%ds (proxy/TUN hang?)", _HARD_TIMEOUT
            )
            return False

        if result["error"]:
            logger.warning("TelegramSender sync call failed: %r", result["error"])
            return False

        return result["ok"]
