"""Persistence for the Telegram integration — install-wide bot config + per-seat links.

Two tables, split along the line that was always there (issue #193, Phase B / B4):

* ``telegram_settings`` — the ``CHECK (id = 1)`` singleton, and still install-wide:
  the Fernet-encrypted bot token, the bot username and the ``getUpdates`` cursor. One
  admin connects the bot; one poller runs. Its ``linked_*``/``link_code``/
  ``conversation_id``/``pending_msg_id`` columns are VESTIGIAL from #193 — written by
  nothing here, read by nothing here — and a later release drops them.
* ``telegram_links`` — one row per seat: that seat's link code, chat binding, Telegram
  identity, assistant conversation and pending-confirmation batch. A partial UNIQUE on
  ``chat_id`` makes inbound dispatch unambiguous, and the confirmation-batch mutex lives
  on the link row, so two seats pressing Approve no longer serialize through one lock.

Check-then-write paths (connect/disconnect reset, the link claim, the confirmation-batch
gate) run in ONE transaction with ``SELECT ... FOR UPDATE`` per the repo rule, so a
double-click or a concurrent callback can never interleave. Synchronous psycopg2 —
callers on the event loop must offload via ``asyncio.to_thread``.

The bot token is Fernet-encrypted at rest (``core.encryption.encrypt_value`` /
``decrypt_value``), exactly like ``ai_providers.api_key_enc``.
"""

import logging
import secrets

import psycopg2

# is_unsettled_result is the single source of truth for "a stored tool result is still
# awaiting approval" — reused here so the Telegram batch gate matches the engine.
from assistant.history import (
    conversation_exists,
    create_conversation,
    is_unsettled_result,
)
from core.encryption import decrypt_value, encrypt_value
from core.postgres import get_connection, pg_execute, pg_fetchall, pg_fetchone

logger = logging.getLogger(__name__)

_LINK_CODE_BYTES = 16  # secrets.token_urlsafe(16) → ~128 bits, deep-link-safe chars

# The public user columns a link carries alongside its own. `is_active` is part of the
# authorization decision, not decoration: a deactivated seat loses Telegram the moment it
# is deactivated, inbound and outbound alike.
_LINK_USER_COLUMNS = "u.id, u.email, u.name, u.role, u.is_active"

_LINK_COLUMNS = (
    "l.id, l.user_id, l.link_code, l.chat_id, l.telegram_user_id, "
    "l.telegram_name, l.conversation_id, l.pending_msg_id"
)

# The per-seat state a dead bot invalidates. Applied by connect() and disconnect() to
# EVERY link in the same transaction that mutates the token. `conversation_id` is
# deliberately NOT cleared: the chat binding belongs to the bot, but the assistant thread
# belongs to the seat, and deleting it would strand a conversation in that person's
# sidebar. See connect()'s docstring for the one narrow race this leaves.
_CLEAR_LINK_BINDINGS = (
    "UPDATE telegram_links SET chat_id = '', telegram_user_id = '', "
    "telegram_name = '', link_code = '', pending_msg_id = '', updated_at = now() "
    "WHERE chat_id <> '' OR link_code <> '' OR pending_msg_id <> ''"
)


def _new_link_code() -> str:
    return secrets.token_urlsafe(_LINK_CODE_BYTES)


def _link_row(row: dict | None) -> dict | None:
    """Split a joined link+user row into the link fields plus a nested public user dict."""
    if row is None:
        return None
    user = {
        "id": row.pop("u_id"),
        "email": row.pop("u_email"),
        "name": row.pop("u_name"),
        "role": row.pop("u_role"),
        "is_active": row.pop("u_is_active"),
    }
    row["user"] = user
    return row


_JOINED_LINK_SELECT = (
    f"SELECT {_LINK_COLUMNS}, "
    "u.id AS u_id, u.email AS u_email, u.name AS u_name, "
    "u.role AS u_role, u.is_active AS u_is_active "
    "FROM telegram_links l JOIN users u ON u.id = l.user_id "
)


# ── Install-wide bot config (the surviving singleton) ────────────────────────

