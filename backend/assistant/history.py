"""Postgres conversation history for the assistant.

A straight reimplementation of Chatty's SQLite ``ChatHistoryService`` interface on
Postgres (schema owned by ``migrations/*_assistant.sql``), accessed through the
``core.postgres`` helpers. Two tables: ``assistant_conversations`` and
``assistant_messages`` (one row per model iteration of a turn).

Key differences from the SQLite original, all deliberate:
- ``tool_calls`` / ``tool_results`` cross this API as **Python lists**, never JSON
  strings — psycopg2 returns JSONB already parsed, so nothing here ``json.loads``.
- Check-then-write paths (seq allocation, per-result merge, the confirm claim) run
  in one transaction with ``SELECT ... FOR UPDATE`` (repo rule; there is no
  process-wide write lock like Chatty's SQLite).

Every function here is synchronous, blocking psycopg2 code. The async streaming
engine must call these via ``asyncio.to_thread``; the non-streaming router
endpoints are sync handlers (FastAPI runs them in its threadpool).
"""

import json
import uuid

from core.postgres import (
    get_connection,
    pg_execute,
    pg_fetchall,
    pg_fetchone,
    row_to_dict,
)

# Result-status sentinels stored (JSON-encoded) in a tool_results entry's content.
PENDING_STATUS = "pending_user_approval"
EXECUTING_STATUS = "executing"
DENIED_STATUS = "denied_by_user"

PENDING_RESULT_JSON = json.dumps({"status": PENDING_STATUS})
_EXECUTING_RESULT_JSON = json.dumps({"status": EXECUTING_STATUS})
DENIED_RESULT_JSON = json.dumps({"status": DENIED_STATUS})

_TITLE_MAX = 60
_RENAME_MAX = 100


def _status_of(content: str | None) -> str | None:
    """Return the ``status`` field of a JSON-encoded result content, or None."""
    if not content:
        return None
    try:
        parsed = json.loads(content)
    except (ValueError, TypeError):
        return None
    return parsed.get("status") if isinstance(parsed, dict) else None


def is_pending_result(content: str | None) -> bool:
    """True iff a stored result content is exactly the pending-approval placeholder."""
    return _status_of(content) == PENDING_STATUS


# ── Conversations ──────────────────────────────────────────────────────────

def create_conversation() -> dict:
    conv_id = str(uuid.uuid4())
    return pg_fetchone(
        "INSERT INTO assistant_conversations (id) VALUES (%s) "
        "RETURNING id, title, title_edited_by_user, created_at, updated_at",
        (conv_id,),
    )


def conversation_exists(conv_id: str) -> bool:
    return pg_fetchone("SELECT 1 AS ok FROM assistant_conversations WHERE id = %s", (conv_id,)) is not None


def auto_title(conversation_id: str, first_message: str) -> str:
    """Set an un-edited conversation's title from the first user message.

    Never overrides a title the user has edited. Returns the resolved title.
    """
    title = " ".join((first_message or "").split()).strip()
    if len(title) > _TITLE_MAX:
        title = title[: _TITLE_MAX - 3].rstrip() + "..."
    if not title:
        title = "New conversation"
    pg_execute(
        "UPDATE assistant_conversations SET title = %s, updated_at = now() "
        "WHERE id = %s AND title_edited_by_user = FALSE",
        (title, conversation_id),
    )
    return title


def list_conversations(limit: int = 50, offset: int = 0) -> list[dict]:
    """Conversations newest-first, each with a message count and a short preview."""
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    return pg_fetchall(
        """
        SELECT c.id, c.title, c.title_edited_by_user, c.created_at, c.updated_at,
               COALESCE(m.msg_count, 0) AS message_count,
               COALESCE(u.preview, '')  AS preview
        FROM assistant_conversations c
        LEFT JOIN (
            SELECT conversation_id, COUNT(*) AS msg_count
            FROM assistant_messages GROUP BY conversation_id
        ) m ON m.conversation_id = c.id
        LEFT JOIN LATERAL (
            SELECT LEFT(content, 120) AS preview
            FROM assistant_messages
            WHERE conversation_id = c.id AND role = 'user' AND content <> ''
            ORDER BY seq DESC LIMIT 1
        ) u ON TRUE
        ORDER BY c.updated_at DESC
        LIMIT %s OFFSET %s
        """,
        (limit, offset),
    )


def get_conversation(conv_id: str) -> dict | None:
    """A conversation with its messages ordered by seq (JSONB → Python lists)."""
    conv = pg_fetchone(
        "SELECT id, title, title_edited_by_user, created_at, updated_at "
        "FROM assistant_conversations WHERE id = %s",
        (conv_id,),
    )
    if conv is None:
        return None
    conv["messages"] = pg_fetchall(
        "SELECT id, role, content, seq, tool_calls, tool_results, model, created_at "
        "FROM assistant_messages WHERE conversation_id = %s ORDER BY seq",
        (conv_id,),
    )
    return conv


def delete_conversation(conv_id: str) -> bool:
    return pg_execute("DELETE FROM assistant_conversations WHERE id = %s", (conv_id,)) > 0


def rename_conversation(conv_id: str, title: str) -> str | None:
    """Set a user-edited title. Returns the new title, or None if blank/not found."""
    clean = " ".join((title or "").split()).strip()[:_RENAME_MAX]
    if not clean:
        return None
    updated = pg_execute(
        "UPDATE assistant_conversations "
        "SET title = %s, title_edited_by_user = TRUE, updated_at = now() WHERE id = %s",
        (clean, conv_id),
    )
    return clean if updated > 0 else None


