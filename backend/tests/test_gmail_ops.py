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


# ── Large text bodies stored under attachmentId (issue #43) ───────────────────

class _Attachments:
    """users().messages().attachments() — records every fetch."""

    def __init__(self, blobs, fail=False):
        self._blobs = blobs
        self._fail = fail
        self.calls = []

    def get(self, userId, messageId, id):
        self.calls.append((messageId, id))
        if self._fail:
            raise RuntimeError("attachment fetch exploded")
        return _Exec({"data": self._blobs[id]})


class _MessagesWithAttachments:
    def __init__(self, attachments):
        self._attachments = attachments

    def attachments(self):
        return self._attachments


def _stored_part(mime_type, attachment_id, size, filename=""):
    """A text part whose content Gmail moved out of body.data."""
    return {
        "mimeType": mime_type,
        "filename": filename,
        "body": {"data": "", "attachmentId": attachment_id, "size": size},
    }


def test_stored_plain_body_is_recovered_via_fetcher():
    part = _stored_part("text/plain", "att-1", 12)
    assert ops._part_text(part, fetch=lambda aid: "recovered body") == "recovered body"


def test_stored_html_body_is_flattened_to_text():
    """Fetched HTML must go through the same flattening as inline HTML —
    gmail_read_thread's contract is plain text."""
    part = _stored_part("text/html", "att-1", 40)
    out = ops._part_text(part, fetch=lambda aid: "<p>Hi <b>there</b></p>")
    assert "<p>" not in out and "<b>" not in out
    assert "Hi there" in out


def test_stored_body_over_cap_shows_marker_and_never_fetches():
    """Oversize bodies must be distinguishable from an empty email, and must not
    be downloaded at all — the cap is checked BEFORE the fetch."""
    fetched = []
    part = _stored_part("text/plain", "att-1", ops._MAX_BODY_FETCH_BYTES + 1)
    out = ops._part_text(part, fetch=lambda aid: fetched.append(aid) or "nope")
    assert out == ops._BODY_TOO_LARGE
    assert fetched == []


def test_stored_body_without_a_fetcher_is_blank_as_before():
    """`fetch=None` reproduces the pre-#43 behaviour byte for byte, which is what
    keeps list_messages_op and every other caller unaffected."""
    part = _stored_part("text/plain", "att-1", 10)
    assert ops._part_text(part, fetch=None) == ""
    assert ops._part_text(part, fetch=lambda aid: "") == ""


def test_body_fetcher_swallows_api_errors_and_enforces_its_budget():
    """The fetcher is where 'never raises' and the thread-wide bound actually live."""
    failing = _Attachments({}, fail=True)
    svc = _Service(_Users(messages=_MessagesWithAttachments(failing)))
    assert ops._make_body_fetcher(svc)("m1", "att-1") == ""

    ok = _Attachments({f"att-{i}": _b64("x") for i in range(8)})
    fetch = ops._make_body_fetcher(_Service(_Users(messages=_MessagesWithAttachments(ok))))
    for i in range(8):
        fetch("m1", f"att-{i}")
    assert len(ok.calls) == ops._MAX_BODY_FETCHES_PER_THREAD


def test_body_fetcher_rejects_a_blob_that_under_declared_its_size():
    """Backstop for a part whose body.size lied: the pre-fetch cap check can't
    catch it, so the fetcher refuses to decode an oversized payload."""
    huge = "A" * (ops._MAX_BODY_FETCH_BYTES * 2 + 4)
    attachments = _Attachments({"att-1": huge})
    svc = _Service(_Users(messages=_MessagesWithAttachments(attachments)))
    assert ops._make_body_fetcher(svc)("m1", "att-1") == ""


def test_real_file_attachment_is_never_fetched_as_a_body():
    """A part with a filename is a genuine file attachment. Reading attachment
    CONTENT stays out of scope — only text BODIES are recovered."""
    fetched = []
    part = _stored_part("text/plain", "att-1", 10, filename="notes.txt")
    out = ops._part_text(part, fetch=lambda aid: fetched.append(aid) or "secret file")
    assert out == ""
    assert fetched == []


