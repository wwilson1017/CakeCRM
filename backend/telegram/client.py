"""Telegram Bot API client — synchronous httpx, no SDK.

Every function takes an explicit ``bot_token``; there is no global fallback. The
client is fully synchronous and importable without side effects (``httpx`` is
imported lazily inside ``_post``), so it can be called from any context — the poll
task offloads it via ``asyncio.to_thread``; ``service.notify_user_telegram`` (for the
#6 heartbeat) calls it directly on a plain thread.

Security: the bot token is embedded in every Bot API URL, so this module NEVER logs a
raw URL or a raw httpx exception — errors are redacted to ``method`` + status +
Telegram's own description. Two send paths keep untrusted text safe: ``send_html``
(assistant output → markdown→HTML, chunked on the source) and ``send_text`` (system /
error / notification text → NO parse mode, so arbitrary text can never break HTML
parsing). Plain-text fallback happens ONLY on a Telegram parse error, never on
auth/rate/transport failures.
"""

import logging
import time

from .format import chunk_text, markdown_to_telegram_html

logger = logging.getLogger(__name__)

MAX_TEXT_LENGTH = 4096  # Telegram's hard per-message limit
_LONG_POLL_TIMEOUT = 25  # getUpdates server-side long-poll seconds


class TelegramError(Exception):
    """A Bot API call failed. Carries the HTTP status and, on 429, ``retry_after``.

    The message is pre-redacted (never contains the token-bearing URL). ``is_parse_error``
    marks a 400 HTML-parse failure so a caller can safely retry the same content as
    plain text — the ONLY error class for which a plain-text fallback is appropriate.
    """

    def __init__(self, message: str, *, status: int | None = None,
                 retry_after: int | None = None, is_parse_error: bool = False):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after
        self.is_parse_error = is_parse_error


def _base_url(bot_token: str) -> str:
    return f"https://api.telegram.org/bot{bot_token}"


def _parse(method: str, resp) -> dict | list | None:
    """Turn a Bot API response into ``result`` or raise a redacted ``TelegramError``."""
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.status_code == 200 and data.get("ok"):
        return data.get("result")

    desc = str(data.get("description") or f"HTTP {resp.status_code}")
    params = data.get("parameters") if isinstance(data.get("parameters"), dict) else {}
    retry_after = params.get("retry_after")
    is_parse = resp.status_code == 400 and "parse" in desc.lower()
    # Redacted: method + status + Telegram's description only — never the URL.
    raise TelegramError(
        f"{method} failed ({resp.status_code}): {desc}",
        status=resp.status_code, retry_after=retry_after, is_parse_error=is_parse,
    )


def _post(token: str, method: str, payload: dict | None = None, *, read_timeout: float = 30.0):
    import httpx  # lazy — never at module top level

    timeout = httpx.Timeout(connect=10.0, read=read_timeout, write=10.0, pool=10.0)
    try:
        resp = httpx.post(f"{_base_url(token)}/{method}", json=payload or {}, timeout=timeout)
    except httpx.HTTPError as e:
        # `from None`: never chain the original exception — its repr contains the URL
        # (hence the token). Log only the method + exception class.
        logger.warning("telegram %s transport error: %s", method, type(e).__name__)
        raise TelegramError(f"{method} transport error ({type(e).__name__})") from None
    return _parse(method, resp)


# ── Bot management / validation ─────────────────────────────────────────────

def validate_token(bot_token: str) -> dict | None:
    """Validate a token via getMe. Returns the bot's ``result`` dict, or None."""
    if not bot_token:
        return None
    try:
        return _post(bot_token, "getMe", read_timeout=10.0)
    except TelegramError:
        return None


def delete_webhook(bot_token: str, drop_pending_updates: bool = False) -> None:
    """Remove any configured webhook so getUpdates long-polling can work.

    A previously-used bot may have a webhook set, which makes getUpdates fail with
    409 Conflict until it is removed. Best-effort — swallow errors.
    """
    if not bot_token:
        return
    try:
        _post(bot_token, "deleteWebhook", {"drop_pending_updates": drop_pending_updates}, read_timeout=10.0)
    except TelegramError:
        logger.info("telegram deleteWebhook did not succeed (continuing)")


