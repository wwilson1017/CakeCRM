"""Bare deal ids in outbound assistant text become "Title (url)" (issue #238).

Baker's tool results carry a ``url`` for every deal (#145), but its prose still says
"deal #14" — useless in a Telegram message or a push notification whose reader cannot
click an id. The fix is deterministic: a deal a tool returned during THIS turn is
rewritten from that tool's own title and url; every other number is left alone, so a
made-up id is never lent a real deal's title.

Pinned here: the pure rewrite (``crm.links``), the real ``ToolRegistry`` recording what
its tools returned, and the two outbound seams that read it — ``notify_user`` and the
Telegram reply (including the continuation after an approved write). Hermetic.
"""

import json
import types

import pytest
from test_telegram_service import Harness, _callback, _install, _msg

from assistant.registry import ToolRegistry
from crm.links import deal_url, link_deal_refs, remember_deal_refs
from notifications import tools as notification_tools
from telegram import service

SEARCH_RESULT = {"deals": [{"id": 14, "title": "Harbor Supply renewal", "url": deal_url(14)}],
                 "total": 1}


@pytest.fixture
def url():
    return deal_url(14)


@pytest.fixture(autouse=True)
def _no_paused_refs_between_tests():
    service._paused_deal_refs.clear()
    yield
    service._paused_deal_refs.clear()


def _refs(result=SEARCH_RESULT):
    refs: dict = {}
    remember_deal_refs(result, refs)
    return refs


# ── the rewrite ─────────────────────────────────────────────────────────


def test_a_deal_this_turn_read_is_rewritten_to_title_and_url(url):
    refs = _refs()
    for text in ("FYI on deal #14.", "FYI on deal 14.", "FYI on Deal#14.", "FYI on #14."):
        assert link_deal_refs(text, refs) == f"FYI on Harbor Supply renewal ({url})."


def test_a_second_mention_or_a_url_already_present_is_not_linked_twice(url):
    refs = _refs()
    assert (link_deal_refs("#14, then deal 14 again", refs)
            == f"Harbor Supply renewal ({url}), then Harbor Supply renewal again")
    assert link_deal_refs(f"deal #14: {url}", refs) == f"Harbor Supply renewal: {url}"


def test_another_deals_url_does_not_count_as_this_ones():
    """Deal 1's relative url is a prefix of deal 14's — a bare substring test would
    think deal 1 is already linked and drop its link."""
    refs = _refs({"deals": [
        {"id": 1, "title": "Alpha", "url": deal_url(1)},
        {"id": 14, "title": "Beta", "url": deal_url(14)},
    ]})
    out = link_deal_refs(f"See {deal_url(14)} and deal #1.", refs)
    assert out == f"See {deal_url(14)} and Alpha ({deal_url(1)})."


def test_an_id_this_turn_never_read_is_left_alone():
    refs = _refs()
    text = "Logged on deal #2941 and #2472 closed."
    assert link_deal_refs(text, refs) == text
    assert link_deal_refs("deal #14", {}) == "deal #14"
    assert link_deal_refs("deal #14", None) == "deal #14"


def test_a_non_deal_number_is_left_alone_even_when_it_matches_a_deal_id(url):
    refs = _refs()
    for text in ("PO#14 is open", "PO #14 is open", "issue #14 shipped", "todo #14 is done",
                 "Invoice #14 is paid", f"see {url}", "invoice 14", "14 units",
                 "/crm/pipeline?deal=14"):
        assert link_deal_refs(text, refs) == text


@pytest.mark.parametrize("label", [
    "PO", "purchase order", "sales order", "order", "invoice", "bill", "issue", "PR",
    "ticket", "todo", "contact", "company", "item", "case", "check", "step",
])
def test_every_other_label_keeps_its_bare_number(label):
    text = f"See {label} #14 today."
    assert link_deal_refs(text, _refs()) == text


def test_so_is_read_as_the_english_word_not_a_label(url):
    assert link_deal_refs("so #14 needs a call", _refs()) == f"so Harbor Supply renewal ({url}) needs a call"


def test_the_link_is_relative_without_a_configured_address_and_absolute_with_one(monkeypatch):
    from core.config import settings

    monkeypatch.setattr(settings, "frontend_url_is_default", True)
    refs = _refs({"id": 14, "title": "T", "url": deal_url(14)})
    assert link_deal_refs("deal 14", refs) == "T (/crm/pipeline?deal=14)"

    monkeypatch.setattr(settings, "frontend_url", "https://crm.example.test/")
    monkeypatch.setattr(settings, "frontend_url_is_default", False)
    refs = _refs({"id": 14, "title": "T", "url": deal_url(14)})
    assert link_deal_refs("deal 14", refs) == "T (https://crm.example.test/crm/pipeline?deal=14)"


