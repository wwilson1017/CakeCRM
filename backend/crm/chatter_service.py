"""CRM — chatter / notes threads on deals and contacts (issue #15).

Ported from the manual-notes half of ``cake_os/backend/apps/crm/chatter_service.py``.
Notes are free-text commentary attached to a deal or contact, rendered alongside the
``activity_log`` timeline on the entity's detail view, and editable / soft-archivable.

All validation lives HERE (not just the router), so the HTTP routes and the assistant
tools share one source of truth and cannot drift: entity type/existence, a trimmed
non-empty bounded message, and bounded list windows. Invalid input raises ``ValueError``
(the router maps it to 400; the tools wrap it as ``{"error": ...}``).

Storage is polymorphic ``(entity_type, entity_id)`` with no FK, so orphan safety is
enforced in two places: this module refuses to write a note against a non-existent
target, and ``service.py`` clears chatter in ``delete_contact`` and every CRM-truncate
path — a reused SERIAL id can never inherit a deleted entity's notes.
"""

from datetime import datetime, timezone

from core.postgres import pg_fetchall, pg_fetchone

CHATTER_ENTITY_TYPES = ("deal", "contact")
_ENTITY_TABLE = {"deal": "deals", "contact": "contacts"}
MAX_MESSAGE_LEN = 10_000
_MAX_LIMIT = 200


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_message(message: str) -> str:
    text = (message or "").strip()
    if not text:
        raise ValueError("Message is required")
    if len(text) > MAX_MESSAGE_LEN:
        raise ValueError(f"Message too long (max {MAX_MESSAGE_LEN} characters)")
    return text


def _check_entity_type(entity_type: str) -> None:
    if entity_type not in CHATTER_ENTITY_TYPES:
        allowed = ", ".join(CHATTER_ENTITY_TYPES)
        raise ValueError(f"Invalid entity_type: {entity_type!r}. Must be one of: {allowed}.")


def _validate_target(entity_type: str, entity_id: int) -> None:
    """Reject a bad type, a non-positive id, or a target row that does not exist."""
    _check_entity_type(entity_type)
    if not isinstance(entity_id, int) or entity_id <= 0:
        raise ValueError("entity_id must be a positive integer")
    table = _ENTITY_TABLE[entity_type]
    if pg_fetchone(f"SELECT 1 FROM {table} WHERE id = %s", (entity_id,)) is None:
        raise ValueError(f"No {entity_type} with id {entity_id}")


def _bounded_limit(limit: int) -> int:
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return 50
    return max(1, min(limit, _MAX_LIMIT))


def _bounded_offset(offset: int) -> int:
    try:
        offset = int(offset)
    except (TypeError, ValueError):
        return 0
    return max(0, offset)


def log_note(entity_type: str, entity_id: int, message: str) -> dict:
    """Append a note to a deal or contact. Raises ValueError on invalid input."""
    text = _clean_message(message)
    _validate_target(entity_type, entity_id)
    return pg_fetchone(
        """INSERT INTO crm_chatter (entity_type, entity_id, message, created_at)
           VALUES (%s, %s, %s, %s) RETURNING *""",
        (entity_type, entity_id, text, _now()),
    )


def get_chatter(
    entity_type: str,
    entity_id: int,
    limit: int = 50,
    offset: int = 0,
    include_archived: bool = False,
) -> list[dict]:
    """Notes for one entity, newest first. Deterministic order (created_at, id)."""
    _check_entity_type(entity_type)
    limit = _bounded_limit(limit)
    offset = _bounded_offset(offset)
    archived_filter = "" if include_archived else " AND archived = 0"
    return pg_fetchall(
        f"""SELECT * FROM crm_chatter
            WHERE entity_type = %s AND entity_id = %s{archived_filter}
            ORDER BY created_at DESC, id DESC
            LIMIT %s OFFSET %s""",
        (entity_type, entity_id, limit, offset),
    )


def update_note(note_id: int, message: str) -> dict | None:
    """Edit a note's message; stamps updated_at. Returns None if the note is gone."""
    text = _clean_message(message)
    return pg_fetchone(
        "UPDATE crm_chatter SET message = %s, updated_at = %s WHERE id = %s RETURNING *",
        (text, _now(), note_id),
    )


def archive_note(note_id: int) -> bool | None:
    """Soft-hide a note (no hard delete). Returns None if the note does not exist."""
    row = pg_fetchone(
        "UPDATE crm_chatter SET archived = 1 WHERE id = %s RETURNING id", (note_id,)
    )
    return True if row else None


def unarchive_note(note_id: int) -> bool | None:
    """Restore an archived note. Returns None if the note does not exist."""
    row = pg_fetchone(
        "UPDATE crm_chatter SET archived = 0 WHERE id = %s RETURNING id", (note_id,)
    )
    return True if row else None


def delete_chatter_for(cur, entity_type: str, entity_id: int) -> None:
    """Cascade helper: drop an entity's notes inside a caller-supplied transaction."""
    cur.execute(
        "DELETE FROM crm_chatter WHERE entity_type = %s AND entity_id = %s",
        (entity_type, entity_id),
    )
