"""Telegram service — the update→assistant translation and the confirm handshake.

The single most important test module for issue #7. Hermetic: a scripted fake
``engine.chat`` (async generator yielding SSE lines) stands in for the real loop, and
fake client/store namespaces capture what would be sent. It pins the acceptance
criteria + the security-critical behaviors Codex flagged: auth gates, private-chat-only
linking, inline-keyboard confirmations, the resolve→continuation flow, multi-write
batching (continue only after ALL resolved), unauthorized-callback rejection, no-provider
degradation, and the ``notify_linked_user`` contract for #6.
"""

import json
import types

from telegram import client as real_client, service


class Harness:
    def __init__(self):
        self.sent = []      # (kind, chat_id, body, reply_markup)
        self.answers = []   # (callback_query_id, text)
        self.edits = []     # (chat_id, message_id, reply_markup)
        self.resolve_calls = []
        self.link_calls = []
        self.pending_sets = []
        self.cleared = 0
        self.chat_calls = []          # messages passed to each engine.chat call
        self._chat_scripts = []
        self._chat_idx = 0
        self._consume = iter(())
        self._provider = object()
        self.settings = {
            "connected": True, "linked": True,
            "linked_chat_id": "chat1", "linked_user_id": "user1", "linked_name": "Alex",
            "conversation_id": "conv1", "pending_msg_id": "",
        }


def _install(monkeypatch, h: Harness, *, chat_scripts=None, consume=None, provider=...):
    h._chat_scripts = chat_scripts or []
    if consume is not None:
        h._consume = iter(consume)
    if provider is not ...:
        h._provider = provider

    async def fake_chat(provider, registry, messages, tool_mode="normal", conversation_id=None, title_hint=None):
        h.chat_calls.append(messages)
        i = h._chat_idx
        h._chat_idx += 1
        script = h._chat_scripts[i] if i < len(h._chat_scripts) else []
        for evt in script:
            yield f"data: {json.dumps(evt)}\n\n"

    def fake_resolve(registry, conv, tuid, decision, msg_id=None):
        h.resolve_calls.append((conv, tuid, decision, msg_id))
        return {"tool": "crm_create_task", "decision": decision, "result": {"ok": True}}

    fake_engine = types.SimpleNamespace(chat=fake_chat, resolve_confirmation=fake_resolve)

    def link_chat(code, chat_id, user_id, name):
        h.link_calls.append((code, chat_id, user_id, name))
        return code == "GOODCODE"

    def try_consume_batch(msg_id):
        try:
            return next(h._consume)
        except StopIteration:
            return False

    fake_store = types.SimpleNamespace(
        get_settings=lambda: dict(h.settings),
        get_bot_token=lambda: ("TESTTOKEN" if h.settings.get("connected") else ""),
        get_or_create_conversation=lambda: "conv1",
        set_pending_msg=lambda m: (h.pending_sets.append(m), h.settings.__setitem__("pending_msg_id", m)),
        clear_pending_msg=lambda: (setattr_count(h), h.settings.__setitem__("pending_msg_id", "")),
        try_consume_batch=try_consume_batch,
        link_chat=link_chat,
    )

    fake_client = types.SimpleNamespace(
        send_text=lambda chat_id, text, token, reply_markup=None: h.sent.append(("text", chat_id, text, reply_markup)),
        send_html=lambda chat_id, md, token, reply_markup=None: h.sent.append(("html", chat_id, md, reply_markup)),
        answer_callback_query=lambda cb_id, token, text="": h.answers.append((cb_id, text)),
        edit_reply_markup=lambda chat_id, message_id, token, reply_markup=None: h.edits.append((chat_id, message_id, reply_markup)),
        TelegramError=real_client.TelegramError,
    )

    monkeypatch.setattr(service, "engine", fake_engine)
    monkeypatch.setattr(service, "store", fake_store)
    monkeypatch.setattr(service, "client", fake_client)
    monkeypatch.setattr(service, "get_ai_provider", lambda: h._provider)
    monkeypatch.setattr(service, "ToolRegistry", lambda: object())
    monkeypatch.setattr(service, "list_pending_tool_uses", lambda conv, msg: [])
    return h


def setattr_count(h):
    h.cleared += 1


def _msg(text, chat_id="chat1", user_id="user1", chat_type="private"):
    return {"message": {"text": text, "chat": {"id": chat_id, "type": chat_type},
                        "from": {"id": user_id, "first_name": "Alex"}}}


def _callback(data, user_id="user1", chat_id="chat1", message_id=55):
    return {"callback_query": {"id": "cb1", "data": data, "from": {"id": user_id},
                              "message": {"message_id": message_id, "chat": {"id": chat_id}}}}


def _texts(h):
    return [b for (k, _c, b, _m) in h.sent]


# ── Auth + linking ──────────────────────────────────────────────────────────

async def test_unlinked_sender_gets_link_help(monkeypatch):
    h = Harness()
    h.settings.update(linked=False, linked_chat_id="", linked_user_id="")
    _install(monkeypatch, h)
    await service.handle_update(_msg("how many deals?", user_id="stranger"))
    assert any("don't recognize" in t for t in _texts(h))
    assert h.chat_calls == []  # the assistant was never invoked for an unknown sender


async def test_link_command_in_private_chat_links(monkeypatch):
    h = Harness()
    _install(monkeypatch, h)
    await service.handle_update(_msg("/link GOODCODE", user_id="newuser"))
    assert h.link_calls and h.link_calls[0][0] == "GOODCODE"
    assert any("Linked" in t for t in _texts(h))


