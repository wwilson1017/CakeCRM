"""Hermetic tests for gmail.tools — executor error discipline (never raise) and
get_gmail_tools() connection + seat gating (#8, seat gate #194)."""

from __future__ import annotations

from conftest import fake_admin, fake_member

from gmail import tools
from gmail.client import GmailAuthError


def test_gmail_search_wraps_results_and_clamps(monkeypatch):
    seen = {}

    def fake_call(op, **kwargs):
        seen.update(kwargs)
        return [{"id": "m1", "thread_id": "t1"}]

    monkeypatch.setattr(tools.client, "call_gmail", fake_call)
    out = tools.gmail_search(query="hi", max_results=999)
    assert out == {"messages": [{"id": "m1", "thread_id": "t1"}], "count": 1}
    assert seen["max_results"] == tools._MAX_SEARCH_RESULTS  # clamped to 25
    assert seen["query"] == "hi"


def test_gmail_search_clamps_low_and_bad(monkeypatch):
    seen = {}
    monkeypatch.setattr(tools.client, "call_gmail", lambda op, **kw: seen.update(kw) or [])
    tools.gmail_search(query="x", max_results=0)
    assert seen["max_results"] == 1
    tools.gmail_search(query="x", max_results="not-int")
    assert seen["max_results"] == 10  # falls back to default


def test_gmail_search_auth_error_returns_needs_reconnect(monkeypatch):
    def boom(op, **kw):
        raise GmailAuthError("Gmail is not connected. Connect it in Settings.")

    monkeypatch.setattr(tools.client, "call_gmail", boom)
    out = tools.gmail_search(query="x")
    assert out["needs_reconnect"] is True
    assert "not connected" in out["error"]


def test_gmail_search_generic_error_is_dict_not_raise(monkeypatch):
    def boom(op, **kw):
        raise RuntimeError("HttpError 500 raw detail")

    monkeypatch.setattr(tools.client, "call_gmail", boom)
    out = tools.gmail_search(query="x")
    assert "error" in out
    # Raw exception text must NOT leak into the model-facing error.
    assert "HttpError 500 raw detail" not in out["error"]


def test_gmail_read_thread_degrades(monkeypatch):
    monkeypatch.setattr(tools.client, "call_gmail", lambda op, **kw: {"thread_id": "t1", "messages": []})
    assert tools.gmail_read_thread(thread_id="t1")["thread_id"] == "t1"


def test_gmail_create_draft_adds_review_note(monkeypatch):
    monkeypatch.setattr(
        tools.client, "call_gmail",
        lambda op, **kw: {"ok": True, "draft_id": "d1", "message_id": "m1"},
    )
    out = tools.gmail_create_draft(to="a@x.com", subject="s", body="b")
    assert out["draft_id"] == "d1"
    assert "review and send" in out["note"].lower()


def test_gmail_create_draft_auth_error(monkeypatch):
    monkeypatch.setattr(tools.client, "call_gmail", lambda op, **kw: (_ for _ in ()).throw(GmailAuthError("expired")))
    out = tools.gmail_create_draft(to="a@x.com", subject="s", body="b")
    assert out["needs_reconnect"] is True


# ── get_gmail_tools() gating: connection AND seat (issue #194) ────────────────

_ALL_THREE = {"gmail_search", "gmail_read_thread", "gmail_create_draft"}

# The shared seat fixtures, not hand-rolled copies: conftest keeps ONE definition so the
# shape can only drift in one place, and every other Gmail test file imports it.
ADMIN = fake_admin()
MEMBER = fake_member()


def _connection(monkeypatch, *, connected=True, shared=False):
    monkeypatch.setattr(tools.store, "is_connected", lambda: connected)
    monkeypatch.setattr(tools.store, "sharing_enabled", lambda: shared)


def test_get_gmail_tools_admin_seat_connected(monkeypatch):
    _connection(monkeypatch)
    defs, execs = tools.get_gmail_tools(user=ADMIN)
    assert {d["name"] for d in defs} == _ALL_THREE
    assert set(execs) == _ALL_THREE