def test_missing_declared_size_fails_closed_and_never_fetches():
    """Gmail has no ranged read, so an undeclared size cannot be bounded before the
    download. The sender chooses the message shape, so this must fail closed —
    otherwise the 256 KB cap is trivially bypassed by omitting `size`."""
    fetched = []
    part = {"mimeType": "text/plain", "body": {"data": "", "attachmentId": "att-1"}}
    assert ops._part_text(part, fetch=lambda aid: fetched.append(aid) or "huge") == ""
    assert fetched == []


def test_unparseable_declared_size_also_fails_closed():
    fetched = []
    part = {"mimeType": "text/plain",
            "body": {"data": "", "attachmentId": "att-1", "size": "not-a-number"}}
    assert ops._part_text(part, fetch=lambda aid: fetched.append(aid) or "huge") == ""
    assert fetched == []


def test_get_thread_op_recovers_stored_bodies_with_one_thread_call():
    """End-to-end: the thread read is still ONE threads.get (the two-step fetch was
    declined in #43), and the stored body is recovered through the thread-scoped
    fetcher built inside the already-allow-listed op."""
    attachments = _Attachments({"att-1": _b64("the real body")})
    thread = {"messages": [{
        "id": "m1",
        "threadId": "t1",
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [{"name": "Subject", "value": "Big one"}],
            "parts": [_stored_part("text/plain", "att-1", 13)],
        },
    }]}
    threads = _Threads(thread)
    svc = _Service(_Users(messages=_MessagesWithAttachments(attachments), threads=threads))

    out = ops.get_thread_op(svc, "t1")
    assert out["messages"][0]["body"] == "the real body"
    assert attachments.calls == [("m1", "att-1")]


def test_get_thread_op_budgets_attachment_fetches_per_thread():
    """The MIME walk is breadth-unbounded AND a thread holds up to 20 messages, so
    ONE budget for the whole read is what stops a sender shaping a single
    gmail_read_thread into dozens of sequential round-trips."""
    blobs = {f"att-{i}": _b64(f"body {i}") for i in range(6)}
    attachments = _Attachments(blobs)
    # Six sibling text/plain parts, each stored out-of-line.
    parts = [_stored_part("text/plain", f"att-{i}", 10) for i in range(6)]
    thread = {"messages": [{"id": "m1", "threadId": "t1",
                            "payload": {"mimeType": "multipart/mixed", "headers": [], "parts": parts}}]}
    svc = _Service(_Users(messages=_MessagesWithAttachments(attachments), threads=_Threads(thread)))

    out = ops.get_thread_op(svc, "t1")
    # Exact, not `<=`: a one-sided bound would also pass if the fetcher were broken
    # into never fetching at all, which is the regression that matters most here.
    assert len(attachments.calls) == ops._MAX_BODY_FETCHES_PER_THREAD
    # ...and the budget was spent usefully — a body did come back. Which sibling
    # wins is PRE-EXISTING behavior (the last non-empty text/plain part), preserved
    # unchanged by this refactor; asserted here only to prove a fetch succeeded.
    assert out["messages"][0]["body"] == f"body {ops._MAX_BODY_FETCHES_PER_THREAD - 1}"


def test_attachment_fetch_budget_spans_the_whole_thread_not_each_message():
    """The budget is per THREAD READ. Per-message it would scale with message
    count, letting a sender turn one read into dozens of sequential round-trips."""
    n_messages = 10
    blobs = {f"att-{i}": _b64(f"body {i}") for i in range(n_messages)}
    attachments = _Attachments(blobs)
    thread = {"messages": [
        {"id": f"m{i}", "threadId": "t1", "payload": {
            "mimeType": "multipart/mixed", "headers": [],
            "parts": [_stored_part("text/plain", f"att-{i}", 10)]}}
        for i in range(n_messages)
    ]}
    svc = _Service(_Users(messages=_MessagesWithAttachments(attachments), threads=_Threads(thread)))

    out = ops.get_thread_op(svc, "t1")
    assert len(attachments.calls) == ops._MAX_BODY_FETCHES_PER_THREAD
    # Every message is still returned; the ones past the budget just have no body.
    assert len(out["messages"]) == n_messages
    assert sum(1 for m in out["messages"] if m["body"]) == ops._MAX_BODY_FETCHES_PER_THREAD


