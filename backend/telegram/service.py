"""Telegram ↔ assistant glue: drive ``engine.chat`` from Telegram updates.

``handle_update`` runs on the MAIN event loop (the poll task offloads only the
blocking ``getUpdates`` via ``asyncio.to_thread``), so ``engine.chat`` executes on the
same loop as the SSE endpoint — keeping the provider's loop-bound cached client
consistent. Every synchronous DB/network call inside these coroutines is offloaded
with ``asyncio.to_thread`` so the loop is never blocked.

Write confirmations reuse the engine's server-authoritative flow verbatim: a ``confirm``
event becomes an inline Approve/Deny keyboard; a button press resolves it via
``engine.resolve_confirmation`` and, once every write in the batch is resolved, runs one
empty-messages continuation turn (which may itself surface more confirmations).

``notify_linked_user`` is the pure-sync outbound path the #6 heartbeat/notifications
module calls (contract frozen with teammate issue-6). It has no event-loop or engine
dependency and returns False when Telegram isn't connected/linked.
"""

import asyncio
import json
import logging

from assistant import engine
from assistant.history import list_pending_tool_uses
from assistant.registry import ToolRegistry
from crm import gtd_common, gtd_service
from providers import get_ai_provider

from . import client, store

logger = logging.getLogger(__name__)

_LINK_HELP = (
    "I don't recognize this account yet. To connect me, open CakeCRM → Settings → "
    "Telegram and tap “Link this device” (or copy the link code shown there)."
)
_NO_PROVIDER = (
    "⚠️ No AI provider is connected, so I can't chat yet. Add one in CakeCRM → Settings "
    "to start using the assistant."
)


# ── Outbound (public contract for issue #6) ─────────────────────────────────

def notify_linked_user(text: str) -> bool:
    """Send a plain-text message to the linked Telegram user. Pure sync, safe anywhere.

    Returns False when Telegram isn't connected/linked or the send fails — never raises.
    This is the notification delivery channel #6 (heartbeat/notifications)
    consumes; the signature is frozen by team agreement (do not change name/path/shape).
    """
    if not text:
        return False
    try:
        target = store.get_send_target()  # (token, chat_id) in one consistent read
        if target is None:
            return False
        token, chat_id = target
        client.send_text(chat_id, text, token)
        return True
    except client.TelegramError as e:
        logger.warning("notify_linked_user send failed: status=%s", e.status)
        return False
    except Exception:
        logger.exception("notify_linked_user failed")
        return False


# ── Inbound dispatch ────────────────────────────────────────────────────────

async def handle_update(update: dict) -> None:
    """Route one Telegram update. Runs on the main loop; never raises to the poller."""
    try:
        if "callback_query" in update:
            await _handle_callback(update["callback_query"])
        elif "message" in update:
            await _handle_message(update["message"])
    except Exception:
        logger.exception("telegram handle_update failed")


async def _handle_message(msg: dict) -> None:
    text = (msg.get("text") or "").strip()
    chat = msg.get("chat") or {}
    sender = msg.get("from") or {}
    chat_id = str(chat.get("id") or "")
    chat_type = chat.get("type") or ""
    user_id = str(sender.get("id") or "")
    name = sender.get("first_name") or sender.get("username") or "there"

    token = await asyncio.to_thread(store.get_bot_token)
    if not token or not chat_id:
        return

    # /start <code> and /link <code> are the linking commands (deep link sends /start).
    if text.startswith("/start") or text.startswith("/link"):
        code = _parse_command_arg(text)
        if code:
            if chat_type != "private":
                await _send_text(chat_id, "Please link me from a private chat, not a group.", token)
                return
            linked = await asyncio.to_thread(store.link_chat, code, chat_id, user_id, name)
            if linked:
                await _send_text(
                    chat_id,
                    f"✅ Linked! Hi {name} — you can now chat with your CakeCRM assistant "
                    "right here. Ask me anything about your contacts, deals, and tasks.",
                    token,
                )
            else:
                await _send_text(
                    chat_id,
                    "That link code is invalid or has expired. Generate a fresh one in "
                    "CakeCRM → Settings → Telegram.",
                    token,
                )
            return
        # Bare /start: greet if already linked, else explain how to link.
        s = await asyncio.to_thread(store.get_settings)
        if _is_authorized(s, chat_id, user_id):
            await _send_text(chat_id, f"Hi {name}! You're linked. Ask me anything about your CRM.", token)
        else:
            await _send_text(chat_id, _LINK_HELP, token)
        return

    # Ignore non-text updates (photos, stickers, voice, service events): they carry no
    # text, and feeding an empty message to the assistant would error and would also
    # auto-deny a pending confirmation batch. Text-only for v1.
    if not text:
        return

    # Non-command: must come from the linked user in the linked chat.
    s = await asyncio.to_thread(store.get_settings)
    if not _is_authorized(s, chat_id, user_id):
        await _send_text(chat_id, _LINK_HELP, token)
        return

    if await _try_capture(chat_id, text, token):
        return

    await _run_turn(s, token, user_text=text)


