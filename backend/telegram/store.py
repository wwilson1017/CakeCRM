"""Persistence for the Telegram integration — the ``telegram_settings`` singleton.

All state lives on one row (``id = 1``). Check-then-write paths (connect/disconnect
reset, link-code consume, the confirmation-batch gate) run in ONE transaction with
``SELECT ... FOR UPDATE`` per the repo rule, so a double-click or a concurrent
callback can never interleave. Synchronous psycopg2 — callers on the event loop must
offload via ``asyncio.to_thread``.

The bot token is Fernet-encrypted at rest (``core.encryption.encrypt_value`` /
``decrypt_value``), exactly like ``ai_providers.api_key_enc``.
"""

import hmac
import logging
import secrets

# is_pending_result is the single source of truth for "a stored tool result is still
# awaiting approval" — reused here so the Telegram batch gate matches the engine.
from assistant.history import (
    conversation_exists,
    create_conversation,
    is_unsettled_result,
)
from core.encryption import decrypt_value, encrypt_value
from core.postgres import get_connection, pg_execute, pg_fetchone

logger = logging.getLogger(__name__)

_LINK_CODE_BYTES = 16  # secrets.token_urlsafe(16) → ~128 bits, deep-link-safe chars


def _new_link_code() -> str:
    return secrets.token_urlsafe(_LINK_CODE_BYTES)


def get_settings() -> dict:
    """Return the singleton row plus a derived ``connected`` flag.

    ``bot_token_enc`` is included (still encrypted); use ``get_bot_token`` for the
    plaintext. Callers must never log the token.
    """
    row = pg_fetchone(
        "SELECT bot_token_enc, bot_username, linked_chat_id, linked_user_id, "
        "linked_name, link_code, conversation_id, pending_msg_id, poll_offset "
        "FROM telegram_settings WHERE id = 1"
    )
    if row is None:  # migration always seeds row 1; defensive only
        return {"connected": False, "linked": False}
    row["connected"] = bool(row.get("bot_token_enc"))
    row["linked"] = bool(row.get("linked_chat_id"))
    return row


def get_bot_token() -> str:
    """Return the decrypted bot token, or '' when not connected."""
    row = pg_fetchone("SELECT bot_token_enc FROM telegram_settings WHERE id = 1")
    if not row or not row.get("bot_token_enc"):
        return ""
    return decrypt_value(row["bot_token_enc"])


def get_send_target() -> tuple[str, str] | None:
    """Return ``(token, chat_id)`` for the linked user, or None if not connected/linked.

    Reads token and chat_id in ONE row so a concurrent bot swap can't hand back one
    bot's token paired with another's chat id (the connect() reset clears the link
    atomically, so a mid-swap read yields either the old pair or no link at all).
    """
    row = pg_fetchone(
        "SELECT bot_token_enc, linked_chat_id FROM telegram_settings WHERE id = 1"
    )
    if not row or not row.get("bot_token_enc") or not row.get("linked_chat_id"):
        return None
    return decrypt_value(row["bot_token_enc"]), row["linked_chat_id"]


def connect(token: str, username: str) -> str:
    """Store a validated bot token and reset ALL bot-specific state atomically.

    A bot swap must not retain the previous bot's offset (its update_ids are
    unrelated) or its linked user/conversation. Returns the freshly-generated link
    code. Encryption + code generation happen before the txn (no nested checkout).
    """
    enc = encrypt_value(token)
    code = _new_link_code()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM telegram_settings WHERE id = 1 FOR UPDATE")
        cur.execute(
            "UPDATE telegram_settings SET "
            "bot_token_enc = %s, bot_username = %s, "
            "linked_chat_id = '', linked_user_id = '', linked_name = '', "
            "conversation_id = NULL, pending_msg_id = '', "
            "link_code = %s, poll_offset = 0, updated_at = now() "
            "WHERE id = 1",
            (enc, username, code),
        )
    return code