def get_settings() -> dict:
    """Return the install-wide bot config plus a derived ``connected`` flag.

    Per-seat link state is NOT here since #193 — ask ``get_link``/``find_link`` for that.
    ``bot_token_enc`` is included (still encrypted); use ``get_bot_token`` for the
    plaintext. Callers must never log the token.
    """
    row = pg_fetchone(
        "SELECT bot_token_enc, bot_username, poll_offset "
        "FROM telegram_settings WHERE id = 1"
    )
    if row is None:  # migration always seeds row 1; defensive only
        return {"connected": False}
    row["connected"] = bool(row.get("bot_token_enc"))
    return row


def get_bot_token() -> str:
    """Return the decrypted bot token, or '' when not connected."""
    row = pg_fetchone("SELECT bot_token_enc FROM telegram_settings WHERE id = 1")
    if not row or not row.get("bot_token_enc"):
        return ""
    return decrypt_value(row["bot_token_enc"])


def connect(token: str, username: str) -> None:
    """Store a validated bot token and reset ALL bot-specific state atomically.

    A bot swap must not retain the previous bot's offset (its update_ids are unrelated)
    or any chat binding made against it — a chat id is per (user, bot) pair, so every
    existing binding names a chat the new bot cannot be reached in. Each seat re-links
    with a fresh code of its own. Encryption happens before the txn (no nested checkout).

    **The one narrow swap race, documented rather than engineered around.** The router
    stops the poller before calling this and awaits the cancellation, and updates are
    processed strictly sequentially, so no handler is *running* here. A handler cancelled
    mid ``asyncio.to_thread(claim_link, …)`` can still let that thread's write land
    afterwards, binding a chat that belongs to the OLD bot. The row is inert — the new bot
    is never polled on that chat and cannot be — and one re-link clears it. This is the
    same class as the offset-skew window ``poller`` already documents.
    """
    enc = encrypt_value(token)
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM telegram_settings WHERE id = 1 FOR UPDATE")
        cur.execute(
            "UPDATE telegram_settings SET "
            "bot_token_enc = %s, bot_username = %s, poll_offset = 0, updated_at = now() "
            "WHERE id = 1",
            (enc, username),
        )
        cur.execute(_CLEAR_LINK_BINDINGS)


