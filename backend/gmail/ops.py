"""Gmail operations — read + create-draft ONLY (ported from chatty's gmail_ops,
reduced). Each op takes an authenticated Gmail v1 service as its first argument.

NOT ported (deliberately, so no send/modify path exists): the send and reply
operations, the mark-as-read/modify ops, and attachment sending. The three tool
executors call ONLY the ops defined here; client.call_gmail additionally
allow-lists them at runtime. See SECURITY.md.

Imports are stdlib-only (base64/re/email/html), safe at module top level.
"""

from __future__ import annotations

import base64
import re
from email.message import EmailMessage
from html import unescape

_TAG_RE = re.compile(r"<[^>]+>")
_MULTI_NL = re.compile(r"\n{3,}")

# Bound the sensitive data pulled into the model context / persisted history.
_MAX_BODY_CHARS = 4000
_MAX_THREAD_MESSAGES = 20


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


def _get_body_text(payload: dict) -> str:
    mime_type = payload.get("mimeType", "")

    if mime_type == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

    if mime_type == "text/html":
        data = payload.get("body", {}).get("data", "")
        if data:
            html = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            return _html_to_text(html)

    parts = payload.get("parts", [])
    plain_text = ""
    html_text = ""
    for part in parts:
        part_mime = part.get("mimeType", "")
        if part_mime == "text/plain":
            data = part.get("body", {}).get("data", "")
            if data:
                plain_text = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        elif part_mime == "text/html":
            data = part.get("body", {}).get("data", "")
            if data:
                html = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                html_text = _html_to_text(html)
        elif part_mime.startswith("multipart/"):
            nested = _get_body_text(part)
            if nested:
                return nested

    return plain_text or html_text


def _truncate_body(text: str) -> str:
    if len(text) > _MAX_BODY_CHARS:
        return text[:_MAX_BODY_CHARS] + "\n[... truncated ...]"
    return text


def _get_attachments(payload: dict) -> list[dict]:
    """Attachment METADATA only (names/types/sizes) — reading attachment content
    is out of scope."""
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
            attachments.extend(_get_attachments(part))
    return attachments


def _format_message(msg: dict) -> dict:
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
        "body": _truncate_body(_get_body_text(payload)),
        "attachments": _get_attachments(payload),
    }


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
    only."""
    thread = service.users().threads().get(
        userId="me", id=thread_id, format="full"
    ).execute()
    all_messages = thread.get("messages", [])
    total = len(all_messages)
    # Keep the most-recent messages when a thread is very long.
    kept = all_messages[-_MAX_THREAD_MESSAGES:] if total > _MAX_THREAD_MESSAGES else all_messages
    messages = [_format_message(m) for m in kept]
    result = {
        "thread_id": thread_id,
        "message_count": total,
        "messages": messages,
    }
    if total > len(messages):
        result["truncated"] = True
        result["note"] = f"Showing the {len(messages)} most recent of {total} messages in this thread."
    return result


def _get_profile_op(service) -> dict:
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
