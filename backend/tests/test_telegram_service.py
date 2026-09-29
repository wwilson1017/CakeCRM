"""Telegram service — the update→assistant translation and the confirm handshake.

The single most important test module for the feature. Hermetic: a scripted fake
``engine.chat`` (async generator yielding SSE lines) stands in for the real loop, and
fake client/store namespaces capture what would be sent. It pins the acceptance
criteria + the security-critical behaviors Codex flagged: auth gates, private-chat-only
linking, inline-keyboard confirmations, the resolve→continuation flow, multi-write
batching (continue only after ALL resolved), unauthorized-callback rejection, no-provider
degradation, and the outbound delivery contract.

Since #193 it also pins the per-seat half: every inbound update resolves to ONE active
seat through ``store.find_link``, that seat's identity reaches ``ToolRegistry``,
``engine.chat`` and ``engine.resolve_confirmation``, a Telegram capture is stamped with
its owner, and outbound delivery is targeted or broadcast rather than install-wide.
"""

import json
import types

from telegram import client as real_client, service

MEMBER = {"id": 2, "email": "member@cakecrm.test", "name": "Member",
          "role": "member", "is_active": True}


class Harness:
    def __init__(self):
        self.sent = []      # (kind, chat_id, body, reply_markup)
        self.answers = []   # (callback_query_id, text)
        self.edits = []     # (chat_id, message_id, reply_markup)
        self.resolve_calls = []
        self.claim_calls = []
        self.pending_sets = []
        self.cleared = []             # (link_id, expected_msg_id) per conditional clear
        self.chat_calls = []          # messages passed to each engine.chat call
        self.chat_users = []          # the `user` each engine.chat call was given
        self.resolve_users = []       # the `user` each resolve_confirmation call was given
        self.registry_users = []      # the `user` each ToolRegistry was built with (#190)
        self.captures = []            # (text, source, owner_id) per gtd capture
        self.pending_tool_uses = []   # what list_pending_tool_uses returns (stale-batch tests)
        self.consume_calls = []       # (link_id, batch) per try_consume_batch
        self._chat_scripts = []
        self._chat_idx = 0
        self._consume = iter(())
        self._provider = object()
        self.connected = True
        self.linked = True
        # The row store.find_link resolves for this chat. One seat, one chat.
        self.link = {
            "id": 7, "user_id": 2, "link_code": "", "chat_id": "chat1",
            "telegram_user_id": "user1", "telegram_name": "Alex",
            "conversation_id": "conv1", "pending_msg_id": "",
            "user": dict(MEMBER),
        }
        # Extra seats reachable for a broadcast.
        self.send_targets = [("TESTTOKEN", "chat1")]


