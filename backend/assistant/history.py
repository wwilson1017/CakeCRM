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


def is_unsettled_result(content: str | None) -> bool:
    """True iff a stored result is still awaiting approval OR mid-execution.

    Additive helper for the Telegram batch gate (issue #7): a write stuck in
    ``executing`` — a crash between ``claim_pending_tool`` and the final
    ``merge_tool_result`` — is not ``pending`` but is also NOT resolved, so a
    continuation must treat it as not-yet-done rather than silently proceeding.
    """
    return _status_of(content) in (PENDING_STATUS, EXECUTING_STATUS)


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
    """Conversations newest-first, each with a message count and a short preview.

    This was the last OFFSET paginator still missing a tiebreaker — the CRM's contact
    and company lists already ended on `id` — so the `c.id` term is what stops a
    conversation appearing on two pages or on none when `updated_at` ties (issue #58).
    The LATERAL's own `ORDER BY seq DESC` needs none: (conversation_id, seq) is unique.
    """
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
        ORDER BY c.updated_at DESC, c.id DESC
        LIMIT %s OFFSET %s
        """,
        (limit, offset),
    )


def get_conversation(conv_id: str) -> dict | None:
    """A conversation with its messages ordered by seq (JSONB → Python lists)."""
    conv = pg_fetchone(
        "SELECT id, title, title_edited_by_user, created_at, updated_at, "
        "       compaction_summary, compaction_first_kept_seq "
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


# ── Compaction state (issue #72 Phase 3) ───────────────────────────────────

def get_compaction_state(conv_id: str) -> dict | None:
    """The conversation's compaction boundary plus its last fullness reading.

    ONE row read answering every question the compaction pass asks, so the common
    case — a thread nowhere near the threshold — costs a single indexed lookup and
    no message scan at all. Returns None when the conversation does not exist.

    Keys: ``summary`` / ``first_kept_seq`` (NULL until a first compaction),
    ``tainted`` (see ``is_conversation_tainted``), ``last_context_tokens``
    (NULL when no provider has ever reported usage for this thread).
    """
    row = pg_fetchone(
        "SELECT compaction_summary AS summary, "
        "       compaction_first_kept_seq AS first_kept_seq, "
        "       untrusted_content_seen AS tainted, "
        "       last_context_tokens "
        "FROM assistant_conversations WHERE id = %s",
        (conv_id,),
    )
    return row


def set_compaction(conv_id: str, summary: str, first_kept_seq: int, tainted: bool) -> bool:
    """Persist a gist + boundary. Returns True iff this call actually advanced it.

    A compare-and-set, not a plain write: the WHERE clause refuses a boundary that
    does not move strictly FORWARD. Two turns racing on one conversation (a browser
    stream and a Telegram message arriving together) can therefore never rewind the
    boundary or pair a newer summary with an older one — at worst the loser's
    summary is discarded, which costs one wasted light-tier call and nothing else.

    ``tainted`` is OR-ed rather than assigned, because the flag records that
    untrusted content ONCE entered this thread. That never stops being true, and a
    later compaction of a clean span must not clear it. This is the BACKFILL path for
    the flag, not the primary one — see ``mark_untrusted_seen``.

    It also CLEARS ``last_context_tokens``. That reading describes a context this write
    has just made smaller, so leaving it would send the next turn down the fast path on a
    number that no longer describes anything — and it is the one moment a decrease in the
    meter is legitimate, which is what lets ``save_message`` otherwise keep the greater of
    the two. The next turn pays one row scan and records a fresh, accurate reading.

    ``updated_at`` is deliberately NOT bumped: it orders the conversation list as a
    record of user activity, and compaction is internal housekeeping that always runs
    inside a turn whose own message write bumps it anyway.
    """
    return pg_execute(
        "UPDATE assistant_conversations "
        "SET compaction_summary = %s, "
        "    compaction_first_kept_seq = %s, "
        "    last_context_tokens = NULL, "
        "    untrusted_content_seen = untrusted_content_seen OR %s "
        "WHERE id = %s "
        "  AND (compaction_first_kept_seq IS NULL OR compaction_first_kept_seq < %s)",
        (summary, first_kept_seq, bool(tainted), conv_id, first_kept_seq),
    ) > 0


def mark_untrusted_seen(conv_id: str) -> None:
    """Record, durably, that untrusted content has entered this conversation.

    Called at INGRESS — the moment an upload's fenced text is saved, or an external
    read's result is fenced — rather than later when compaction removes the rows
    carrying it. That ordering is the point: an assistant row is persisted with its
    ``tool_calls`` first and its ``tool_results`` merged afterwards, so a compaction
    pass reading between the two would see a row with no marker on it and record no
    taint, even though the row is about to hold a fenced email. Writing at ingress
    cannot lose that race, because the flag is set before the content is anywhere a
    compaction pass could miss it.
    """
    pg_execute(
        "UPDATE assistant_conversations SET untrusted_content_seen = TRUE WHERE id = %s",
        (conv_id,),
    )


def is_conversation_tainted(conv_id: str) -> bool:
    """True once untrusted content has ever entered this thread.

    The engine decides the power→normal write downgrade by scanning the ASSEMBLED
    context for untrusted fences. Compaction REMOVES rows, so without this flag the
    first compaction that aged out an uploaded file or a Gmail read would silently
    switch that mitigation off — and a later power-mode turn could auto-execute a
    write the model proposed from injected text.

    **Fails CLOSED.** An unreadable flag answers True, which costs a confirmation
    prompt; answering False on a database blip would cost the mitigation itself.
    """
    try:
        row = pg_fetchone(
            "SELECT untrusted_content_seen FROM assistant_conversations WHERE id = %s",
            (conv_id,),
        )
    except Exception:
        return True
    if row is None:
        return True
    return bool(row.get("untrusted_content_seen"))


# ── Messages ───────────────────────────────────────────────────────────────

def save_message(
    conversation_id: str,
    msg_id: str,
    role: str,
    content: str,
    tool_calls: list | None = None,
    model: str = "",
    context_tokens: int | None = None,
) -> None:
    """Insert one message, allocating ``seq`` atomically under the conversation lock.

    Raises if the conversation does not exist (fail-loud — never silently create).

    ``context_tokens`` is the cache-inclusive input count the model read for THIS
    iteration (see ``providers.windows.cache_inclusive_input_tokens``). It rides this
    call rather than a write of its own because the transaction already holds the
    conversation row and already updates it — so compaction's fullness meter costs
    zero extra round trips on the streaming hot path. ``None`` leaves the stored value
    alone, so a turn on a provider that reports no usage never blanks a good reading.

    The write takes the GREATER of the two, because between compactions a conversation
    only grows and two turns racing on it can finish out of order: a slow turn whose
    context was assembled before the other's rows existed would otherwise land LAST and
    replace a high reading with its own stale low one. The next turn would then take the
    fast path on that low number and skip compaction — and if the real context is already
    past the provider's limit, every turn fails, none of them records a corrective
    reading, and the thread stays stuck there. GREATEST alone would be wrong if nothing
    ever lowered the meter, since a post-compaction reading is legitimately smaller;
    ``set_compaction`` clears it for exactly that reason, which is the only moment a
    decrease is real.
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
            "UPDATE assistant_conversations "
            "SET updated_at = now(), "
            "    last_context_tokens = CASE WHEN %s IS NULL THEN last_context_tokens "
            "                               ELSE GREATEST(%s, COALESCE(last_context_tokens, 0)) END "
            "WHERE id = %s",
            (context_tokens, context_tokens, conversation_id),
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


