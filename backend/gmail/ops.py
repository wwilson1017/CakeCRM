"""Gmail operations — read + create-draft ONLY (ported from chatty's gmail_ops,
reduced). Each op takes an authenticated Gmail v1 service as its first argument.

NOT ported (deliberately, so no send/modify path exists): the send and reply
operations, the mark-as-read/modify ops, and attachment sending. The three tool
executors call ONLY the ops defined here; client.call_gmail additionally
allow-lists them at runtime. See SECURITY.md.

Imports are stdlib-only (base64/logging/re/email/html), safe at module top level.
"""

from __future__ import annotations

import base64
import functools
import logging
import re
from email.message import EmailMessage
from html import unescape

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_MULTI_NL = re.compile(r"\n{3,}")

# Bound the sensitive data pulled into the model context / persisted history.
_MAX_BODY_CHARS = 4000
_MAX_THREAD_MESSAGES = 20
# Cap recursion into attacker-controllable MIME part trees (a hostile sender can
# nest multipart parts arbitrarily deep; without a cap a read would RecursionError).
_MAX_MIME_DEPTH = 20
# Gmail moves a large text body out of the inline `body.data` and into a separately
# fetchable `body.attachmentId`. We recover those (issue #43) under a hard byte cap:
# only 4000 CHARS are ever kept, but HTML markup means recovering that much readable
# text can need far more raw bytes, so the cap is generous while still bounding the
# transient download.
_MAX_BODY_FETCH_BYTES = 262144
# The budget is per THREAD READ, not per message: the MIME walk is breadth-unbounded
# and a thread holds up to 20 messages, so a per-message budget would let a sender
# shape one gmail_read_thread into ~40 sequential round-trips. A genuinely oversized
# body is rare — one or two per thread — so a thread-wide budget keeps the feature
# while capping the worst case at 4 fetches / ~1 MB.
_MAX_BODY_FETCHES_PER_THREAD = 4
# Shown instead of a blank body when the stored text is too large to pull in, so the
# model can tell "this email is empty" from "this body was not retrieved".
_BODY_TOO_LARGE = "[body too large to display]"


# ── Helpers ──────────────────────────────────────────────────────────────────

def _html_to_text(html: str) -> str:
    """Strip HTML tags and decode entities to plain text."""
    if not html:
        return ""
    text = html.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    text = text.replace("</p>", "\n\n").replace("</div>", "\n")
    text = _TAG_RE.sub("", text)
    text = unescape(text)
    text = _MULTI_NL.sub("\n\n", text).strip()
    return text


def _parse_headers(headers: list[dict]) -> dict:
    result = {}
    for h in headers:
        name = h.get("name", "").lower()
        if name in ("from", "to", "subject", "date", "cc", "bcc", "message-id", "references", "in-reply-to"):
            result[name] = h.get("value", "")
    return result


def _declared_size(body: dict) -> int | None:
    """The part's declared decoded byte size, or None when it didn't declare one."""
    try:
        return int(body["size"])
    except (KeyError, TypeError, ValueError):
        return None


def _is_attachment_part(part: dict) -> bool:
    """True when a text part is a FILE attachment rather than the message body.

    A `filename` is the common signal, but `Content-Disposition: attachment` with no
    filename parameter is valid MIME and still a file. Body recovery must skip both,
    or the "message bodies only, never your files" guarantee in SECURITY.md leaks
    through the nameless case.
    """
    if part.get("filename"):
        return True
    for header in part.get("headers") or []:
        if header.get("name", "").lower() == "content-disposition":
            return header.get("value", "").strip().lower().startswith("attachment")
    return False


def _stored_body_id(part: dict) -> str:
    """The attachmentId of an out-of-line TEXT BODY, or "" if this part isn't one."""
    body = part.get("body") or {}
    if body.get("data") or _is_attachment_part(part):
        return ""
    return body.get("attachmentId") or ""


def _part_text(part: dict, fetch=None) -> str:
    """Plain text for one text/plain or text/html part ("" if it carries none).

    Prefers the inline `body.data`. When Gmail stored a large text body separately
    (empty `data` plus a `body.attachmentId`) and a fetcher is supplied, recover it
    under the byte cap (#43). Strictly text bodies: a file attachment — named OR
    merely disposition-marked — is NEVER fetched; those stay metadata-only via
    _get_attachments. `fetch=None` reproduces the pre-#43 behavior exactly.
    """
    mime_type = part.get("mimeType", "")
    if mime_type not in ("text/plain", "text/html"):
        return ""
    body = part.get("body") or {}
    data = body.get("data", "")
    if data:
        try:
            text = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        except (ValueError, TypeError):
            # Malformed base64 degrades this ONE part to blank rather than failing
            # the whole thread read.
            return ""
    elif fetch and _stored_body_id(part):
        size = _declared_size(body)
        # Fail CLOSED on an undeclared size. Gmail has no ranged read, so an
        # unknown size means we cannot bound the download before making it — and
        # the sender of this mail chose its shape. Skipping costs nothing that
        # wasn't already blank before #43.
        if size is None:
            return ""
        if size > _MAX_BODY_FETCH_BYTES:
            return _BODY_TOO_LARGE
        text = fetch(body["attachmentId"])  # never raises; "" on any failure
    else:
        return ""
    if not text:
        return ""
    # HTML must be flattened here just as inline HTML is — gmail_read_thread's
    # contract is plain text, and raw markup would burn the 4000-char budget.
    return _html_to_text(text) if mime_type == "text/html" else text