def _install(monkeypatch, h: Harness, *, chat_scripts=None, consume=None, provider=...):
    h._chat_scripts = chat_scripts or []
    if consume is not None:
        h._consume = iter(consume)
    if provider is not ...:
        h._provider = provider

    async def fake_chat(provider, registry, messages, tool_mode="normal", conversation_id=None,
                        title_hint=None, context=None, *, user):
        # `user` is required keyword-only since #191. Since #193 a Telegram turn has a
        # real seat behind it, so this must be that seat's row — never None.
        h.chat_calls.append(messages)
        h.chat_users.append(user)
        i = h._chat_idx
        h._chat_idx += 1
        script = h._chat_scripts[i] if i < len(h._chat_scripts) else []
        for evt in script:
            yield f"data: {json.dumps(evt)}\n\n"

    def fake_resolve(registry, conv, tuid, decision, msg_id=None, *, user):
        h.resolve_calls.append((conv, tuid, decision, msg_id))
        h.resolve_users.append(user)
        return {"tool": "crm_create_todo", "decision": decision, "result": {"ok": True}}

    fake_engine = types.SimpleNamespace(chat=fake_chat, resolve_confirmation=fake_resolve)

    def claim_link(code, chat_id, tg_user_id, name):
        h.claim_calls.append((code, chat_id, tg_user_id, name))
        if code != "GOODCODE":
            return None
        h.linked = True
        h.link.update(chat_id=chat_id, telegram_user_id=tg_user_id, telegram_name=name)
        return h.link["user_id"]

    def find_link(chat_id, tg_user_id):
        if not h.linked:
            return None
        if chat_id != h.link["chat_id"] or tg_user_id != h.link["telegram_user_id"]:
            return None
        return dict(h.link, user=dict(h.link["user"]))

    def try_consume_batch(link_id, batch):
        h.consume_calls.append((link_id, batch))
        try:
            return next(h._consume)
        except StopIteration:
            return False

    def set_pending(link_id, msg_id):
        h.pending_sets.append((link_id, msg_id))
        h.link["pending_msg_id"] = msg_id

    def clear_pending(link_id, expected):
        h.cleared.append((link_id, expected))
        if h.link["pending_msg_id"] == expected:
            h.link["pending_msg_id"] = ""

    fake_store = types.SimpleNamespace(
        get_bot_token=lambda: ("TESTTOKEN" if h.connected else ""),
        get_send_target=lambda uid: (
            ("TESTTOKEN", h.link["chat_id"])
            if h.connected and h.linked and uid == h.link["user_id"] else None
        ),
        list_send_targets=lambda: (list(h.send_targets) if h.connected and h.linked else []),
        get_or_create_conversation=lambda link: link.get("conversation_id") or "conv1",
        set_pending_msg=set_pending,
        clear_pending_msg=clear_pending,
        try_consume_batch=try_consume_batch,
        claim_link=claim_link,
        find_link=find_link,
    )

    fake_client = types.SimpleNamespace(
        send_text=lambda chat_id, text, token, reply_markup=None: h.sent.append(("text", chat_id, text, reply_markup)),
        send_html=lambda chat_id, md, token, reply_markup=None: h.sent.append(("html", chat_id, md, reply_markup)),
        answer_callback_query=lambda cb_id, token, text="": h.answers.append((cb_id, text)),
        edit_reply_markup=lambda chat_id, message_id, token, reply_markup=None: h.edits.append((chat_id, message_id, reply_markup)),
        TelegramError=real_client.TelegramError,
    )

    def fake_registry(*, user=None, background=False):
        h.registry_users.append(user)
        return types.SimpleNamespace(deal_refs={})  # the real registry's per-turn map (#238)

    monkeypatch.setattr(service, "engine", fake_engine)
    monkeypatch.setattr(service, "store", fake_store)
    monkeypatch.setattr(service, "client", fake_client)
    monkeypatch.setattr(service, "get_ai_provider", lambda: h._provider)
    monkeypatch.setattr(service, "ToolRegistry", fake_registry)
    monkeypatch.setattr(service, "list_pending_tool_uses", lambda conv, msg: list(h.pending_tool_uses))
    return h


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
    h.linked = False
    _install(monkeypatch, h)
    await service.handle_update(_msg("how many deals?", user_id="stranger"))
    assert any("don't recognize" in t for t in _texts(h))
    assert h.chat_calls == []  # the assistant was never invoked for an unknown sender


async def test_a_different_telegram_account_in_a_linked_chat_is_not_the_seat(monkeypatch):
    """find_link matches BOTH identifiers — the chat alone does not authorize."""
    h = Harness()
    _install(monkeypatch, h)
    await service.handle_update(_msg("how many deals?", chat_id="chat1", user_id="intruder"))
    assert h.chat_calls == []
    assert any("don't recognize" in t for t in _texts(h))


async def test_link_command_in_private_chat_links(monkeypatch):
    h = Harness()
    h.linked = False
    _install(monkeypatch, h)
    await service.handle_update(_msg("/link GOODCODE", user_id="newuser"))
    assert h.claim_calls and h.claim_calls[0][0] == "GOODCODE"
    assert any("Linked" in t for t in _texts(h))


async def test_link_reply_names_the_cakecrm_seat_it_bound(monkeypatch):
    """A mis-pasted code must be visible before the assistant writes as someone else."""
    h = Harness()
    h.linked = False
    _install(monkeypatch, h)
    await service.handle_update(_msg("/link GOODCODE", user_id="user1"))
    assert any("Member" in t for t in _texts(h))


async def test_link_command_in_group_rejected(monkeypatch):
    h = Harness()
    _install(monkeypatch, h)
    await service.handle_update(_msg("/link GOODCODE", chat_type="group"))
    assert h.claim_calls == []  # never consume a code from a group
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