def get_updates(bot_token: str, offset: int, timeout: int = _LONG_POLL_TIMEOUT) -> list[dict]:
    """Long-poll for new updates. Raises ``TelegramError`` so the poller can back off.

    Requests both ``message`` and ``callback_query`` updates (the latter carry inline
    Approve/Deny button presses). The httpx read timeout is set ABOVE the server-side
    long-poll timeout so a quiet poll returns normally instead of a client timeout.
    """
    if not bot_token:
        return []
    payload = {
        "timeout": timeout,
        "allowed_updates": ["message", "callback_query"],
    }
    if offset:
        payload["offset"] = offset
    result = _post(bot_token, "getUpdates", payload, read_timeout=timeout + 10.0)
    return result or []


_MAX_RETRY_AFTER = 30  # cap how long we'll block a send thread on a Telegram 429


def _send_message(bot_token: str, payload: dict) -> None:
    """POST sendMessage, retrying ONCE after Telegram's ``retry_after`` on a 429.

    Without this, a rate limit partway through a multi-chunk reply would raise and drop
    every remaining chunk. Blocks the calling thread (sends run under ``asyncio.to_thread``
    / on #6's heartbeat thread, never the event loop), bounded by ``_MAX_RETRY_AFTER``.
    """
    try:
        _post(bot_token, "sendMessage", payload)
    except TelegramError as e:
        if e.status == 429 and e.retry_after:
            time.sleep(min(e.retry_after, _MAX_RETRY_AFTER))
            _post(bot_token, "sendMessage", payload)
        else:
            raise


# ── Sending ─────────────────────────────────────────────────────────────────

def send_text(chat_id: int | str, text: str, bot_token: str, reply_markup: dict | None = None) -> None:
    """Send plain text (NO parse mode) — safe for arbitrary/untrusted content.

    Used for system messages, errors, and the outbound notification path
    (``service.notify_user_telegram`` / ``broadcast_telegram``): without a parse
    mode, Telegram renders the text literally, so nothing can break HTML parsing.
    """
    if not bot_token or not text:
        return
    chunks = chunk_text(text, MAX_TEXT_LENGTH)
    for i, chunk in enumerate(chunks):
        payload: dict = {"chat_id": chat_id, "text": chunk}
        # Attach the keyboard only to the final chunk.
        if reply_markup and i == len(chunks) - 1:
            payload["reply_markup"] = reply_markup
        _send_message(bot_token, payload)


def send_html(chat_id: int | str, markdown: str, bot_token: str, reply_markup: dict | None = None) -> None:
    """Send assistant output: convert markdown→Telegram HTML, chunked on the source.

    Chunking the SOURCE markdown (before conversion) guarantees HTML tags/entities are
    never split across messages. On a Telegram parse error for a chunk (and ONLY then),
    retry that chunk as plain text so the user still gets the content.
    """
    if not bot_token or not markdown:
        return
    chunks = chunk_text(markdown)
    for i, chunk in enumerate(chunks):
        payload: dict = {
            "chat_id": chat_id,
            "text": markdown_to_telegram_html(chunk),
            "parse_mode": "HTML",
        }
        if reply_markup and i == len(chunks) - 1:
            payload["reply_markup"] = reply_markup
        try:
            _send_message(bot_token, payload)
        except TelegramError as e:
            if not e.is_parse_error:
                raise
            # Parse error only: resend this chunk as literal plain text.
            fallback = {"chat_id": chat_id, "text": chunk}
            if reply_markup and i == len(chunks) - 1:
                fallback["reply_markup"] = reply_markup
            _send_message(bot_token, fallback)


def answer_callback_query(callback_query_id: str, bot_token: str, text: str = "") -> None:
    """Acknowledge a button press (stops Telegram's spinner). Best-effort."""
    if not bot_token or not callback_query_id:
        return
    payload: dict = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    try:
        _post(bot_token, "answerCallbackQuery", payload, read_timeout=10.0)
    except TelegramError:
        logger.info("telegram answerCallbackQuery did not succeed (continuing)")


def edit_reply_markup(chat_id: int | str, message_id: int, bot_token: str,
                      reply_markup: dict | None = None) -> None:
    """Replace/remove a message's inline keyboard (removes buttons after a decision).

    Passing ``reply_markup=None`` strips the keyboard entirely, so a resolved
    confirmation's buttons can't be pressed again. Best-effort.
    """
    if not bot_token or not message_id:
        return
    payload: dict = {"chat_id": chat_id, "message_id": message_id}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    else:
        payload["reply_markup"] = {"inline_keyboard": []}
    try:
        _post(bot_token, "editMessageReplyMarkup", payload, read_timeout=10.0)
    except TelegramError:
        logger.info("telegram editMessageReplyMarkup did not succeed (continuing)")