def _get_body_text(payload: dict, _depth: int = 0, fetch=None) -> str:
    if _depth > _MAX_MIME_DEPTH:
        return ""

    direct = _part_text(payload, fetch)
    if direct:
        return direct

    # Walk the parts inline-only first, collecting the ones whose body Gmail stored
    # out of line. Fetching is deferred to the resolution step below so the thread's
    # fetch budget is never spent on an alternative we would discard: plain wins over
    # html, so a message with BOTH stored would otherwise burn two slots per message.
    inline_plain = ""
    inline_html = ""
    stored_plain: list[dict] = []
    stored_html: list[dict] = []
    for part in payload.get("parts", []):
        part_mime = part.get("mimeType", "")
        if part_mime in ("text/plain", "text/html"):
            text = _part_text(part)  # inline only — never fetches
            is_plain = part_mime == "text/plain"
            if text:
                if is_plain:
                    inline_plain = text
                else:
                    inline_html = text
            elif _stored_body_id(part):
                (stored_plain if is_plain else stored_html).append(part)
        elif part_mime.startswith("multipart/"):
            nested = _get_body_text(part, _depth + 1, fetch)
            if nested:
                return nested

    oversize = ""

    def _resolve(inline: str, stored: list[dict]) -> str:
        nonlocal oversize
        if inline or not fetch:
            return inline
        for candidate in stored:
            text = _part_text(candidate, fetch)
            # The oversize marker is truthy, so hold it aside — an unreadable
            # text/plain must not shadow a readable text/html alternative.
            if text == _BODY_TOO_LARGE:
                oversize = text
            elif text:
                return text
        return ""

    return _resolve(inline_plain, stored_plain) or _resolve(inline_html, stored_html) or oversize


def _truncate_body(text: str) -> str:
    if len(text) > _MAX_BODY_CHARS:
        return text[:_MAX_BODY_CHARS] + "\n[... truncated ...]"
    return text


def _get_attachments(payload: dict, _depth: int = 0) -> list[dict]:
    """Attachment METADATA only — names, types, sizes; this never reads a byte of
    attachment content, and a real file's content is never read anywhere.

    The one narrow exception lives in _part_text: a large text BODY that Gmail
    stored out of line behind an attachmentId is recovered there under a byte cap
    (#43). Parts that are actual files — named, or marked
    `Content-Disposition: attachment` — are excluded from that path."""
    if _depth > _MAX_MIME_DEPTH:
        return []
    attachments = []
    parts = payload.get("parts", [])
    for part in parts:
        filename = part.get("filename", "")
        if filename:
            attachments.append({
                "filename": filename,
                "mime_type": part.get("mimeType", ""),
                "size": part.get("body", {}).get("size", 0),
            })
        if part.get("parts"):
            attachments.extend(_get_attachments(part, _depth + 1))
    return attachments


def _format_message(msg: dict, fetch=None) -> dict:
    payload = msg.get("payload", {})
    headers = _parse_headers(payload.get("headers", []))
    label_ids = msg.get("labelIds", [])
    return {
        "id": msg.get("id"),
        "thread_id": msg.get("threadId"),
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "cc": headers.get("cc", ""),
        "subject": headers.get("subject", ""),
        "date": headers.get("date", ""),
        "snippet": msg.get("snippet", ""),
        "is_unread": "UNREAD" in label_ids,
        "labels": label_ids,
        "body": _truncate_body(_get_body_text(payload, fetch=fetch)),
        "attachments": _get_attachments(payload),
    }