def test_get_gmail_tools_disconnected_returns_empty(monkeypatch):
    """Unchanged since #8: a disconnected install hides the tools from everyone,
    including an admin."""
    _connection(monkeypatch, connected=False)
    assert tools.get_gmail_tools(user=ADMIN) == ([], {})


def test_get_gmail_tools_member_seat_is_denied_by_default(monkeypatch):
    """THE issue: one mailbox per install, so advertising it to every seat is how a
    member came to be able to read the admin's mail."""
    _connection(monkeypatch, shared=False)
    assert tools.get_gmail_tools(user=MEMBER) == ([], {})


def test_get_gmail_tools_member_seat_allowed_when_install_shares(monkeypatch):
    """Will's widening: an install deliberately running one shared inbox opts in."""
    _connection(monkeypatch, shared=True)
    defs, execs = tools.get_gmail_tools(user=MEMBER)
    assert {d["name"] for d in defs} == _ALL_THREE
    assert set(execs) == _ALL_THREE


def test_a_seat_with_no_role_is_treated_as_a_member(monkeypatch):
    """Fail closed on a malformed/legacy user dict — absence is not admin."""
    _connection(monkeypatch, shared=False)
    assert tools.get_gmail_tools(user={"id": 3}) == ([], {})


def test_unattended_turn_is_denied_even_when_the_install_shares(monkeypatch):
    """The background narrowing is UNCONDITIONAL. 'Share with all seats' widens the gate
    to seats, and a turn with no user is not a seat — so turning sharing on can never
    hand the mailbox back to the heartbeat or the proactive digest."""
    _connection(monkeypatch, shared=True)
    assert tools.get_gmail_tools(user=None) == ([], {})
    assert tools.get_gmail_tools() == ([], {})


def test_unattended_denial_never_reads_the_database(monkeypatch):
    """Ordering pin, asserted on CALLS not results: `user is None` is tested first, so an
    unattended turn is denied even when the connection row is unreadable. A results-only
    test would still pass if the checks were reordered."""
    def boom(*a, **k):
        raise AssertionError("get_gmail_tools(user=None) must not touch the DB")

    monkeypatch.setattr(tools.store, "is_connected", boom)
    monkeypatch.setattr(tools.store, "sharing_enabled", boom)
    assert tools.get_gmail_tools(user=None) == ([], {})


def test_admin_seat_never_reads_the_sharing_flag(monkeypatch):
    """An admin is allowed regardless, so the second read is paid only by a member."""
    def boom(*a, **k):
        raise AssertionError("an admin seat must not need the sharing flag")

    monkeypatch.setattr(tools.store, "is_connected", lambda: True)
    monkeypatch.setattr(tools.store, "sharing_enabled", boom)
    assert {d["name"] for d in tools.get_gmail_tools(user=ADMIN)[0]} == _ALL_THREE


def test_get_gmail_tools_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("pool not initialized")

    monkeypatch.setattr(tools.store, "is_connected", boom)
    assert tools.get_gmail_tools(user=ADMIN) == ([], {})


def test_a_failing_sharing_read_denies_the_member(monkeypatch):
    """Fail closed: an unreadable sharing flag is 'not shared', never 'shared'."""
    def boom(*a, **k):
        raise RuntimeError("pool not initialized")

    monkeypatch.setattr(tools.store, "is_connected", lambda: True)
    monkeypatch.setattr(tools.store, "sharing_enabled", boom)
    assert tools.get_gmail_tools(user=MEMBER) == ([], {})


def test_gmail_read_thread_generic_error_is_dict_not_raise(monkeypatch):
    def boom(op, **kw):
        raise RuntimeError("HttpError 500 raw detail")

    monkeypatch.setattr(tools.client, "call_gmail", boom)
    out = tools.gmail_read_thread(thread_id="t1")
    assert "error" in out
    assert "HttpError 500 raw detail" not in out["error"]  # raw text not leaked