async def test_the_turn_runs_as_the_linked_seat(monkeypatch):
    """#193's whole point: a Telegram write is attributed to the person holding the phone.

    Before this, `telegram/service` built `ToolRegistry()` with no user at three sites, so
    a CRM write from Telegram recorded NULL and `owner: "me"` answered "this run has
    nobody — it is unattended" to a human who was very much attended.
    """
    h = Harness()
    _install(monkeypatch, h, chat_scripts=[[{"type": "done"}]])
    await service.handle_update(_msg("how many open deals?"))
    assert h.chat_users == [MEMBER]
    assert h.registry_users == [MEMBER]


async def test_confirm_event_sends_keyboard_and_marks_batch(monkeypatch):
    h = Harness()
    script = [
        {"type": "text", "text": "I'll create that todo."},
        {"type": "confirm", "tool": "crm_create_todo", "args": {"title": "Call Acme"},
         "tool_use_id": "tu1", "msg_id": "m1", "description": "Create a todo"},
        {"type": "done"},
    ]
    _install(monkeypatch, h, chat_scripts=[script])
    await service.handle_update(_msg("make a todo to call Acme"))
    # Narration flushed as HTML before the button prompt.
    assert ("html", "chat1", "I'll create that todo.", None) in h.sent
    # A confirm prompt with an inline Approve/Deny keyboard was sent.
    kb_sends = [s for s in h.sent if s[0] == "text" and s[3] and "inline_keyboard" in s[3]]
    assert kb_sends, "expected an inline-keyboard confirm message"
    buttons = kb_sends[0][3]["inline_keyboard"][0]
    assert {b["callback_data"] for b in buttons} == {"a:m1:tu1", "d:m1:tu1"}
    assert "Create a todo" in kb_sends[0][2]  # server-derived description, not narration
    # The batch marker rides the pressing seat's OWN link row, not the singleton.
    assert h.pending_sets == [(7, "m1")]


async def test_callback_approve_resolves_and_continues(monkeypatch):
    h = Harness()
    h.link["pending_msg_id"] = "m1"
    # Continuation turn (2nd engine.chat call) narrates the result.
    _install(monkeypatch, h,
             chat_scripts=[[{"type": "text", "text": "Done — todo created."}, {"type": "done"}]],
             consume=[True])
    await service.handle_update(_callback("a:m1:tu1"))
    assert h.resolve_calls == [("conv1", "tu1", "approve", "m1")]
    assert h.answers and "Done" in h.answers[0][1]
    assert h.edits and h.edits[0][2] is None  # keyboard stripped
    # The continuation ran (engine.chat called with empty messages) and streamed text.
    assert h.chat_calls == [[]]
    assert ("html", "chat1", "Done — todo created.", None) in h.sent
    # The approver is a real seat: the write records who approved it, and
    # resolve_confirmation can prove the conversation belongs to them.
    assert h.resolve_users == [MEMBER] and h.chat_users == [MEMBER]
    # ONE registry resolves the write and drives the continuation, so the reply can link
    # a deal the approved write returned (#238).
    assert h.registry_users == [MEMBER]


async def test_batch_is_consumed_against_the_pressing_seats_link(monkeypatch):
    h = Harness()
    h.link["pending_msg_id"] = "m1"
    _install(monkeypatch, h, chat_scripts=[[{"type": "done"}]], consume=[True])
    await service.handle_update(_callback("a:m1:tu1"))
    assert h.consume_calls == [(7, "m1")]


async def test_two_confirms_continue_only_after_both_resolved(monkeypatch):
    h = Harness()
    h.link["pending_msg_id"] = "m1"
    # First press: batch NOT done (False). Second press: done (True) → one continuation.
    _install(monkeypatch, h,
             chat_scripts=[[{"type": "text", "text": "All set."}, {"type": "done"}]],
             consume=[False, True])
    await service.handle_update(_callback("a:m1:tu1"))
    assert h.chat_calls == []  # no continuation yet — sibling still pending
    await service.handle_update(_callback("d:m1:tu2"))
    assert h.resolve_calls == [("conv1", "tu1", "approve", "m1"), ("conv1", "tu2", "deny", "m1")]
    assert h.chat_calls == [[]]  # continuation ran exactly once, after the second


async def test_unauthorized_callback_rejected(monkeypatch):
    h = Harness()
    h.link["pending_msg_id"] = "m1"
    _install(monkeypatch, h)
    await service.handle_update(_callback("a:m1:tu1", user_id="intruder"))
    assert h.answers and "authorized" in h.answers[0][1].lower()
    assert h.resolve_calls == []  # the intruder never triggered a write