# ── Messages ───────────────────────────────────────────────────────────────

def save_message(
    conversation_id: str,
    msg_id: str,
    role: str,
    content: str,
    tool_calls: list | None = None,
    model: str = "",
) -> None:
    """Insert one message, allocating ``seq`` atomically under the conversation lock.

    Raises if the conversation does not exist (fail-loud — never silently create).
    """
    with get_connection() as conn:
        cur = conn.cursor()
        # Serialize seq allocation per conversation; also asserts the conversation
        # exists (fail-closed before any tool execution references this row).
        cur.execute(
            "SELECT id FROM assistant_conversations WHERE id = %s FOR UPDATE",
            (conversation_id,),
        )
        if cur.fetchone() is None:
            raise KeyError(f"conversation {conversation_id} does not exist")
        cur.execute(
            "SELECT COALESCE(MAX(seq), -1) + 1 FROM assistant_messages WHERE conversation_id = %s",
            (conversation_id,),
        )
        seq = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO assistant_messages "
            "(id, conversation_id, role, content, seq, tool_calls, model) "
            "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)",
            (
                msg_id,
                conversation_id,
                role,
                content,
                seq,
                json.dumps(tool_calls) if tool_calls is not None else None,
                model,
            ),
        )
        cur.execute(
            "UPDATE assistant_conversations SET updated_at = now() WHERE id = %s",
            (conversation_id,),
        )


def merge_tool_result(msg_id: str, tool_use_id: str, tool_name: str, content: str) -> None:
    """Insert-or-replace one tool result on a message, preserving sibling results.

    Read-modify-write under ``FOR UPDATE`` so concurrent merges on the same row
    (e.g. the engine persisting a placeholder while an approval writes the real
    result for a different tool_use_id) can never clobber each other.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT tool_results FROM assistant_messages WHERE id = %s FOR UPDATE",
            (msg_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise KeyError(f"assistant_message {msg_id} does not exist")
        results = list(row[0] or [])  # JSONB → parsed list
        results = [r for r in results if r.get("tool_use_id") != tool_use_id]
        results.append({"tool_use_id": tool_use_id, "tool_name": tool_name, "content": content})
        cur.execute(
            "UPDATE assistant_messages SET tool_results = %s::jsonb WHERE id = %s",
            (json.dumps(results), msg_id),
        )


def claim_pending_tool(conversation_id: str, tool_use_id: str, msg_id: str | None = None) -> dict | None:
    """Atomically claim a pending write awaiting user approval.

    When ``msg_id`` is given (the confirm SSE event always carries it), the search
    is scoped to that exact message row — critical because a positional-id provider
    (Gemini reuses ``call_0`` every stream_turn) can leave two distinct pending
    writes sharing one ``tool_use_id``, and a tool_use_id-only match could resolve
    the wrong one. Without ``msg_id`` it falls back to scanning the conversation's
    assistant rows newest-first. Marks the matched result ``executing`` under
    ``FOR UPDATE`` (so a concurrent double-approval sees it already claimed) and
    returns the canonical ``{"msg_id", "tool", "args"}`` from the persisted call.
    Returns None when nothing pending matches (already approved/denied/executing,
    unknown, or the row was deleted) — the caller treats that as an idempotent
    no-op. The tool/args come from the DB, not the client, so an approval cannot
    execute an attacker-chosen tool.
    """
    if msg_id:
        candidates = [{"id": msg_id}]
    else:
        candidates = pg_fetchall(
            "SELECT id FROM assistant_messages "
            "WHERE conversation_id = %s AND role = 'assistant' AND tool_calls IS NOT NULL "
            "ORDER BY seq DESC",
            (conversation_id,),
        )
    with get_connection() as conn:
        cur = conn.cursor()
        for cand in candidates:
            # Scope by conversation_id too, so a caller-supplied msg_id can't reach
            # another conversation's row; a concurrently-deleted row → None → skip.
            cur.execute(
                "SELECT tool_calls, tool_results FROM assistant_messages "
                "WHERE id = %s AND conversation_id = %s FOR UPDATE",
                (cand["id"], conversation_id),
            )
            row = cur.fetchone()
            if row is None:
                continue
            locked = row_to_dict(cur, row)
            calls = locked.get("tool_calls") or []
            call = next((c for c in calls if c.get("tool_use_id") == tool_use_id), None)
            if call is None:
                continue
            results = list(locked.get("tool_results") or [])
            existing = next((r for r in results if r.get("tool_use_id") == tool_use_id), None)
            if existing is None or not is_pending_result(existing.get("content")):
                # This call in this row isn't pending; keep scanning older rows in
                # case an earlier iteration reused the same tool_use_id (Gemini).
                continue
            results = [r for r in results if r.get("tool_use_id") != tool_use_id]
            results.append({
                "tool_use_id": tool_use_id,
                "tool_name": call.get("tool"),
                "content": _EXECUTING_RESULT_JSON,
            })
            cur.execute(
                "UPDATE assistant_messages SET tool_results = %s::jsonb WHERE id = %s",
                (json.dumps(results), cand["id"]),
            )
            return {"msg_id": cand["id"], "tool": call.get("tool"), "args": call.get("args") or {}}
    return None