def test_only_real_deal_records_are_remembered():
    # A contact row has an id and a job title but no deal url; a todo has a title but no
    # url; an error dict has neither. None may become a rewrite target.
    refs = _refs({"contacts": [{"id": 14, "title": "Buyer", "name": "Wendy"}],
                  "todos": [{"id": 14, "title": "Call back"}],
                  "deal": {"error": "Deal 15 not found"},
                  "forged": {"id": 16, "title": "X", "url": deal_url(17)}})
    assert refs == {}
    # `crm_get_deal_health` keys its record `deal_id`; nested lists are walked.
    refs = _refs({"deal_id": 7, "title": "Lakeside", "url": deal_url(7),
                  "contact": {"deals": [{"id": 9, "title": "Ridge", "url": deal_url(9)}]}})
    assert set(refs) == {7, 9}


# ── the registry records every tool's deals ─────────────────────────────


def test_the_registry_records_deals_a_tool_returned():
    reg = ToolRegistry()
    assert reg.deal_refs == {}
    reg.executors["crm_search_deals"] = lambda **k: SEARCH_RESULT
    assert reg.execute_tool_sync("crm_search_deals", {}) == SEARCH_RESULT
    assert reg.deal_refs == {14: ("Harbor Supply renewal", deal_url(14))}


def test_a_bookkeeping_failure_never_costs_a_tool_its_result(monkeypatch):
    import assistant.registry as registry_mod

    def boom(*a, **k):
        raise RuntimeError("nope")

    monkeypatch.setattr(registry_mod, "remember_deal_refs", boom)
    reg = ToolRegistry()
    reg.executors["crm_update_deal"] = lambda **k: {"ok": True}
    assert reg.execute_tool_sync("crm_update_deal", {}) == {"ok": True}


# ── notify_user ─────────────────────────────────────────────────────────


def test_notify_user_rewrites_ids_this_run_read(monkeypatch, url):
    sent = []
    monkeypatch.setattr(notification_tools.delivery, "deliver_notification",
                        lambda t, m: sent.append((t, m)) or {"channels_sent": ["push"]})
    reg = types.SimpleNamespace(_notify_user_called=False, deal_refs=_refs())
    _, execs = notification_tools.get_notification_tools(reg)
    execs["notify_user"](title="Deal #14 is stale", message="Nobody touched deal 14; #99 too.")
    # Title and body are rewritten independently: each is read on its own (a push
    # banner may show only one), so each carries its own link.
    assert sent == [(f"Harbor Supply renewal ({url}) is stale",
                     f"Nobody touched Harbor Supply renewal ({url}); #99 too.")]


def test_notify_user_is_untouched_when_the_run_read_no_deal(monkeypatch):
    sent = []
    monkeypatch.setattr(notification_tools.delivery, "deliver_notification",
                        lambda t, m: sent.append((t, m)) or {"channels_sent": []})
    reg = types.SimpleNamespace(_notify_user_called=False, deal_refs={})
    _, execs = notification_tools.get_notification_tools(reg)
    execs["notify_user"](title="Heads up", message="deal #14 is stale")
    assert sent == [("Heads up", "deal #14 is stale")]


# ── Telegram ────────────────────────────────────────────────────────────


def _telegram(monkeypatch, scripts, *, consume=None):
    """The shared Telegram harness, with a fake chat that 'runs a tool' by recording the
    deals a script step names into the registry it was handed — what the real
    ``ToolRegistry.execute_tool_sync`` does."""
    h = Harness()
    _install(monkeypatch, h, consume=consume)
    registries = []

    def fake_registry(*, user=None, background=False):
        reg = types.SimpleNamespace(deal_refs={})
        registries.append(reg)
        return reg

    idx = {"i": 0}

    async def fake_chat(provider, registry, messages, tool_mode="normal", conversation_id=None,
                        title_hint=None, context=None, *, user):
        script = scripts[idx["i"]] if idx["i"] < len(scripts) else []
        idx["i"] += 1
        for step in script:
            if "tool_result" in step:
                remember_deal_refs(step["tool_result"], registry.deal_refs)
            else:
                yield f"data: {json.dumps(step)}\n\n"

    # Each approved write returns its own deal: tu1 → deal 10, anything else → deal 14.
    def fake_resolve(registry, conv, tuid, decision, msg_id=None, *, user):
        if decision == "approve":
            deal = ({"id": 10, "title": "Lakeside expansion", "url": deal_url(10)}
                    if tuid == "tu1" else
                    {"id": 14, "title": "Harbor Supply renewal", "url": deal_url(14)})
            remember_deal_refs(deal, registry.deal_refs)
        return {"tool": "crm_update_deal", "decision": decision, "result": {"ok": True}}

    monkeypatch.setattr(service, "ToolRegistry", fake_registry)
    monkeypatch.setattr(service, "engine",
                        types.SimpleNamespace(chat=fake_chat, resolve_confirmation=fake_resolve))
    return h, registries