async def test_callback_from_an_unlinked_chat_is_rejected(monkeypatch):
    """A keyboard left over from before an unlink must not still resolve writes."""
    h = Harness()
    h.link["pending_msg_id"] = "m1"
    h.linked = False
    _install(monkeypatch, h)
    await service.handle_update(_callback("a:m1:tu1"))
    assert h.resolve_calls == []
    assert h.answers and "authorized" in h.answers[0][1].lower()


async def test_expired_callback_when_no_pending(monkeypatch):
    h = Harness()
    h.link["pending_msg_id"] = ""  # nothing pending
    _install(monkeypatch, h)
    await service.handle_update(_callback("a:m1:tu1"))
    assert h.resolve_calls == []
    assert h.answers and "expired" in h.answers[0][1].lower()


async def test_stale_batch_button_is_rejected_not_resolved(monkeypatch):
    # The current pending batch is m2 (a superseded turn); a leftover m1 button must NOT
    # resolve against m2 — critical for Gemini's reused positional tool ids.
    h = Harness()
    h.link["pending_msg_id"] = "m2deadbe"
    _install(monkeypatch, h)
    await service.handle_update(_callback("a:m1:tu1"))  # batch prefix "m1" != "m2deadbe"
    assert h.resolve_calls == []
    assert h.answers and "no longer active" in h.answers[0][1].lower()


async def test_no_provider_degrades_gracefully(monkeypatch):
    h = Harness()
    _install(monkeypatch, h, provider=None)
    await service.handle_update(_msg("hello"))
    assert any("No AI provider" in t for t in _texts(h))
    assert h.chat_calls == []


# ── Outbound delivery (targeted vs broadcast) ───────────────────────────────

def test_notify_user_telegram_sends_to_that_seats_chat(monkeypatch):
    h = Harness()
    _install(monkeypatch, h)
    assert service.notify_user_telegram(2, "Todo due: follow up with Acme") is True
    assert ("text", "chat1", "Todo due: follow up with Acme", None) in h.sent


def test_notify_user_telegram_false_for_a_seat_with_no_link(monkeypatch):
    """A targeted notification must NOT fall back to somebody else's chat."""
    h = Harness()
    _install(monkeypatch, h)
    assert service.notify_user_telegram(999, "private thing") is False
    assert h.sent == []


def test_notify_user_telegram_false_when_unlinked(monkeypatch):
    h = Harness()
    h.linked = False
    _install(monkeypatch, h)
    assert service.notify_user_telegram(2, "anything") is False
    assert h.sent == []


def test_notify_user_telegram_false_when_disconnected(monkeypatch):
    h = Harness()
    h.connected = False
    _install(monkeypatch, h)
    assert service.notify_user_telegram(2, "anything") is False


def test_broadcast_reaches_every_linked_chat(monkeypatch):
    h = Harness()
    h.send_targets = [("TESTTOKEN", "chatA"), ("TESTTOKEN", "chatB")]
    _install(monkeypatch, h)
    assert service.broadcast_telegram("Daily digest") is True
    assert [c for (_k, c, _b, _m) in h.sent] == ["chatA", "chatB"]


def test_broadcast_false_when_nobody_is_linked(monkeypatch):
    h = Harness()
    h.linked = False
    _install(monkeypatch, h)
    assert service.broadcast_telegram("Daily digest") is False


def _raise_tg(*a, **k):
    raise real_client.TelegramError("boom", status=400)


def _raise_generic(*a, **k):
    raise RuntimeError("boom")


def test_notify_user_telegram_false_on_send_error(monkeypatch):
    # The delivery contract: never raises. A TelegramError from send → False.
    h = Harness()
    _install(monkeypatch, h)
    monkeypatch.setattr(service.client, "send_text", _raise_tg)
    assert service.notify_user_telegram(2, "hi") is False


def test_notify_user_telegram_false_on_unexpected_error(monkeypatch):
    h = Harness()
    _install(monkeypatch, h)
    monkeypatch.setattr(service.client, "send_text", _raise_generic)
    assert service.notify_user_telegram(2, "hi") is False