async def _try_capture(chat_id: str, text: str, token: str) -> bool:
    """Deterministic GTD capture intercept — returns True when it handled the message.

    "capture buy vanilla" / "/capture buy vanilla" creates an inbox todo BEFORE the
    model runs: zero AI cost, zero confirmation friction, and it works with no
    provider configured at all. That is what makes deferring a public capture link
    reasonable for anyone who has Telegram linked.

    GTD mode only — in normal mode "capture ..." is just conversation.
    """
    mode = await asyncio.to_thread(_task_mode)
    if mode != "gtd":
        return False
    payload = gtd_common.parse_capture(text)
    if payload is None:
        return False
    if not payload:
        await _send_text(chat_id, "Send `capture <what's on your mind>` to add to your inbox.", token)
        return True
    try:
        todo = await asyncio.to_thread(gtd_service.capture, payload, "telegram")
    except gtd_common.ValidationError as e:
        await _send_text(chat_id, f"Couldn't capture that: {e}", token)
        return True
    except Exception:
        logger.exception("telegram: capture failed")
        await _send_text(chat_id, "Couldn't capture that — try again.", token)
        return True
    await _send_text(chat_id, f"Captured: {todo['title']}", token)
    return True


def _task_mode() -> str:
    """Current task mode; fail-safe to the product default ('gtd' since #102), matching
    the other three readers.

    THIS except covers an import failure only — `get_task_mode` never raises. The path
    that actually yields a wrong 'gtd' on a deliberately-normal install is
    `get_task_mode`'s OWN except: one failed `pg_fetchone` (pool exhaustion, a dropped
    connection, a statement timeout), which does not mean the database is broken — the
    next query may well succeed. In that window `capture the Henderson quote` is
    intercepted and filed as an inbox todo instead of reaching the assistant.

    Accepted, not overlooked: the blast radius is one misrouted message, the user is
    told what happened ("Captured: …"), and the todo lands in the same `tasks` store
    either mode reads. A retry here would buy little and add a hot-path round trip.
    """
    try:
        from crm.service import get_task_mode
        return get_task_mode()
    except Exception:
        return "gtd"


async def _handle_callback(cb: dict) -> None:
    cb_id = cb.get("id") or ""
    sender = cb.get("from") or {}
    user_id = str(sender.get("id") or "")
    data = cb.get("data") or ""
    message = cb.get("message") or {}
    msg_chat_id = str(((message.get("chat") or {}).get("id")) or "")
    message_id = message.get("message_id")

    token = await asyncio.to_thread(store.get_bot_token)
    if not token:
        return
    s = await asyncio.to_thread(store.get_settings)

    # Authorization: only the linked user may resolve confirmations.
    if not _is_authorized(s, msg_chat_id, user_id):
        await asyncio.to_thread(client.answer_callback_query, cb_id, token, "Not authorized.")
        return

    decision, batch, tool_use_id = _parse_callback_data(data)
    if not decision:
        await asyncio.to_thread(client.answer_callback_query, cb_id, token, "Unknown action.")
        return

    conv = s.get("conversation_id")
    pending_msg_id = s.get("pending_msg_id")
    if not conv or not pending_msg_id:
        await asyncio.to_thread(client.answer_callback_query, cb_id, token, "This confirmation has expired.")
        await _strip_keyboard(msg_chat_id, message_id, token)
        return

    # Reject a button whose batch is no longer the active one (a superseded/stale
    # keyboard). Resolving it against the current pending_msg_id could hit a different
    # write under a positional-id provider (Gemini reuses call_0 across turns).
    if not str(pending_msg_id).startswith(batch):
        await asyncio.to_thread(client.answer_callback_query, cb_id, token, "This confirmation is no longer active.")
        await _strip_keyboard(msg_chat_id, message_id, token)
        return

    registry = ToolRegistry()
    result = await asyncio.to_thread(
        engine.resolve_confirmation, registry, conv, tool_use_id, decision, pending_msg_id
    )
    outcome = _outcome_text(decision, result)
    await asyncio.to_thread(client.answer_callback_query, cb_id, token, outcome)
    await _strip_keyboard(msg_chat_id, message_id, token)

    # Continue the assistant turn only once EVERY write in this batch is resolved.
    should_continue = await asyncio.to_thread(store.try_consume_batch, pending_msg_id)
    if should_continue:
        await _run_turn(s, token, user_text=None)