async def test_telegram_reply_rewrites_a_deal_the_turn_read(monkeypatch, url):
    h, _ = _telegram(monkeypatch, [[
        {"tool_result": SEARCH_RESULT},
        {"type": "text", "text": "Deal #14 is in proposal; #99 is not mine to name."},
        {"type": "done"},
    ]])
    await service.handle_update(_msg("where is it?"))
    assert [b for (k, _c, b, _m) in h.sent if k == "html"] == [
        f"Harbor Supply renewal ({url}) is in proposal; #99 is not mine to name."
    ]


async def test_telegram_reply_is_untouched_without_a_deal_read(monkeypatch):
    h, _ = _telegram(monkeypatch, [[{"type": "text", "text": "Deal #14 is fine."},
                                    {"type": "done"}]])
    await service.handle_update(_msg("hi"))
    assert [b for (k, _c, b, _m) in h.sent if k == "html"] == ["Deal #14 is fine."]


def _html(h):
    return [b for (k, _c, b, _m) in h.sent if k == "html"]


async def test_the_continuation_links_every_deal_a_multi_write_batch_returned(monkeypatch, url):
    """Each button press runs on its own registry; the continuation is a third. Only the
    batch-completing press's deals reaching the reply would leave the first bare."""
    h, registries = _telegram(
        monkeypatch,
        [[{"type": "text", "text": "Updated deal #10 and deal #14."}, {"type": "done"}]],
        consume=[False, True],
    )
    h.link["pending_msg_id"] = "abcd1234"
    await service.handle_update(_callback("a:abcd1234:tu1"))
    await service.handle_update(_callback("a:abcd1234:tu2"))
    assert len(registries) == 3
    assert _html(h) == [
        f"Updated Lakeside expansion ({deal_url(10)}) and Harbor Supply renewal ({url})."
    ]
    assert service._paused_deal_refs == {}  # consumed by the continuation


async def test_narration_before_a_confirm_card_is_linked_and_carried_forward(monkeypatch, url):
    """The turn reads deal 14, narrates the write it is about to make and pauses; after the
    approval (which returns deal 10) the continuation still links the deal read before."""
    h, _ = _telegram(monkeypatch, [
        [{"tool_result": SEARCH_RESULT},
         {"type": "text", "text": "I'll close deal #14 — approve?"},
         {"type": "confirm", "tool": "crm_mark_deal_won", "args": {}, "tool_use_id": "tu1",
          "msg_id": "abcd1234", "description": "Mark won"}],
        [{"type": "text", "text": "Closed deal #14; see deal #10 next."}, {"type": "done"}],
    ], consume=[True])
    await service.handle_update(_msg("close it"))
    await service.handle_update(_callback("a:abcd1234:tu1"))
    assert _html(h) == [
        f"I'll close Harbor Supply renewal ({url}) — approve?",
        f"Closed Harbor Supply renewal ({url}); see Lakeside expansion ({deal_url(10)}) next.",
    ]


async def test_an_error_mid_turn_still_links_what_was_said(monkeypatch, url):
    h, _ = _telegram(monkeypatch, [[
        {"tool_result": SEARCH_RESULT},
        {"type": "text", "text": "Deal #14 first."},
        {"type": "error", "error": "provider down"},
    ]])
    await service.handle_update(_msg("go"))
    assert _html(h) == [f"Harbor Supply renewal ({url}) first."]


async def test_a_turn_that_ends_without_done_still_links(monkeypatch, url):
    h, _ = _telegram(monkeypatch, [[{"tool_result": SEARCH_RESULT},
                                    {"type": "text", "text": "Deal #14."}]])
    await service.handle_update(_msg("go"))
    assert _html(h) == [f"Harbor Supply renewal ({url})."]


async def test_a_new_message_does_not_inherit_an_abandoned_pause(monkeypatch):
    h, _ = _telegram(monkeypatch, [[{"type": "text", "text": "Deal #14?"}, {"type": "done"}]])
    service._paused_deal_refs[h.link["id"]] = _refs()
    await service.handle_update(_msg("something else"))
    assert _html(h) == ["Deal #14?"]
    assert service._paused_deal_refs == {}