def test_broadcast_survives_one_unreachable_chat(monkeypatch):
    """One blocked bot must not swallow everyone else's notification."""
    h = Harness()
    h.send_targets = [("TESTTOKEN", "chatDEAD"), ("TESTTOKEN", "chatOK")]
    _install(monkeypatch, h)

    def flaky(chat_id, text, token, reply_markup=None):
        if chat_id == "chatDEAD":
            raise real_client.TelegramError("blocked", status=403)
        h.sent.append(("text", chat_id, text, reply_markup))

    monkeypatch.setattr(service.client, "send_text", flaky)
    assert service.broadcast_telegram("Daily digest") is True
    assert [c for (_k, c, _b, _m) in h.sent] == ["chatOK"]


def test_empty_text_is_never_sent(monkeypatch):
    h = Harness()
    _install(monkeypatch, h)
    assert service.notify_user_telegram(2, "") is False
    assert service.broadcast_telegram("") is False
    assert h.sent == []


# ── Non-text messages, error/edge turns, stale-batch cleanup ────────────────

async def test_non_text_message_is_ignored(monkeypatch):
    h = Harness()
    _install(monkeypatch, h)
    # A photo/sticker has no "text" field.
    await service.handle_update({"message": {"chat": {"id": "chat1", "type": "private"},
                                             "from": {"id": "user1", "first_name": "Alex"}}})
    assert h.chat_calls == []   # no empty turn
    assert h.sent == []         # and no spurious reply


async def test_error_event_notifies_user(monkeypatch):
    h = Harness()
    _install(monkeypatch, h, chat_scripts=[[{"type": "error", "error": "provider exploded"}]])
    await service.handle_update(_msg("hi"))
    assert any("provider exploded" in t for t in _texts(h))


async def test_engine_crash_notifies_user(monkeypatch):
    h = Harness()
    _install(monkeypatch, h)

    async def boom(*a, **k):
        raise RuntimeError("kaboom")
        yield  # pragma: no cover — makes this an async generator

    monkeypatch.setattr(service.engine, "chat", boom)
    await service.handle_update(_msg("hi"))
    assert any("unexpected error" in t.lower() for t in _texts(h))


async def test_generator_without_done_flushes_buffer(monkeypatch):
    h = Harness()
    _install(monkeypatch, h, chat_scripts=[[{"type": "text", "text": "partial answer"}]])  # no 'done'
    await service.handle_update(_msg("hi"))
    assert ("html", "chat1", "partial answer", None) in h.sent


async def test_new_message_auto_denies_stale_batch(monkeypatch):
    h = Harness()
    h.link["pending_msg_id"] = "mOLD"
    h.pending_tool_uses = ["tuA", "tuB"]
    _install(monkeypatch, h, chat_scripts=[[{"type": "text", "text": "ok"}, {"type": "done"}]])
    await service.handle_update(_msg("never mind, do this instead"))
    assert ("conv1", "tuA", "deny", "mOLD") in h.resolve_calls
    assert ("conv1", "tuB", "deny", "mOLD") in h.resolve_calls
    # Cleared CONDITIONALLY — only the batch this turn actually denied, so a batch
    # installed in between keeps its live buttons.
    assert h.cleared == [(7, "mOLD")]
    # The auto-deny path carries the seat too, so a denial is attributable.
    assert h.resolve_users and all(u == MEMBER for u in h.resolve_users)
    assert h.chat_users and all(u == MEMBER for u in h.chat_users)


async def test_stale_batch_auto_denied_even_without_provider(monkeypatch):
    # The reorder fix: a new message cancels abandoned buttons BEFORE the provider gate,
    # so a disconnected provider can't leave a live Approve/Deny for an abandoned write.
    h = Harness()
    h.link["pending_msg_id"] = "mOLD"
    h.pending_tool_uses = ["tuA"]
    _install(monkeypatch, h, provider=None)
    await service.handle_update(_msg("never mind"))
    assert ("conv1", "tuA", "deny", "mOLD") in h.resolve_calls  # denied despite no provider
    assert h.chat_calls == []                                    # no turn ran
    assert any("No AI provider" in t for t in _texts(h))


async def test_bare_start_linked_greets(monkeypatch):
    h = Harness()
    _install(monkeypatch, h)
    await service.handle_update(_msg("/start"))
    assert any("linked" in t.lower() for t in _texts(h))
    assert h.chat_calls == []


async def test_bare_start_unlinked_shows_help(monkeypatch):
    h = Harness()
    h.linked = False
    _install(monkeypatch, h)
    await service.handle_update(_msg("/start", user_id="stranger"))
    assert any("don't recognize" in t for t in _texts(h))


# ── Pure helpers ────────────────────────────────────────────────────────────

