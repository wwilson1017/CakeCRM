"""The three assistant Gmail tools + get_gmail_tools() (issue #8).

Exactly three tools — gmail_search, gmail_read_thread (reads), gmail_create_draft
(the only write). NO send/reply tool exists; there is no code path to send email.
Executors follow the registry contract: plain sync `def(**kwargs) -> dict`, never
raise (return {"error": ...}); the confirmation gate keys off the "writes" flag.

Since #194 the tools are gated on the SEAT as well as the connection — see
get_gmail_tools() below. That gate decides who is offered the tools; it never widens
what they can do.
"""

from __future__ import annotations

import logging
from typing import Callable

from gmail import client, ops, store
from gmail.client import GmailAuthError, GmailTimeoutError

logger = logging.getLogger(__name__)

_MAX_SEARCH_RESULTS = 25

GMAIL_TOOL_DEFS: list[dict] = [
    {
        "name": "gmail_search",
        "writes": False,
        "description": (
            "Search the connected Gmail account. Supports Gmail search operators "
            "(from:, to:, subject:, is:unread, newer_than:7d, has:attachment, ...). "
            "Each result includes a thread_id usable with gmail_read_thread."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Gmail search query, e.g. 'from:jane@acme.com newer_than:30d'",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max results (default 10, capped at 25)",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
        "kind": "gmail",
    },
    {
        "name": "gmail_read_thread",
        "writes": False,
        "description": (
            "Read all messages in a Gmail thread (bodies as plain text, plus "
            "attachment names). Use a thread_id from gmail_search."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "thread_id": {"type": "string", "description": "Thread ID from gmail_search"},
            },
            "required": ["thread_id"],
        },
        "kind": "gmail",
    },
    {
        "name": "gmail_create_draft",
        "writes": True,
        "description": (
            "Create a DRAFT email in the user's Gmail Drafts folder. CakeCRM can "
            "NEVER send email — the user reviews and sends the draft themselves from "
            "Gmail. The draft is not sent by this tool."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address(es), comma-separated"},
                "subject": {"type": "string"},
                "body": {"type": "string", "description": "Plain-text body"},
                "cc": {"type": "string", "default": ""},
                "bcc": {"type": "string", "default": ""},
            },
            "required": ["to", "subject", "body"],
        },
        "kind": "gmail",
    },
]


def gmail_search(query: str, max_results: int = 10) -> dict:
    try:
        n = max(1, min(int(max_results), _MAX_SEARCH_RESULTS))
    except (TypeError, ValueError):
        n = 10
    try:
        msgs = client.call_gmail(ops.list_messages_op, query=query, max_results=n)
        return {"messages": msgs, "count": len(msgs)}
    except GmailAuthError as e:
        return {"error": str(e), "needs_reconnect": True}
    except GmailTimeoutError as e:
        # Retryable, and NOT a broken connection — deliberately no needs_reconnect.
        return {"error": str(e)}
    except Exception as e:
        logger.error("gmail_search failed: %s", e)
        return {"error": "Gmail search failed. Please try again."}


def gmail_read_thread(thread_id: str) -> dict:
    try:
        return client.call_gmail(ops.get_thread_op, thread_id=thread_id)
    except GmailAuthError as e:
        return {"error": str(e), "needs_reconnect": True}
    except GmailTimeoutError as e:
        # Retryable, and NOT a broken connection — deliberately no needs_reconnect.
        return {"error": str(e)}
    except Exception as e:
        logger.error("gmail_read_thread failed: %s", e)
        return {"error": "Reading the Gmail thread failed. Please try again."}


def gmail_create_draft(to: str, subject: str, body: str, cc: str = "", bcc: str = "") -> dict:
    try:
        result = client.call_gmail(
            ops.create_draft_op, to=to, subject=subject, body=body, cc=cc, bcc=bcc
        )
        result["note"] = "Draft created in Gmail — review and send it yourself. CakeCRM does not send email."
        return result
    except GmailAuthError as e:
        return {"error": str(e), "needs_reconnect": True}
    except GmailTimeoutError as e:
        # NOT a broken connection — deliberately no needs_reconnect. But this is a WRITE,
        # so whether a retry is safe depends on how far the request got: refused before it
        # was sent means nothing reached Gmail, while a mid-flight stall means Gmail may
        # have created the draft and only lost the response. Saying "nothing happened"
        # there would invite a duplicate draft.
        if e.started:
            return {"error": f"{e} Check Gmail for a draft before retrying — it may have "
                             "been created even though the reply was lost."}
        return {"error": f"{e} No draft was created, so retrying is safe."}
    except Exception as e:
        logger.error("gmail_create_draft failed: %s", e)
        return {"error": "Creating the Gmail draft failed. Please try again."}


