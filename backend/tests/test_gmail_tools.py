"""Hermetic tests for gmail.tools — executor error discipline (never raise) and
get_gmail_tools() connection gating."""

from __future__ import annotations

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


# ── get_gmail_tools() gating ──────────────────────────────────────────────────

def test_get_gmail_tools_connected(monkeypatch):
    monkeypatch.setattr(tools.store, "is_connected", lambda: True)
    defs, execs = tools.get_gmail_tools()
    assert {d["name"] for d in defs} == {"gmail_search", "gmail_read_thread", "gmail_create_draft"}
    assert set(execs) == {"gmail_search", "gmail_read_thread", "gmail_create_draft"}


def test_get_gmail_tools_disconnected_returns_empty(monkeypatch):
    monkeypatch.setattr(tools.store, "is_connected", lambda: False)
    defs, execs = tools.get_gmail_tools()
    assert defs == []
    assert execs == {}


def test_get_gmail_tools_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("pool not initialized")

    monkeypatch.setattr(tools.store, "is_connected", boom)
    defs, execs = tools.get_gmail_tools()
    assert defs == []
    assert execs == {}