def disconnect() -> None:
    """Clear ALL bot state in one transaction (token, link, conversation, offset)."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM telegram_settings WHERE id = 1 FOR UPDATE")
        cur.execute(
            "UPDATE telegram_settings SET "
            "bot_token_enc = '', bot_username = '', "
            "linked_chat_id = '', linked_user_id = '', linked_name = '', "
            "conversation_id = NULL, pending_msg_id = '', "
            "link_code = '', poll_offset = 0, updated_at = now() "
            "WHERE id = 1"
        )


def regenerate_link_code() -> str:
    """Issue a fresh link code and UNLINK the current user (so a new device can link)."""
    code = _new_link_code()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM telegram_settings WHERE id = 1 FOR UPDATE")
        cur.execute(
            "UPDATE telegram_settings SET "
            "link_code = %s, linked_chat_id = '', linked_user_id = '', "
            "linked_name = '', conversation_id = NULL, pending_msg_id = '', "
            "updated_at = now() WHERE id = 1",
            (code,),
        )
    return code


def link_chat(code: str, chat_id: str, user_id: str, name: str) -> bool:
    """Consume the presented link code and bind this chat/user, atomically.

    Compare-and-consume under ``FOR UPDATE``: succeeds only if a non-empty link code is
    set and matches (constant-time). The code is single-use — cleared on success. The
    code is never logged. Returns True on link, False on mismatch/expired.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT link_code FROM telegram_settings WHERE id = 1 FOR UPDATE"
        )
        row = cur.fetchone()
        stored = (row[0] if row else "") or ""
        if not stored or not code or not hmac.compare_digest(stored, code):
            return False
        cur.execute(
            "UPDATE telegram_settings SET "
            "linked_chat_id = %s, linked_user_id = %s, linked_name = %s, "
            "link_code = '', conversation_id = NULL, pending_msg_id = '', "
            "updated_at = now() WHERE id = 1",
            (str(chat_id), str(user_id), name or ""),
        )
        return True


# ── Poll cursor ─────────────────────────────────────────────────────────────

def get_offset() -> int:
    row = pg_fetchone("SELECT poll_offset FROM telegram_settings WHERE id = 1")
    return int(row["poll_offset"]) if row and row.get("poll_offset") is not None else 0


def advance_offset(new_offset: int) -> None:
    """Advance the getUpdates cursor monotonically (never move it backwards)."""
    pg_execute(
        "UPDATE telegram_settings SET poll_offset = GREATEST(poll_offset, %s), "
        "updated_at = now() WHERE id = 1",
        (int(new_offset),),
    )


# ── Conversation ────────────────────────────────────────────────────────────

def get_or_create_conversation() -> str:
    """Return the Telegram conversation id, minting one if unset or since-deleted.

    The assistant API can delete the conversation out from under us (the column is a
    nullable FK with ON DELETE SET NULL), so a stored id is verified for existence and
    recreated when gone. Sequential poll processing means no concurrent creation.
    """
    row = pg_fetchone("SELECT conversation_id FROM telegram_settings WHERE id = 1")
    conv_id = (row.get("conversation_id") if row else None) or ""
    if conv_id and conversation_exists(conv_id):
        return conv_id
    conv = create_conversation()  # own transaction — not nested under a held lock
    new_id = conv["id"]
    pg_execute(
        "UPDATE telegram_settings SET conversation_id = %s, updated_at = now() WHERE id = 1",
        (new_id,),
    )
    return new_id


# ── Confirmation batch gate ─────────────────────────────────────────────────

def set_pending_msg(msg_id: str) -> None:
    pg_execute(
        "UPDATE telegram_settings SET pending_msg_id = %s, updated_at = now() WHERE id = 1",
        (msg_id or "",),
    )


def clear_pending_msg() -> None:
    pg_execute(
        "UPDATE telegram_settings SET pending_msg_id = '', updated_at = now() WHERE id = 1"
    )


def try_consume_batch(batch_msg_id: str) -> bool:
    """Return True exactly once, when the given confirmation batch is fully resolved.

    Under ``FOR UPDATE`` on the singleton: if ``pending_msg_id`` still names this batch
    AND none of that message's tool results are still pending, clear ``pending_msg_id``
    and return True (caller runs the single continuation turn). Otherwise False. The
    lock serializes concurrent button presses so only one wins the "all resolved"
    transition — the continuation runs exactly once, never zero or twice.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT pending_msg_id, conversation_id FROM telegram_settings WHERE id = 1 FOR UPDATE"
        )
        row = cur.fetchone()
        if not row:
            return False
        pending_msg_id, conversation_id = row[0], row[1]
        if not pending_msg_id or pending_msg_id != batch_msg_id:
            return False  # already consumed by a sibling press, or not the current batch
        cur.execute(
            "SELECT tool_results FROM assistant_messages WHERE id = %s AND conversation_id = %s",
            (batch_msg_id, conversation_id),
        )
        tr = cur.fetchone()
        if tr is None:
            # The batch message is gone (e.g. its conversation was deleted out from
            # under us). Clear the stale marker and do NOT continue — there is no valid
            # turn to resume, and continuing would run an empty turn on another thread.
            cur.execute(
                "UPDATE telegram_settings SET pending_msg_id = '', updated_at = now() WHERE id = 1"
            )
            return False
        # `executing` counts as unsettled too — a write stuck mid-execution (a crash
        # between claim and merge) must not let the continuation proceed.
        pending = sum(1 for r in (tr[0] or []) if is_unsettled_result(r.get("content")))
        if pending == 0:
            cur.execute(
                "UPDATE telegram_settings SET pending_msg_id = '', updated_at = now() WHERE id = 1"
            )
            return True
        return False
