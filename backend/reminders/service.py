"""Reminders — data service (Postgres).

Ported from Chatty's ``core/agents/reminders/service.py`` and translated to
``core.postgres`` helpers, single-tenant (agent columns dropped), with:
  * ``due_at`` as a real ``TIMESTAMPTZ`` instant (not a TEXT date);
  * an ATOMIC claim that creates the next recurring occurrence in the SAME
    transaction (so a crash during delivery can never terminate a series);
  * full CRUD (create/update/cancel/delete) — Chatty's REST was read-only, so
    update/delete are net-new here (issue #6 acceptance: "reminders CRUD from UI").

Recurrence math lives in ``reminders.recurrence`` (shared, no import cycle).
"""

import logging
import uuid
from datetime import datetime, timezone

from psycopg2.extras import Json

from core.postgres import get_connection, pg_execute, pg_fetchall, pg_fetchone
from reminders import recurrence

logger = logging.getLogger(__name__)


# ── helpers ────────────────────────────────────────────────────────────────

def _normalize_due_at(raw) -> datetime:
    """Parse a client/tool due_at into a timezone-aware UTC datetime.

    Accepts ISO 8601 with ``Z``, an offset, or naive (assumed UTC), or a
    datetime. PAST values are allowed (they fire on the next tick — this is what
    makes the feature demonstrable). Raises ValueError on anything unparseable.
    """
    if isinstance(raw, datetime):
        dt = raw
    else:
        if not raw or not str(raw).strip():
            raise ValueError("due_at is required")
        s = str(raw).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)  # raises ValueError on garbage
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _transform(row: dict | None) -> dict | None:
    """Add derived display fields to a reminder row."""
    if not row:
        return row
    rule = row.get("recurrence_rule")  # JSONB → dict | None (already parsed)
    row["is_recurring"] = rule is not None
    row["recurrence_description"] = recurrence.describe_recurrence(rule)
    return row


# ── create ─────────────────────────────────────────────────────────────────

def create_reminder(message: str, due_at, context: str | None = None,
                    recurrence_rule: dict | None = None, series_id: str | None = None) -> dict:
    """Create a reminder. Returns ``{ok, id, ...}`` or ``{error}``.

    A recurring reminder without an explicit ``series_id`` becomes the head of a
    new series (``series_id = id``).
    """
    message = (message or "").strip()
    if not message:
        return {"error": "message is required"}
    try:
        due = _normalize_due_at(due_at)
    except ValueError:
        return {"error": "due_at must be a valid ISO 8601 datetime"}
    rule_err = recurrence.validate_rule(recurrence_rule)
    if rule_err:
        return {"error": rule_err}

    rid = str(uuid.uuid4())
    if recurrence_rule is not None and not series_id:
        series_id = rid
    pg_execute(
        """INSERT INTO reminders (id, message, context, due_at, status, recurrence_rule, series_id)
           VALUES (%s, %s, %s, %s, 'pending', %s, %s)""",
        (rid, message, context or "", due, Json(recurrence_rule) if recurrence_rule else None, series_id),
    )
    return {
        "ok": True, "id": rid, "message": message, "due_at": due.isoformat(),
        "recurring": recurrence_rule is not None,
        "recurrence": recurrence.describe_recurrence(recurrence_rule), "series_id": series_id,
    }


# ── read ───────────────────────────────────────────────────────────────────

def get_reminder(reminder_id: str) -> dict | None:
    return _transform(pg_fetchone("SELECT * FROM reminders WHERE id = %s", (reminder_id,)))


def list_reminders(status: str | None = "pending", limit: int = 50) -> list[dict]:
    """List reminders. ``status=None``/``'all'`` returns every status.

    Pending sorts by soonest-due; other views sort newest-first.
    """
    limit = max(1, min(int(limit or 50), 200))
    if status and status != "all":
        rows = pg_fetchall(
            "SELECT * FROM reminders WHERE status = %s ORDER BY "
            "CASE WHEN status = 'pending' THEN due_at END ASC, created_at DESC LIMIT %s",
            (status, limit),
        )
    else:
        rows = pg_fetchall(
            "SELECT * FROM reminders ORDER BY "
            "CASE WHEN status = 'pending' THEN 0 ELSE 1 END, "
            "CASE WHEN status = 'pending' THEN due_at END ASC, created_at DESC LIMIT %s",
            (limit,),
        )
    return [_transform(r) for r in rows]


# ── update / cancel / delete (net-new CRUD) ────────────────────────────────

_UNSET = object()


def update_reminder(reminder_id: str, *, message=_UNSET, due_at=_UNSET,
                   context=_UNSET, recurrence_rule=_UNSET) -> dict:
    """Edit a PENDING reminder. Fired/cancelled reminders are immutable.

    Pass ``recurrence_rule=None`` to clear recurrence; omit an arg to leave it
    unchanged. Adding recurrence to a one-shot reminder initializes its series.
    """
    sets, params = [], []
    if message is not _UNSET:
        if not (message or "").strip():
            return {"error": "message cannot be empty"}
        sets.append("message = %s")
        params.append(message.strip())
    if context is not _UNSET:
        sets.append("context = %s")
        params.append(context or "")
    if due_at is not _UNSET:
        try:
            sets.append("due_at = %s")
            params.append(_normalize_due_at(due_at))
        except ValueError:
            return {"error": "due_at must be a valid ISO 8601 datetime"}
    if recurrence_rule is not _UNSET:
        rule_err = recurrence.validate_rule(recurrence_rule)
        if rule_err:
            return {"error": rule_err}
        sets.append("recurrence_rule = %s")
        params.append(Json(recurrence_rule) if recurrence_rule else None)
        # Adding recurrence to a formerly one-shot reminder must start a series.
        if recurrence_rule is not None:
            sets.append("series_id = COALESCE(series_id, id)")
    if not sets:
        return {"error": "no fields to update"}

    params.append(reminder_id)
    updated = pg_execute(
        f"UPDATE reminders SET {', '.join(sets)} WHERE id = %s AND status = 'pending'",
        tuple(params),
    )
    if updated == 0:
        existing = get_reminder(reminder_id)
        if existing is None:
            return {"error": "reminder not found"}
        return {"error": "only pending reminders can be edited"}
    return {"ok": True, "reminder": get_reminder(reminder_id)}


