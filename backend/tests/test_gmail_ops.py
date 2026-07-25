"""Hermetic tests for gmail.ops — MIME construction, body/attachment parsing,
size caps, and proof that draft creation uses ONLY users().drafts().create()."""

from __future__ import annotations

import base64
from email import message_from_bytes
from email.policy import default as default_policy

import pytest

from gmail import ops


def _parse(raw: str):
    return message_from_bytes(base64.urlsafe_b64decode(raw), policy=default_policy)


# ── Fakes ─────────────────────────────────────────────────────────────────────

class _Exec:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


class _Messages:
    def __init__(self, listing, gets):
        self._listing = listing
        self._gets = gets

    def list(self, userId, q, maxResults):
        return _Exec(self._listing)

    def get(self, userId, id, format=None, metadataHeaders=None):
        return _Exec(self._gets[id])


class _Threads:
    def __init__(self, thread):
        self._thread = thread

    def get(self, userId, id, format):
        return _Exec(self._thread)


class _DraftsOnly:
    """Exposes ONLY create — accessing .send raises AttributeError, proving the
    draft path can't reach a send call."""

    def __init__(self, captured):
        self._captured = captured

    def create(self, userId, body):
        self._captured["body"] = body
        return _Exec({"id": "draft-1", "message": {"id": "msg-1"}})


class _Users:
    def __init__(self, messages=None, threads=None, drafts=None):
        self._messages = messages
        self._threads = threads
        self._drafts = drafts

    def messages(self):
        return self._messages

    def threads(self):
        return self._threads

    def drafts(self):
        return self._drafts


class _Service:
    def __init__(self, users):
        self._users = users

    def users(self):
        return self._users


# ── MIME ──────────────────────────────────────────────────────────────────────

def test_build_mime_roundtrips_headers_and_body():
    raw = ops._build_mime("to@x.com", "Hello", "Line one\nLine two", cc="cc@x.com", bcc="bcc@x.com")
    msg = _parse(raw)
    assert msg["To"] == "to@x.com"
    assert msg["Subject"] == "Hello"
    assert msg["Cc"] == "cc@x.com"
    assert msg["Bcc"] == "bcc@x.com"
    assert "Line one" in msg.get_content()
    assert "Line two" in msg.get_content()


def test_build_mime_omits_empty_cc_bcc():
    raw = ops._build_mime("to@x.com", "Subj", "Body")
    msg = _parse(raw)
    assert msg["Cc"] is None
    assert msg["Bcc"] is None


# ── Body parsing ──────────────────────────────────────────────────────────────

def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode()


def test_get_body_text_plain():
    payload = {"mimeType": "text/plain", "body": {"data": _b64("plain body")}}
    assert ops._get_body_text(payload) == "plain body"


def test_get_body_text_prefers_plain_in_multipart():
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": _b64("the plain part")}},
            {"mimeType": "text/html", "body": {"data": _b64("<p>html</p>")}},
        ],
    }
    assert ops._get_body_text(payload) == "the plain part"


def test_get_body_text_html_fallback_to_text():
    payload = {"mimeType": "text/html", "body": {"data": _b64("<p>Hi</p><br>there")}}
    out = ops._get_body_text(payload)
    assert "Hi" in out and "there" in out and "<p>" not in out


def test_truncate_body_caps_length():
    long = "x" * (ops._MAX_BODY_CHARS + 500)
    out = ops._truncate_body(long)
    assert out.endswith("[... truncated ...]")
    assert len(out) < len(long)


# ── Read ops ──────────────────────────────────────────────────────────────────

def test_list_messages_op_returns_summaries_with_thread_id():
    listing = {"messages": [{"id": "m1"}]}
    gets = {"m1": {
        "id": "m1", "threadId": "t1", "snippet": "hi", "labelIds": ["UNREAD"],
        "payload": {"headers": [
            {"name": "From", "value": "a@x.com"},
            {"name": "Subject", "value": "Re: hi"},
        ]},
    }}
    svc = _Service(_Users(messages=_Messages(listing, gets)))
    out = ops.list_messages_op(svc, query="is:unread", max_results=5)
    assert out == [{
        "id": "m1", "thread_id": "t1", "from": "a@x.com", "to": "",
        "subject": "Re: hi", "date": "", "snippet": "hi", "is_unread": True,
    }]


