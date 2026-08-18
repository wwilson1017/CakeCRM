"""Pending gmail_create_draft is bound to the connection it was proposed against
(issue #43).

A draft is proposed in one turn and approved later — minutes later, from the web UI
or a Telegram button. If the admin connects a DIFFERENT Google account in between,
the approved draft would land in that account. The engine stamps the connection
generation into the pending placeholder at the gate and re-checks it before
executing; these tests pin both halves plus every degradation path.

Hermetic: gmail.store's row read is stubbed, no DB and no Google.
"""

from __future__ import annotations

import json

import pytest

from assistant import engine, history
from gmail import tools as gmail_tools


@pytest.fixture
def at_generation(monkeypatch):
    """Pin the live connection generation the store reports."""

    def _set(value):
        monkeypatch.setattr(gmail_tools.store, "get_row", lambda: {"connection_generation": value})

    return _set


class _Registry:
    """Minimal ToolRegistry stand-in: records whether the executor actually ran."""

    def __init__(self, result=None):
        self.executed = []
        self._result = result if result is not None else {"ok": True, "draft_id": "d1"}

    def is_write(self, tool):
        return tool == "gmail_create_draft"

    def execute_tool_sync(self, tool, args):
        self.executed.append((tool, args))
        return self._result


def _claimed(content):
    return {"msg_id": "m1", "tool": "gmail_create_draft", "args": {"to": "a@b.c"}, "content": content}


def _placeholder(generation):
    return json.dumps({"status": history.PENDING_STATUS, "gmail_generation": generation})


# ── gmail.tools helpers ───────────────────────────────────────────────────────

def test_pending_binding_reads_the_live_generation(at_generation):
    at_generation(5)
    assert gmail_tools.pending_binding() == {"gmail_generation": 5}


def test_pending_binding_degrades_to_empty_when_the_store_is_unreachable(monkeypatch):
    """An unbindable proposal must still be proposable — never block the write.

    The REAL failure mode: store.get_row() swallows read errors and returns {}, it
    does not raise. Reading it as `... or 0` would mint a bogus generation 0 here
    and then falsely refuse the draft at approve time.
    """
    monkeypatch.setattr(gmail_tools.store, "get_row", lambda: {})
    assert gmail_tools.pending_binding() == {}


def test_pending_binding_degrades_when_get_row_raises(monkeypatch):
    """Defence in depth — get_row is documented never to raise, but don't rely on it."""
    monkeypatch.setattr(gmail_tools.store, "get_row",
                        lambda: (_ for _ in ()).throw(RuntimeError("pool down")))
    assert gmail_tools.pending_binding() == {}


def test_binding_conflict_none_when_the_generation_matches(at_generation):
    at_generation(5)
    assert gmail_tools.binding_conflict({"gmail_generation": 5}) is None


def test_binding_conflict_reports_a_changed_connection(at_generation):
    at_generation(6)
    conflict = gmail_tools.binding_conflict({"gmail_generation": 5})
    assert conflict is not None and "error" in conflict
    assert "changed" in conflict["error"]


@pytest.mark.parametrize("placeholder", [
    None,                      # nothing bound
    {},                        # proposed before #43 shipped
    {"status": "x"},           # a placeholder with no binding key
    {"gmail_generation": "not-an-int"},  # hand-edited / corrupt
])
def test_binding_conflict_never_raises_and_defaults_to_executing(at_generation, placeholder):
    """This runs AFTER claim_pending_tool already flipped the result to executing —
    raising here would strand the confirmation forever."""
    at_generation(5)
    assert gmail_tools.binding_conflict(placeholder) is None


@pytest.mark.parametrize("get_row", [
    lambda: {},                                                  # the real failure mode
    lambda: (_ for _ in ()).throw(RuntimeError("pool down")),    # defence in depth
])
def test_binding_conflict_tolerates_an_unreachable_store(monkeypatch, get_row):
    """An unreadable generation must NOT read as a conflict. Reporting one would
    tell the user their Gmail account changed when nothing had, and burn the
    confirmation — claim_pending_tool has already marked it executing."""
    monkeypatch.setattr(gmail_tools.store, "get_row", get_row)
    assert gmail_tools.binding_conflict({"gmail_generation": 5}) is None


# ── The placeholder written at the confirmation gate ──────────────────────────

async def test_placeholder_carries_the_binding_for_a_gmail_draft(at_generation):
    at_generation(9)
    content = await engine._pending_placeholder("gmail_create_draft")
    assert json.loads(content) == {"status": history.PENDING_STATUS, "gmail_generation": 9}