def cancel_reminder(reminder_id: str) -> dict:
    """Cancel a pending reminder. For a recurring reminder this stops the series
    (no further occurrence is generated once the pending head is cancelled)."""
    cancelled = pg_execute(
        "UPDATE reminders SET status = 'cancelled' WHERE id = %s AND status = 'pending'",
        (reminder_id,),
    )
    if cancelled == 0:
        existing = get_reminder(reminder_id)
        if existing is None:
            return {"error": "reminder not found"}
        return {"error": f"reminder already {existing['status']}"}
    was_recurring = bool((get_reminder(reminder_id) or {}).get("is_recurring"))
    return {"ok": True, "id": reminder_id,
            "note": "recurring series stopped" if was_recurring else "reminder cancelled"}


def delete_reminder(reminder_id: str) -> dict:
    """Hard-delete a fired/cancelled reminder. A pending reminder must be
    cancelled first (keeps the UI's cancel-vs-delete semantics unambiguous)."""
    deleted = pg_execute(
        "DELETE FROM reminders WHERE id = %s AND status <> 'pending'", (reminder_id,),
    )
    if deleted == 0:
        existing = get_reminder(reminder_id)
        if existing is None:
            return {"error": "reminder not found"}
        return {"error": "cancel a pending reminder before deleting it"}
    return {"ok": True, "id": reminder_id}


# ── firing (heartbeat-facing) ──────────────────────────────────────────────

def get_due_reminders(limit: int = 50) -> list[dict]:
    """Pending reminders whose due_at has passed, soonest first."""
    return [_transform(r) for r in pg_fetchall(
        "SELECT * FROM reminders WHERE status = 'pending' AND due_at <= now() "
        "ORDER BY due_at ASC LIMIT %s", (max(1, int(limit)),),
    )]


def claim_reminder(reminder: dict) -> bool:
    """Atomically claim a due reminder for firing, creating its next recurring
    occurrence in the SAME transaction.

    Returns True iff this call won the claim (rowcount 1). A concurrent tick /
    run-now loses cleanly (rowcount 0 → no double-fire). Marking the row
    ``fired`` with ``result='processing'`` BEFORE delivery makes delivery
    at-most-once (external push can't be exactly-once); creating the successor
    inside the claim tx means a crash during delivery leaves an inspectable
    ``processing`` row but never terminates a recurring series. The
    ``uq_reminders_series_due`` index makes the successor insert idempotent.
    """
    rid = reminder["id"]
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE reminders SET status = 'fired', fired_at = now(), result = 'processing' "
            "WHERE id = %s AND status = 'pending'",
            (rid,),
        )
        if cur.rowcount != 1:
            return False  # lost the claim
        rule = reminder.get("recurrence_rule")
        if rule:
            try:
                nxt = recurrence.compute_next_due(
                    datetime.fromisoformat(reminder["due_at"]), rule)
            except (ValueError, TypeError):
                nxt = None
            if nxt is not None:
                cur.execute(
                    """INSERT INTO reminders (id, message, context, due_at, status, recurrence_rule, series_id)
                       VALUES (%s, %s, %s, %s, 'pending', %s, %s)
                       ON CONFLICT (series_id, due_at) WHERE series_id IS NOT NULL DO NOTHING""",
                    (str(uuid.uuid4()), reminder["message"], reminder.get("context", ""),
                     nxt, Json(rule), reminder.get("series_id") or rid),
                )
    return True


def finish_reminder(reminder_id: str, result: str) -> None:
    """Record the terminal result text on a fired reminder."""
    pg_execute("UPDATE reminders SET result = %s WHERE id = %s", (result[:2000], reminder_id))


# Kept for API/tests parity with Chatty's naming; claim_reminder now owns the
# same-transaction successor creation, but a caller may still want it standalone.
def create_next_occurrence(fired_reminder: dict) -> dict | None:
    """Create the next pending occurrence for a recurring reminder (idempotent)."""
    rule = fired_reminder.get("recurrence_rule")
    if not rule:
        return None
    try:
        nxt = recurrence.compute_next_due(
            datetime.fromisoformat(fired_reminder["due_at"]), rule)
    except (ValueError, TypeError):
        return None
    if nxt is None:
        return None
    series_id = fired_reminder.get("series_id") or fired_reminder["id"]
    rid = str(uuid.uuid4())
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO reminders (id, message, context, due_at, status, recurrence_rule, series_id)
               VALUES (%s, %s, %s, %s, 'pending', %s, %s)
               ON CONFLICT (series_id, due_at) WHERE series_id IS NOT NULL DO NOTHING
               RETURNING id""",
            (rid, fired_reminder["message"], fired_reminder.get("context", ""),
             nxt, Json(rule), series_id),
        )
        inserted = cur.fetchone()
    if inserted is None:
        return None  # successor already existed
    return {"id": rid, "due_at": nxt.isoformat(), "series_id": series_id}