def _make_body_fetcher(service):
    """Thread-scoped fetcher for text bodies Gmail stored outside `body.data` (#43).

    Strictly read-only (`users.messages.attachments.get`, covered by the existing
    gmail.readonly scope) and executed from INSIDE the already-allow-listed
    get_thread_op, so no new entry in client._APPROVED_OPS is introduced and the
    read+draft-only surface is unchanged.

    ONE budget is shared across every message in the thread — see
    _MAX_BODY_FETCHES_PER_THREAD. Never raises: every failure returns "", which is
    exactly the blank body callers saw before #43.

    Returns a two-arg callable; get_thread_op partially applies the message id so
    the MIME walk only ever sees `fetch(attachment_id)`.
    """
    remaining = [_MAX_BODY_FETCHES_PER_THREAD]
    warned = [False]

    def fetch(message_id: str, attachment_id: str) -> str:
        if remaining[0] <= 0:
            return ""
        remaining[0] -= 1
        try:
            att = service.users().messages().attachments().get(
                userId="me", messageId=message_id, id=attachment_id,
            ).execute()
            data = att.get("data") or ""
            # Backstop for a part that under-declared body.size (the pre-fetch cap
            # check in _part_text is the primary guard). base64 inflates ~4/3, so
            # this sits comfortably above the byte budget we agreed to pull.
            if len(data) > _MAX_BODY_FETCH_BYTES * 2:
                return ""
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        except Exception as e:
            # warning, not debug: a systemic failure here degrades every thread read
            # to blank bodies, which is indistinguishable from genuinely empty mail.
            # Once per thread read, so a bad thread can't flood the log.
            if not warned[0]:
                warned[0] = True
                logger.warning("gmail.ops: body attachment fetch failed for a message: %s", e)
            return ""

    return fetch


# ── Read ops ─────────────────────────────────────────────────────────────────

def list_messages_op(service, query: str = "", max_results: int = 20) -> list[dict]:
    """Search messages; return a summary (incl. thread_id) for each."""
    results = service.users().messages().list(
        userId="me", q=query, maxResults=max_results
    ).execute()

    messages = results.get("messages", [])
    if not messages:
        return []

    summaries = []
    for msg_ref in messages:
        msg = service.users().messages().get(
            userId="me", id=msg_ref["id"], format="metadata",
            metadataHeaders=["From", "To", "Subject", "Date"],
        ).execute()
        headers = _parse_headers(msg.get("payload", {}).get("headers", []))
        label_ids = msg.get("labelIds", [])
        summaries.append({
            "id": msg["id"],
            "thread_id": msg.get("threadId"),
            "from": headers.get("from", ""),
            "to": headers.get("to", ""),
            "subject": headers.get("subject", ""),
            "date": headers.get("date", ""),
            "snippet": msg.get("snippet", ""),
            "is_unread": "UNREAD" in label_ids,
        })
    return summaries


def get_thread_op(service, thread_id: str) -> dict:
    """All messages in a thread (bodies as plain text, capped). Attachment names
    only.

    Deliberately ONE `threads.get(format="full")` call. A two-step
    metadata-then-retained-messages fetch (to bound peak download memory) was
    evaluated in #43 and declined: it makes the common case two calls instead of one
    — and a long thread 1 + 20 — doubling latency and quota on every read, to bound
    transient memory only for rare long threads that Gmail's own conversation model
    already bounds. Model-facing output is bounded regardless, by the per-body char
    cap and the message-count cap below."""
    thread = service.users().threads().get(
        userId="me", id=thread_id, format="full"
    ).execute()
    all_messages = thread.get("messages", [])
    total = len(all_messages)
    # Keep the most-recent messages when a thread is very long.
    kept = all_messages[-_MAX_THREAD_MESSAGES:] if total > _MAX_THREAD_MESSAGES else all_messages
    # One fetcher, one budget, for the whole thread — and spend it NEWEST-first.
    # `kept` runs oldest→newest, so formatting in order would hand the budget to the
    # oldest messages and return the latest replies (what the user actually asked
    # about) blank. Format in reverse, then restore chronological order.
    body_fetcher = _make_body_fetcher(service)
    messages = [
        _format_message(m, fetch=functools.partial(body_fetcher, m.get("id", "")))
        for m in reversed(kept)
    ][::-1]
    result = {
        "thread_id": thread_id,
        "message_count": total,
        "messages": messages,
    }
    if total > len(messages):
        result["truncated"] = True
        result["note"] = f"Showing the {len(messages)} most recent of {total} messages in this thread."
    return result


def get_profile_op(service) -> dict:
    """The connected account's email (used by the OAuth callback to record which
    account was linked). Requires gmail.readonly."""
    profile = service.users().getProfile(userId="me").execute()
    return {"email": profile.get("emailAddress", "")}


# ── MIME builder (plain body, no attachments — reply-threading is out of scope) ──

def _build_mime(to: str, subject: str, body_text: str, cc: str = "", bcc: str = "") -> str:
    """Build an RFC 2822 plain-text message; return its base64url raw form."""
    msg = EmailMessage()
    msg.set_content(body_text)
    msg["To"] = to
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")


# ── Draft op (the ONLY write) ─────────────────────────────────────────────────

def create_draft_op(service, to: str, subject: str, body: str, cc: str = "", bcc: str = "") -> dict:
    """Create a Gmail draft (users.drafts.create). It lands in the user's Drafts
    folder — CakeCRM never sends it. Returns {ok, draft_id, message_id}."""
    raw = _build_mime(to, subject, body, cc=cc, bcc=bcc)
    draft = service.users().drafts().create(
        userId="me",
        body={"message": {"raw": raw}},
    ).execute()
    return {
        "ok": True,
        "draft_id": draft.get("id"),
        "message_id": draft.get("message", {}).get("id"),
    }