# ── Pending-confirmation binding (issue #43) ──────────────────────────────────
#
# gmail_create_draft is proposed in one turn and approved later — potentially
# minutes later, from the web UI or a Telegram button. In between, the admin can
# disconnect Gmail or connect a DIFFERENT Google account, and the approved draft
# would land in whichever account happens to be live. These two helpers bind the
# connection generation observed at propose time to the pending confirmation; the
# assistant engine stamps it into the pending placeholder and checks it before
# executing. They are plain module functions, NOT agent tools — the tool surface
# stays exactly the three defs above.

BINDING_KEY = "gmail_generation"


def _live_generation() -> int | None:
    """The connection's current generation, or None when it could not be read.

    `store.get_row()` never raises — it swallows read failures and returns {} — so
    "unreadable" MUST be distinguished here rather than in an except block. Reading
    it as `... or 0` would turn a transient DB blip into a *bogus* generation 0,
    which is never a real connected value and would falsely refuse a valid draft.
    """
    try:
        row = store.get_row() or {}
    except Exception as e:  # defence in depth; get_row is documented never to raise
        logger.warning("gmail.tools: could not read the connection generation: %s", e)
        return None
    gen = row.get("connection_generation")
    if gen is None:
        logger.warning("gmail.tools: connection generation unavailable (row unreadable)")
        return None
    try:
        return int(gen)
    except (TypeError, ValueError):
        return None


def pending_binding() -> dict:
    """Keys to merge into the pending-confirmation placeholder for a Gmail write.

    Reads Postgres, so callers on the event loop must offload it. Returns {} when
    the connection can't be read — an unbindable proposal simply behaves as it did
    before #43 rather than blocking the write.
    """
    gen = _live_generation()
    return {} if gen is None else {BINDING_KEY: gen}


def binding_conflict(placeholder: dict | None) -> dict | None:
    """An error result when the Gmail connection changed since the draft was
    proposed, else None (meaning: go ahead and execute).

    Never raises — it runs after the pending call has already been claimed and
    marked executing, so an exception here would strand the confirmation. Anything
    unreadable (no binding key, a hand-edited placeholder, an unreachable store)
    resolves to None and executes exactly as before #43. In particular an
    unreadable generation must NOT be reported as a conflict: the draft would be
    refused with a message saying the account changed when nothing had.
    """
    bound = (placeholder or {}).get(BINDING_KEY)
    if bound is None:
        return None  # proposed before #43 shipped, or unbindable at propose time
    live = _live_generation()
    if live is None:
        return None  # can't verify; the executor's own auth path reports a dead link
    try:
        if int(bound) == live:
            return None
    except (TypeError, ValueError):
        return None  # hand-edited / corrupt placeholder
    return {
        "error": (
            "The Gmail connection changed after this draft was proposed, so it was not "
            "created. Ask again to draft it against the account connected now."
        )
    }


GMAIL_TOOL_EXECUTORS: dict[str, Callable[..., dict]] = {
    "gmail_search": gmail_search,
    "gmail_read_thread": gmail_read_thread,
    "gmail_create_draft": gmail_create_draft,
}


def get_gmail_tools(user: dict | None = None) -> tuple[list[dict], dict[str, Callable[..., dict]]]:
    """(defs, executors) for the assistant registry — gated on the connection AND the seat.

    Three reasons to return the empty pair, tested in this order:

    1. ``user is None`` — an unattended turn (heartbeat, the proactive digest, any
       future background caller). It needs NO database, so the denial holds even when
       the connection row is unreadable, and it sits ABOVE the sharing test on purpose:
       "share the mailbox with all seats" widens the gate to *seats*, and a turn with no
       user is not a seat. This strictly narrows the background surface, which
       ``assistant.background.BACKGROUND_EXCLUDED_TOOLS`` (#114) also enforces one layer
       down at allowlist time — two independent locks, neither relying on the other.
    2. Gmail is not connected — a disconnected/keyless instance hides the tools from the
       model entirely (hidden affordance, never an error). Unchanged since #8.
    3. The caller is a MEMBER seat and this install has not opted into sharing (#194).
       There is exactly one mailbox per install, whichever account an admin connected,
       so advertising it to every seat is how a member came to be able to read the
       admin's mail. Admins are unaffected; an install that deliberately runs one shared
       inbox turns ``share_with_all_seats`` on and every seat is offered the tools again.

    This changes only WHO is offered the tools. The three tools, their ``writes`` flags,
    the op allow-list, the scopes and the draft's connection binding are untouched: read
    + create-draft only, forever. A stale or replayed call from a seat that no longer
    holds them degrades to the registry's unknown-tool error dict, never a raise.

    MUST never raise: ToolRegistry() is constructed with no DB in the hermetic suite,
    and get_gmail_tools() is called there.
    """
    if user is None:
        return [], {}
    try:
        connected = store.is_connected()
    except Exception:
        connected = False
    if not connected:
        return [], {}
    if user.get("role") != "admin":
        try:
            shared = store.sharing_enabled()
        except Exception:
            shared = False
        if not shared:
            return [], {}
    return GMAIL_TOOL_DEFS, GMAIL_TOOL_EXECUTORS