def test_get_thread_op_caps_and_flags_truncation():
    many = [
        {"id": f"m{i}", "threadId": "t1", "labelIds": [],
         "payload": {"headers": [{"name": "Subject", "value": f"s{i}"}]}}
        for i in range(ops._MAX_THREAD_MESSAGES + 5)
    ]
    svc = _Service(_Users(threads=_Threads({"messages": many})))
    out = ops.get_thread_op(svc, "t1")
    assert out["message_count"] == ops._MAX_THREAD_MESSAGES + 5
    assert len(out["messages"]) == ops._MAX_THREAD_MESSAGES
    assert out["truncated"] is True


# ── Draft op — proves the ONLY write chain is drafts().create ─────────────────

def test_create_draft_uses_only_drafts_create():
    captured = {}
    drafts = _DraftsOnly(captured)
    svc = _Service(_Users(drafts=drafts))
    result = ops.create_draft_op(svc, "to@x.com", "Subject", "Body text")
    assert result == {"ok": True, "draft_id": "draft-1", "message_id": "msg-1"}
    # The draft body is a base64url MIME message.
    raw = captured["body"]["message"]["raw"]
    msg = message_from_bytes(base64.urlsafe_b64decode(raw))
    assert msg["To"] == "to@x.com"
    # There is NO send on the drafts resource.
    with pytest.raises(AttributeError):
        getattr(drafts, "send")


def test_profile_op_returns_email():
    class _ProfileUsers:
        def getProfile(self, userId):
            return _Exec({"emailAddress": "me@example.com"})

    class _Svc:
        def users(self):
            return _ProfileUsers()

    assert ops.get_profile_op(_Svc()) == {"email": "me@example.com"}


# ── Attachment metadata + depth caps ──────────────────────────────────────────

def test_get_attachments_recursive():
    payload = {"parts": [
        {"filename": "a.pdf", "mimeType": "application/pdf", "body": {"size": 100}},
        {"mimeType": "multipart/mixed", "parts": [
            {"filename": "b.png", "mimeType": "image/png", "body": {"size": 50}},
        ]},
    ]}
    out = ops._get_attachments(payload)
    assert {a["filename"] for a in out} == {"a.pdf", "b.png"}
    by_name = {a["filename"]: a for a in out}
    assert by_name["a.pdf"]["mime_type"] == "application/pdf" and by_name["a.pdf"]["size"] == 100


def test_get_thread_op_short_thread_no_truncation():
    msgs = [{"id": "m1", "threadId": "t1", "labelIds": [],
             "payload": {"headers": [{"name": "Subject", "value": "s"}]}}]
    svc = _Service(_Users(threads=_Threads({"messages": msgs})))
    out = ops.get_thread_op(svc, "t1")
    assert out["message_count"] == 1
    assert len(out["messages"]) == 1
    assert "truncated" not in out


def test_get_body_text_depth_capped_no_recursion_error():
    payload = {"mimeType": "text/plain", "body": {"data": _b64("deep")}}
    for _ in range(ops._MAX_MIME_DEPTH + 5):
        payload = {"mimeType": "multipart/mixed", "parts": [payload]}
    assert ops._get_body_text(payload) == ""  # bailed at the cap, not RecursionError


def test_get_attachments_depth_capped_no_recursion_error():
    payload = {"parts": [{"filename": "leaf.txt", "mimeType": "text/plain", "body": {"size": 1}}]}
    for _ in range(ops._MAX_MIME_DEPTH + 5):
        payload = {"parts": [payload]}
    assert ops._get_attachments(payload) == []  # bailed at the cap, not RecursionError