def disconnect() -> None:
    """Clear the bot (token, username, offset) and every seat's binding in one txn.

    A dead bot's links are dead: nothing can be delivered to them and nothing can arrive
    from them, so leaving a chat bound would only misreport "linked" in Settings.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM telegram_settings WHERE id = 1 FOR UPDATE")
        cur.execute(
            "UPDATE telegram_settings SET "
            "bot_token_enc = '', bot_username = '', poll_offset = 0, updated_at = now() "
            "WHERE id = 1"
        )
        cur.execute(_CLEAR_LINK_BINDINGS)


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


# ── Per-seat links ──────────────────────────────────────────────────────────

def get_link(user_id: int) -> dict | None:
    """My link row (joined to my user), or None when I have never minted a code."""
    return _link_row(
        pg_fetchone(_JOINED_LINK_SELECT + "WHERE l.user_id = %s", (int(user_id),))
    )


def mint_link_code(user_id: int) -> str:
    """Issue a fresh single-use code for this seat, releasing any binding it holds.

    Upsert: a seat that has never linked gets its row here. Minting a code deliberately
    frees the current device (that is what "link a different phone" means) and clears any
    pending confirmation batch with it, but KEEPS ``conversation_id`` — the assistant
    thread is the seat's own and survives a change of device.
    """
    code = _new_link_code()
    pg_execute(
        "INSERT INTO telegram_links (user_id, link_code) VALUES (%s, %s) "
        "ON CONFLICT (user_id) DO UPDATE SET "
        "link_code = EXCLUDED.link_code, chat_id = '', telegram_user_id = '', "
        "telegram_name = '', pending_msg_id = '', updated_at = now()",
        (int(user_id), code),
    )
    return code


def claim_link(code: str, chat_id: str, telegram_user_id: str, name: str) -> int | None:
    """Consume a link code and bind this chat to the seat that minted it.

    Compare-and-consume under ``FOR UPDATE OF telegram_links``: succeeds only when a
    non-empty code matches a row belonging to an ACTIVE seat. The code is single-use —
    cleared on success — and is never logged. Returns the claiming ``users.id``, or None
    when the code is unknown/expired, its seat is deactivated, or the chat is already
    bound to a different seat.

    **Security rests on the code being unguessable**, not on how it is compared: 128 bits
    from ``secrets.token_urlsafe``, single-use, tied to no enumerable identifier. The
    singleton's old ``hmac.compare_digest`` compared ONE stored code in Python; finding
    *which* row holds a code is necessarily an indexed lookup, so the comparison moves
    into the index probe. ``uq_telegram_links_code`` makes that row singular.

    Two different codes racing for the same chat are arbitrated by
    ``uq_telegram_links_chat``. The loser's ``UniqueViolation`` is caught OUTSIDE the
    transaction block, so its whole claim rolls back and its code survives for a retry.
    """
    if not code or not chat_id:
        return None
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            # FOR UPDATE OF l: lock the link row we are about to consume, never the
            # joined users row (which other requests legitimately read and write).
            cur.execute(
                "SELECT l.id, l.user_id FROM telegram_links l "
                "JOIN users u ON u.id = l.user_id "
                "WHERE l.link_code = %s AND l.link_code <> '' AND u.is_active "
                "FOR UPDATE OF l",
                (code,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            link_id, user_id = row[0], row[1]
            # Refuse rather than steal: this chat already speaks for another seat.
            cur.execute(
                "SELECT 1 FROM telegram_links WHERE chat_id = %s AND id <> %s",
                (str(chat_id), link_id),
            )
            if cur.fetchone() is not None:
                return None
            cur.execute(
                "UPDATE telegram_links SET chat_id = %s, telegram_user_id = %s, "
                "telegram_name = %s, link_code = '', pending_msg_id = '', "
                "updated_at = now() WHERE id = %s",
                (str(chat_id), str(telegram_user_id), name or "", link_id),
            )
            return int(user_id)
    except psycopg2.errors.UniqueViolation:
        # Lost the race for this chat_id. Nothing was written; the code is still live.
        logger.info("telegram: link claim lost the chat race")
        return None


def find_link(chat_id: str, telegram_user_id: str) -> dict | None:
    """Resolve an inbound update to exactly one ACTIVE seat, or to nothing.

    This is the authorization gate every inbound message and button press passes through
    — the per-seat replacement for the old singleton ``_is_authorized``. BOTH identifiers
    must match (the chat AND the Telegram account speaking in it), and the owning seat
    must still be active, so deactivating a user cuts their Telegram off on the next
    update with no extra bookkeeping. A group chat can never be bound (``claim_link`` is
    only reached from a private chat), so a group id simply finds nothing.
    """
    if not chat_id or not telegram_user_id:
        return None
    return _link_row(
        pg_fetchone(
            _JOINED_LINK_SELECT
            + "WHERE l.chat_id = %s AND l.chat_id <> '' "
            "AND l.telegram_user_id = %s AND u.is_active",
            (str(chat_id), str(telegram_user_id)),
        )
    )


def unlink(user_id: int) -> bool:
    """Release my own chat binding and code. True when something was actually bound."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE telegram_links SET chat_id = '', telegram_user_id = '', "
            "telegram_name = '', link_code = '', pending_msg_id = '', updated_at = now() "
            "WHERE user_id = %s AND chat_id <> ''",
            (int(user_id),),
        )
        return cur.rowcount > 0


# ── Send targets ────────────────────────────────────────────────────────────

def get_send_target(user_id: int) -> tuple[str, str] | None:
    """Return ``(token, chat_id)`` for one seat's link, or None.

    Token and chat id come back in ONE row so a concurrent bot swap can't hand back one
    bot's token paired with another's chat: ``connect``/``disconnect`` clear the bindings
    in the same transaction that mutates the token, so a mid-swap read yields either the
    old pair or no link at all.

    Requires an ACTIVE seat, mirroring ``find_link``. A deactivated user stops receiving
    targeted notifications at the same moment they stop being able to send.
    """
    row = pg_fetchone(
        "SELECT s.bot_token_enc, l.chat_id FROM telegram_settings s "
        "JOIN telegram_links l ON l.user_id = %s "
        "JOIN users u ON u.id = l.user_id "
        "WHERE s.id = 1 AND s.bot_token_enc <> '' AND l.chat_id <> '' AND u.is_active",
        (int(user_id),),
    )
    if not row:
        return None
    return decrypt_value(row["bot_token_enc"]), row["chat_id"]