async def test_bound_placeholder_is_still_recognised_as_pending(at_generation):
    """The extra key must stay inert to the pending/executing state machine —
    history's status helpers read only "status"."""
    at_generation(9)
    content = await engine._pending_placeholder("gmail_create_draft")
    assert history.is_pending_result(content) is True


async def test_placeholder_is_unchanged_for_non_gmail_writes(at_generation):
    at_generation(9)
    assert await engine._pending_placeholder("crm_create_contact") == history.PENDING_RESULT_JSON


async def test_placeholder_falls_back_when_the_binding_cannot_be_read(monkeypatch):
    monkeypatch.setattr(gmail_tools.store, "get_row", lambda: {})
    assert await engine._pending_placeholder("gmail_create_draft") == history.PENDING_RESULT_JSON


# ── resolve_confirmation: the actual refusal ──────────────────────────────────

def test_approve_after_account_swap_does_not_create_the_draft(monkeypatch, at_generation):
    """THE #43 race. Proposed at generation 3; a different Google account is
    connected before the human clicks Approve. The draft must NOT be created."""
    at_generation(4)
    registry = _Registry()
    merged = []
    monkeypatch.setattr(history, "claim_pending_tool", lambda *a, **k: _claimed(_placeholder(3)))
    monkeypatch.setattr(history, "merge_tool_result", lambda *a: merged.append(a))

    out = engine.resolve_confirmation(registry, "c1", "t1", "approve", msg_id="m1")

    assert registry.executed == []  # nothing was sent to Gmail
    assert "error" in out["result"] and "changed" in out["result"]["error"]
    assert merged  # the refusal is recorded, so the turn isn't left pending


def test_approve_on_the_same_connection_creates_the_draft(monkeypatch, at_generation):
    at_generation(3)
    registry = _Registry()
    monkeypatch.setattr(history, "claim_pending_tool", lambda *a, **k: _claimed(_placeholder(3)))
    monkeypatch.setattr(history, "merge_tool_result", lambda *a: None)

    out = engine.resolve_confirmation(registry, "c1", "t1", "approve", msg_id="m1")

    assert registry.executed == [("gmail_create_draft", {"to": "a@b.c"})]
    assert out["result"] == {"ok": True, "draft_id": "d1"}


def test_approve_of_a_pre_43_pending_draft_still_executes(monkeypatch, at_generation):
    """Drafts proposed before this shipped carry no binding. A deployment-window
    edge for a single admin — keep today's behaviour rather than refusing."""
    at_generation(3)
    registry = _Registry()
    monkeypatch.setattr(history, "claim_pending_tool",
                        lambda *a, **k: _claimed(history.PENDING_RESULT_JSON))
    monkeypatch.setattr(history, "merge_tool_result", lambda *a: None)

    engine.resolve_confirmation(registry, "c1", "t1", "approve", msg_id="m1")
    assert registry.executed == [("gmail_create_draft", {"to": "a@b.c"})]


def test_unparseable_placeholder_does_not_strand_the_confirmation(monkeypatch, at_generation):
    at_generation(3)
    registry = _Registry()
    monkeypatch.setattr(history, "claim_pending_tool", lambda *a, **k: _claimed("not json"))
    monkeypatch.setattr(history, "merge_tool_result", lambda *a: None)

    engine.resolve_confirmation(registry, "c1", "t1", "approve", msg_id="m1")
    assert registry.executed == [("gmail_create_draft", {"to": "a@b.c"})]


def test_deny_is_unaffected_by_the_binding(monkeypatch, at_generation):
    at_generation(4)
    registry = _Registry()
    monkeypatch.setattr(history, "claim_pending_tool", lambda *a, **k: _claimed(_placeholder(3)))
    monkeypatch.setattr(history, "merge_tool_result", lambda *a: None)

    out = engine.resolve_confirmation(registry, "c1", "t1", "deny", msg_id="m1")
    assert out["result"] == {"status": history.DENIED_STATUS}
    assert registry.executed == []


def test_non_gmail_writes_skip_the_binding_check_entirely(monkeypatch):
    """CRM writes have no connection to bind to — the check must not touch them,
    and must not need gmail to be reachable at all."""
    monkeypatch.setattr(gmail_tools.store, "get_row",
                        lambda: (_ for _ in ()).throw(AssertionError("must not be consulted")))
    assert engine._binding_conflict("crm_create_contact", _placeholder(3)) is None