def get_tool_result(conversation_id: str, tool_use_id: str, msg_id: str | None = None) -> dict | None:
    """Return the parsed stored result for a tool call (or None if not recorded).

    Lets ``/confirm`` report the canonical outcome when a call is already resolved,
    so the UI reflects the real state (approved / denied / still-executing) rather
    than assuming its own click won.
    """
    if msg_id:
        row = pg_fetchone(
            "SELECT tool_results FROM assistant_messages WHERE id = %s AND conversation_id = %s",
            (msg_id, conversation_id),
        )
        rows = [row] if row else []
    else:
        rows = pg_fetchall(
            "SELECT tool_results FROM assistant_messages "
            "WHERE conversation_id = %s AND tool_calls IS NOT NULL ORDER BY seq DESC",
            (conversation_id,),
        )
    for r in rows:
        for res in (r.get("tool_results") or []):
            if res.get("tool_use_id") == tool_use_id:
                content = res.get("content")
                if not isinstance(content, str):
                    return content
                try:
                    return json.loads(content)
                except (ValueError, TypeError):
                    return {"raw": content}
    return None


def list_pending_tool_uses(conversation_id: str, msg_id: str) -> list[str]:
    """Return the tool_use_ids on one message whose result is still pending approval.

    Additive helper for the Telegram integration (issue #7): a single assistant
    iteration can request several writes, each persisted as a ``pending_user_approval``
    placeholder on the same message. The Telegram confirm flow uses this to know when a
    batch is fully resolved (continue only once the list is empty) and to auto-deny any
    stragglers when a new user message arrives mid-confirmation. Read-only; no lock.
    """
    row = pg_fetchone(
        "SELECT tool_results FROM assistant_messages WHERE id = %s AND conversation_id = %s",
        (msg_id, conversation_id),
    )
    if not row:
        return []
    return [
        r.get("tool_use_id")
        for r in (row.get("tool_results") or [])
        if r.get("tool_use_id") and is_pending_result(r.get("content"))
    ]


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
    execute an attacker-chosen tool. ``content`` carries the pre-claim pending
    placeholder so the resolver can read anything the gate bound to it (#43).
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
            # "content" is the PRE-claim pending placeholder. It normally holds just
            # {"status": ...}, but the gate may have merged extra keys into it — the
            # Gmail connection binding (#43) — that the resolver needs to check
            # before executing.
            return {
                "msg_id": cand["id"],
                "tool": call.get("tool"),
                "args": call.get("args") or {},
                "content": existing.get("content"),
            }
    return None