def test_outcome_text_variants():
    assert service._outcome_text("approve", {"result": {"ok": True}}) == "✅ Done."
    assert service._outcome_text("deny", {"result": {"status": "denied_by_user"}}) == "❌ Denied."
    assert service._outcome_text("approve", {"status": "already_resolved", "result": None}) == "Already handled."
    assert service._outcome_text("approve", {"result": {"error": "nope"}}) == "⚠️ Action failed."


def test_format_args_truncates_long_values():
    out = service._format_args({"note": "x" * 400})
    assert "..." in out and len(out) < 400


def test_format_args_empty():
    assert service._format_args({}) == ""


def test_parse_sse():
    assert service._parse_sse('data: {"type": "done"}\n\n') == {"type": "done"}
    assert service._parse_sse("data: not json\n\n") is None
    assert service._parse_sse("event: ping\n\n") is None


# ── The `capture …` intercept and the todo mode that gates it (#70, #102) ─────

async def test_capture_intercept_files_a_todo_and_never_reaches_the_model(monkeypatch):
    """GTD mode's deterministic intercept runs BEFORE the model — zero AI cost, and it
    works with no provider configured at all."""
    h = Harness()
    _install(monkeypatch, h)
    monkeypatch.setattr(service, "_todo_mode", lambda: "gtd")
    monkeypatch.setattr(service.gtd_service, "capture",
                        lambda text, source, owner_id=None: h.captures.append((text, source, owner_id))
                        or {"id": 7, "title": text})

    await service.handle_update(_msg("capture buy more candles"))

    assert h.chat_calls == [], "the intercept must short-circuit before engine.chat"
    assert any("Captured: buy more candles" in t for t in _texts(h))


async def test_telegram_capture_is_stamped_with_the_linked_seat(monkeypatch):
    """Unlike the PUBLIC capture surface, a Telegram capture has a known person behind it.

    The `source` value is unchanged — "telegram", never "capture_web" — so the public
    path's own handling of capture-sourced text is untouched by this.
    """
    h = Harness()
    _install(monkeypatch, h)
    monkeypatch.setattr(service, "_todo_mode", lambda: "gtd")
    monkeypatch.setattr(service.gtd_service, "capture",
                        lambda text, source, owner_id=None: h.captures.append((text, source, owner_id))
                        or {"id": 7, "title": text})

    await service.handle_update(_msg("capture buy more candles"))

    assert h.captures == [("buy more candles", "telegram", 2)]


async def test_capture_is_ordinary_conversation_in_normal_todo_mode(monkeypatch):
    """In normal mode `capture …` is just words — it has to reach the assistant."""
    h = Harness()
    _install(monkeypatch, h, chat_scripts=[[{"type": "text", "text": "sure"}]])
    monkeypatch.setattr(service, "_todo_mode", lambda: "normal")

    def _never(*a, **k):
        raise AssertionError("capture must not run in normal mode")

    monkeypatch.setattr(service.gtd_service, "capture", _never)

    await service.handle_update(_msg("capture buy more candles"))

    assert len(h.chat_calls) == 1


async def test_capture_failure_answers_the_user_instead_of_crashing(monkeypatch):
    """#102 flipped the mode fail-safe to 'gtd', so an install whose database is
    unreadable now reaches this path where it previously fell through to the model.
    An honest error beats a silently swallowed message — and beats a traceback."""
    h = Harness()
    _install(monkeypatch, h)
    monkeypatch.setattr(service, "_todo_mode", lambda: "gtd")

    def _boom(text, source, owner_id=None):
        raise RuntimeError("Postgres pool not initialized")

    monkeypatch.setattr(service.gtd_service, "capture", _boom)

    await service.handle_update(_msg("capture buy more candles"))

    assert h.chat_calls == []
    assert any("Couldn't capture that" in t for t in _texts(h))


async def test_an_unlinked_sender_can_never_capture(monkeypatch):
    """The capture intercept sits behind the link gate — no owner, no capture."""
    h = Harness()
    h.linked = False
    _install(monkeypatch, h)
    monkeypatch.setattr(service, "_todo_mode", lambda: "gtd")

    def _never(*a, **k):
        raise AssertionError("an unlinked sender must not reach capture")

    monkeypatch.setattr(service.gtd_service, "capture", _never)
    await service.handle_update(_msg("capture something", user_id="stranger"))
    assert any("don't recognize" in t for t in _texts(h))