def test_oversize_marker_does_not_shadow_a_readable_alternative():
    """An unreadable oversized text/plain must not hide a perfectly good text/html
    alternative carrying the same message — the marker is truthy, so it has to be
    held aside rather than treated as recovered text."""
    payload = {"mimeType": "multipart/alternative", "parts": [
        _stored_part("text/plain", "att-1", ops._MAX_BODY_FETCH_BYTES + 1),
        {"mimeType": "text/html", "body": {"data": _b64("<p>the readable one</p>")}},
    ]}
    assert ops._get_body_text(payload, fetch=lambda aid: "") == "the readable one"


def test_oversize_marker_is_shown_when_nothing_else_is_readable():
    payload = {"mimeType": "multipart/alternative", "parts": [
        _stored_part("text/plain", "att-1", ops._MAX_BODY_FETCH_BYTES + 1),
    ]}
    assert ops._get_body_text(payload, fetch=lambda aid: "") == ops._BODY_TOO_LARGE


def test_get_thread_op_survives_attachment_fetch_errors():
    """A failing attachment fetch must not fail the whole thread read."""
    attachments = _Attachments({}, fail=True)
    thread = {"messages": [{"id": "m1", "threadId": "t1", "payload": {
        "mimeType": "multipart/alternative", "headers": [],
        "parts": [_stored_part("text/plain", "att-1", 10)],
    }}]}
    svc = _Service(_Users(messages=_MessagesWithAttachments(attachments), threads=_Threads(thread)))

    out = ops.get_thread_op(svc, "t1")
    assert out["messages"][0]["body"] == ""
    assert out["message_count"] == 1


def test_get_thread_op_makes_exactly_one_threads_get_call():
    """Pins the #43 decision to keep the single full-thread fetch: a two-step
    metadata-then-messages fetch would double every common-case read."""
    calls = []

    class _CountingThreads(_Threads):
        def get(self, userId, id, format):
            calls.append(format)
            return _Exec(self._thread)

    thread = {"messages": [
        {"id": f"m{i}", "threadId": "t1",
         "payload": {"mimeType": "text/plain", "headers": [], "body": {"data": _b64(f"body {i}")}}}
        for i in range(3)
    ]}
    svc = _Service(_Users(threads=_CountingThreads(thread)))
    out = ops.get_thread_op(svc, "t1")
    assert calls == ["full"]
    assert [m["body"] for m in out["messages"]] == ["body 0", "body 1", "body 2"]


def test_malformed_inline_base64_degrades_one_part_not_the_thread():
    """A part whose data won't decode must blank that part only — not raise out of
    the MIME walk and take the whole thread read with it."""
    bad = {"mimeType": "text/plain", "body": {"data": "!!!not-base64!!!"}}
    good_html = {"mimeType": "text/html", "body": {"data": _b64("<p>good</p>")}}
    assert ops._part_text(bad) == ""

    payload = {"mimeType": "multipart/alternative", "parts": [bad, good_html]}
    assert ops._get_body_text(payload) == "good"


def test_repeated_fetch_failures_log_once_per_thread_read():
    """One broken mailbox must not emit a warning per failed fetch."""
    import logging

    failing = _Attachments({}, fail=True)
    svc = _Service(_Users(messages=_MessagesWithAttachments(failing)))
    fetch = ops._make_body_fetcher(svc)

    logger = logging.getLogger("gmail.ops")
    records = []
    handler = logging.Handler()
    handler.emit = records.append
    logger.addHandler(handler)
    try:
        fetch("m1", "att-1")
        fetch("m1", "att-2")
    finally:
        logger.removeHandler(handler)

    assert len([r for r in records if r.levelno >= logging.WARNING]) == 1