# ── Turn driver ─────────────────────────────────────────────────────────────

async def _run_turn(settings: dict, token: str, user_text: str | None) -> None:
    """Drive one assistant turn (or a continuation when ``user_text`` is None)."""
    chat_id = settings.get("linked_chat_id")
    if not chat_id:
        return

    # A new user message cancels any still-open confirmation batch FIRST — before the
    # provider check — so abandoned Approve/Deny buttons can't later execute a write the
    # user has moved on from, even when no provider is configured. The pending batch
    # lives on the existing conversation, so deny against that id directly.
    if user_text is not None:
        stale = settings.get("pending_msg_id")
        stale_conv = settings.get("conversation_id")
        if stale and stale_conv:
            await _auto_deny_batch(stale_conv, stale)
            await asyncio.to_thread(store.clear_pending_msg)

    provider = await asyncio.to_thread(get_ai_provider)
    if provider is None:
        await _send_text(chat_id, _NO_PROVIDER, token)
        return

    conv = await asyncio.to_thread(store.get_or_create_conversation)

    registry = ToolRegistry()
    messages = [] if user_text is None else [{"role": "user", "content": user_text}]

    buffer = ""
    try:
        async for line in engine.chat(provider, registry, messages, tool_mode="normal", conversation_id=conv):
            evt = _parse_sse(line)
            if not evt:
                continue
            etype = evt.get("type")
            if etype == "text":
                buffer += evt.get("text", "")
            elif etype == "confirm":
                buffer = await _flush(chat_id, buffer, token)
                await _send_confirm(chat_id, token, evt)
            elif etype == "error":
                buffer = await _flush(chat_id, buffer, token)
                await _send_text(chat_id, "⚠️ " + str(evt.get("error") or "The assistant hit an error."), token)
                return
            elif etype == "done":
                await _flush(chat_id, buffer, token)
                return
            # tool_start / tool_args / tool_end / conversation_id / usage → ignored
        # Generator ended without an explicit done — flush whatever we have.
        await _flush(chat_id, buffer, token)
    except Exception:
        logger.exception("telegram _run_turn crashed")
        await _send_text(chat_id, "⚠️ The assistant hit an unexpected error and stopped.", token)


async def _auto_deny_batch(conv: str, msg_id: str) -> None:
    """Deny any still-pending writes on a message (idempotent) before a new turn."""
    pending = await asyncio.to_thread(list_pending_tool_uses, conv, msg_id)
    registry = ToolRegistry()
    for tuid in pending:
        await asyncio.to_thread(engine.resolve_confirmation, registry, conv, tuid, "deny", msg_id)


# ── Send helpers (all offloaded; never raise to the loop) ────────────────────

async def _flush(chat_id, buffer: str, token: str) -> str:
    if buffer.strip():
        await _send_html(chat_id, buffer, token)
    return ""


async def _send_html(chat_id, markdown: str, token: str, reply_markup: dict | None = None) -> None:
    try:
        await asyncio.to_thread(client.send_html, chat_id, markdown, token, reply_markup)
    except client.TelegramError as e:
        logger.warning("telegram send_html failed: status=%s", e.status)