def list_send_targets() -> list[tuple[str, str]]:
    """Return ``(token, chat_id)`` for EVERY active seat's link — the broadcast fan-out.

    The token is decrypted once and paired with each chat, so a broadcast costs one
    decrypt rather than one per recipient.
    """
    rows = pg_fetchall(
        "SELECT s.bot_token_enc, l.chat_id FROM telegram_settings s "
        "JOIN telegram_links l ON l.chat_id <> '' "
        "JOIN users u ON u.id = l.user_id "
        "WHERE s.id = 1 AND s.bot_token_enc <> '' AND u.is_active "
        "ORDER BY l.id"
    )
    if not rows:
        return []
    token = decrypt_value(rows[0]["bot_token_enc"])
    return [(token, r["chat_id"]) for r in rows]


# ── Conversation (per link) ─────────────────────────────────────────────────

def get_or_create_conversation(link: dict) -> str:
    """Return this link's conversation id, minting one if unset or since-deleted.

    The assistant API can delete the conversation out from under us (the column is a
    nullable FK with ON DELETE SET NULL), so a stored id is verified for existence and
    recreated when gone. Sequential poll processing means no concurrent creation.

    Both the probe and the mint are SCOPED to the link's own seat (#191's ownership
    primitives), so a Telegram thread is that person's conversation in their sidebar and
    nobody else's. This replaces the ``earliest_admin_id()`` stopgap #191 left here for
    exactly this issue to remove.
    """
    user_id = link["user_id"]
    conv_id = link.get("conversation_id") or ""
    if conv_id and conversation_exists(conv_id, user_id=user_id):
        return conv_id
    # own transaction — not nested under a held lock
    conv = create_conversation(user_id=user_id)
    new_id = conv["id"]
    pg_execute(
        "UPDATE telegram_links SET conversation_id = %s, updated_at = now() WHERE id = %s",
        (new_id, link["id"]),
    )
    link["conversation_id"] = new_id
    return new_id


# ── Confirmation batch gate (per link) ──────────────────────────────────────

def set_pending_msg(link_id: int, msg_id: str) -> None:
    pg_execute(
        "UPDATE telegram_links SET pending_msg_id = %s, updated_at = now() WHERE id = %s",
        (msg_id or "", int(link_id)),
    )


def clear_pending_msg(link_id: int, expected_msg_id: str) -> None:
    """Clear the pending batch marker ONLY if it still names the batch the caller saw.

    Conditional on purpose: the stale-batch auto-deny in ``service._run_turn`` reads the
    link row, denies that batch, then clears it. An unconditional clear would erase a
    batch installed in between and leave its buttons live with nothing to consume them.
    """
    if not expected_msg_id:
        return
    pg_execute(
        "UPDATE telegram_links SET pending_msg_id = '', updated_at = now() "
        "WHERE id = %s AND pending_msg_id = %s",
        (int(link_id), expected_msg_id),
    )


def try_consume_batch(link_id: int, batch_msg_id: str) -> bool:
    """Return True exactly once, when the given confirmation batch is fully resolved.

    Under ``FOR UPDATE`` on the LINK row: if its ``pending_msg_id`` still names this batch
    AND none of that message's tool results are still pending, clear ``pending_msg_id``
    and return True (caller runs the single continuation turn). Otherwise False. The lock
    serializes concurrent button presses so only one wins the "all resolved" transition —
    the continuation runs exactly once, never zero or twice. Locking the link rather than
    the singleton means two seats never wait on each other.

    This gates the CONTINUATION only. It is not an authorization check and never was —
    ``find_link`` authorizes the press and ``engine.resolve_confirmation`` proves the
    conversation belongs to the approver before it claims anything.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT pending_msg_id, conversation_id FROM telegram_links "
            "WHERE id = %s FOR UPDATE",
            (int(link_id),),
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
                "UPDATE telegram_links SET pending_msg_id = '', updated_at = now() "
                "WHERE id = %s",
                (int(link_id),),
            )
            return False
        # `executing` counts as unsettled too — a write stuck mid-execution (a crash
        # between claim and merge) must not let the continuation proceed.
        pending = sum(1 for r in (tr[0] or []) if is_unsettled_result(r.get("content")))
        if pending == 0:
            cur.execute(
                "UPDATE telegram_links SET pending_msg_id = '', updated_at = now() "
                "WHERE id = %s",
                (int(link_id),),
            )
            return True
        return False