async def test_link_command_in_group_rejected(monkeypatch):
    h = Harness()
    _install(monkeypatch, h)
    await service.handle_update(_msg("/link GOODCODE", chat_type="group"))
    assert h.link_calls == []  # never consume a code from a group
    assert any("private chat" in t for t in _texts(h))


async def test_bad_link_code_reports_invalid(monkeypatch):
    h = Harness()
    _install(monkeypatch, h)
    await service.handle_update(_msg("/link WRONGCODE"))
    assert any("invalid" in t.lower() or "expired" in t.lower() for t in _texts(h))


# ── Chat + confirmations ────────────────────────────────────────────────────

async def test_authorized_message_streams_reply(monkeypatch):
    h = Harness()
    _install(monkeypatch, h, chat_scripts=[[{"type": "text", "text": "You have 3 open deals."},
                                            {"type": "done"}]])
    await service.handle_update(_msg("how many open deals?"))
    assert ("html", "chat1", "You have 3 open deals.", None) in h.sent


async def test_confirm_event_sends_keyboard_and_marks_batch(monkeypatch):
    h = Harness()
    script = [
        {"type": "text", "text": "I'll create that task."},
        {"type": "confirm", "tool": "crm_create_task", "args": {"title": "Call Acme"},
         "tool_use_id": "tu1", "msg_id": "m1", "description": "Create a task"},
        {"type": "done"},
    ]
    _install(monkeypatch, h, chat_scripts=[script])
    await service.handle_update(_msg("make a task to call Acme"))
    # Narration flushed as HTML before the button prompt.
    assert ("html", "chat1", "I'll create that task.", None) in h.sent
    # A confirm prompt with an inline Approve/Deny keyboard was sent.
    kb_sends = [s for s in h.sent if s[0] == "text" and s[3] and "inline_keyboard" in s[3]]
    assert kb_sends, "expected an inline-keyboard confirm message"
    buttons = kb_sends[0][3]["inline_keyboard"][0]
    assert {b["callback_data"] for b in buttons} == {"a:tu1", "d:tu1"}
    assert "Create a task" in kb_sends[0][2]  # server-derived description, not narration
    assert h.pending_sets == ["m1"]


async def test_callback_approve_resolves_and_continues(monkeypatch):
    h = Harness()
    h.settings["pending_msg_id"] = "m1"
    # Continuation turn (2nd engine.chat call) narrates the result.
    _install(monkeypatch, h,
             chat_scripts=[[{"type": "text", "text": "Done — task created."}, {"type": "done"}]],
             consume=[True])
    await service.handle_update(_callback("a:tu1"))
    assert h.resolve_calls == [("conv1", "tu1", "approve", "m1")]
    assert h.answers and "Done" in h.answers[0][1]
    assert h.edits and h.edits[0][2] is None  # keyboard stripped
    # The continuation ran (engine.chat called with empty messages) and streamed text.
    assert h.chat_calls == [[]]
    assert ("html", "chat1", "Done — task created.", None) in h.sent


async def test_two_confirms_continue_only_after_both_resolved(monkeypatch):
    h = Harness()
    h.settings["pending_msg_id"] = "m1"
    # First press: batch NOT done (False). Second press: done (True) → one continuation.
    _install(monkeypatch, h,
             chat_scripts=[[{"type": "text", "text": "All set."}, {"type": "done"}]],
             consume=[False, True])
    await service.handle_update(_callback("a:tu1"))
    assert h.chat_calls == []  # no continuation yet — sibling still pending
    await service.handle_update(_callback("d:tu2"))
    assert h.resolve_calls == [("conv1", "tu1", "approve", "m1"), ("conv1", "tu2", "deny", "m1")]
    assert h.chat_calls == [[]]  # continuation ran exactly once, after the second


async def test_unauthorized_callback_rejected(monkeypatch):
    h = Harness()
    h.settings["pending_msg_id"] = "m1"
    _install(monkeypatch, h)
    await service.handle_update(_callback("a:tu1", user_id="intruder"))
    assert h.answers and "authorized" in h.answers[0][1].lower()
    assert h.resolve_calls == []  # the intruder never triggered a write


async def test_expired_callback_when_no_pending(monkeypatch):
    h = Harness()
    h.settings["pending_msg_id"] = ""  # nothing pending
    _install(monkeypatch, h)
    await service.handle_update(_callback("a:tu1"))
    assert h.resolve_calls == []
    assert h.answers and "expired" in h.answers[0][1].lower()


async def test_no_provider_degrades_gracefully(monkeypatch):
    h = Harness()
    _install(monkeypatch, h, provider=None)
    await service.handle_update(_msg("hello"))
    assert any("No AI provider" in t for t in _texts(h))
    assert h.chat_calls == []


# ── Outbound contract for #6 ────────────────────────────────────────────────

def test_notify_linked_user_sends_when_linked(monkeypatch):
    h = Harness()
    _install(monkeypatch, h)
    assert service.notify_linked_user("Reminder: follow up with Acme") is True
    assert ("text", "chat1", "Reminder: follow up with Acme", None) in h.sent


def test_notify_linked_user_false_when_unlinked(monkeypatch):
    h = Harness()
    h.settings.update(linked=False, linked_chat_id="")
    _install(monkeypatch, h)
    assert service.notify_linked_user("anything") is False
    assert h.sent == []


def test_notify_linked_user_false_when_disconnected(monkeypatch):
    h = Harness()
    h.settings.update(connected=False)
    _install(monkeypatch, h)
    assert service.notify_linked_user("anything") is False