async def _send_text(chat_id, text: str, token: str, reply_markup: dict | None = None) -> None:
    try:
        await asyncio.to_thread(client.send_text, chat_id, text, token, reply_markup)
    except client.TelegramError as e:
        logger.warning("telegram send_text failed: status=%s", e.status)


async def _send_confirm(chat_id, token: str, evt: dict) -> None:
    """Send an inline Approve/Deny keyboard for one pending write and mark the batch.

    The prompt text is SERVER-derived from the confirm event (tool + args + description),
    never model narration. callback_data carries only ``a:``/``d:`` + tool_use_id (well
    under Telegram's 64-byte cap); the batch's msg_id lives on the singleton.
    """
    tool_use_id = evt.get("tool_use_id") or ""
    msg_id = evt.get("msg_id") or ""
    desc = evt.get("description") or evt.get("tool") or "this action"
    args_str = _format_args(evt.get("args"))
    body = f"🔧 {desc}"
    if args_str:
        body += f"\n\n{args_str}"
    body += "\n\nApprove this action?"
    # Bind the button to its originating batch via an 8-char msg_id prefix. On a press
    # we require this to still match the singleton's pending_msg_id, so a stale button
    # from a superseded batch is rejected — otherwise it would resolve against the
    # CURRENT batch's msg_id, and a positional-id provider (Gemini reuses call_0 across
    # turns) could then approve the wrong write. Fits Telegram's 64-byte callback cap.
    batch = msg_id[:8]
    markup = {
        "inline_keyboard": [[
            {"text": "✅ Approve", "callback_data": f"a:{batch}:{tool_use_id}"},
            {"text": "❌ Deny", "callback_data": f"d:{batch}:{tool_use_id}"},
        ]]
    }
    if msg_id:
        await asyncio.to_thread(store.set_pending_msg, msg_id)
    await _send_text(chat_id, body, token, reply_markup=markup)


async def _strip_keyboard(chat_id, message_id, token: str) -> None:
    if message_id:
        await asyncio.to_thread(client.edit_reply_markup, chat_id, message_id, token, None)


# ── Pure helpers ────────────────────────────────────────────────────────────

def _is_authorized(settings: dict, chat_id: str, user_id: str) -> bool:
    return bool(
        settings.get("linked")
        and chat_id
        and str(settings.get("linked_chat_id")) == str(chat_id)
        and str(settings.get("linked_user_id")) == str(user_id)
    )


def _parse_command_arg(text: str) -> str:
    """Return the argument after ``/start`` or ``/link`` (bounded), else ''."""
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        return ""
    return parts[1].strip()[:128]


def _parse_callback_data(data: str) -> tuple[str | None, str, str]:
    """Parse ``a:<batch8>:<tuid>`` / ``d:<batch8>:<tuid>`` → (decision, batch8, tuid)."""
    if data.startswith(("a:", "d:")):
        decision = "approve" if data[0] == "a" else "deny"
        batch, _, tuid = data[2:].partition(":")
        if tuid:
            return decision, batch, tuid
    return None, "", ""


def _outcome_text(decision: str, result: dict) -> str:
    if isinstance(result, dict) and result.get("status") == "already_resolved":
        return "Already handled."
    if decision == "deny":
        return "❌ Denied."
    inner = result.get("result") if isinstance(result, dict) else None
    if isinstance(inner, dict) and inner.get("error"):
        return "⚠️ Action failed."
    return "✅ Done."


def _format_args(args) -> str:
    """Compact, human-readable, plain-text rendering of tool args (sent without HTML)."""
    if not isinstance(args, dict) or not args:
        return ""
    lines = []
    for k, v in args.items():
        val = v if isinstance(v, str) else json.dumps(v, default=str)
        if len(val) > 200:
            val = val[:197] + "..."
        lines.append(f"• {k}: {val}")
    return "\n".join(lines)


def _parse_sse(line: str) -> dict | None:
    if not line.startswith("data:"):
        return None
    payload = line[len("data:"):].strip()
    if not payload:
        return None
    try:
        return json.loads(payload)
    except ValueError:
        return None
