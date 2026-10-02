"""CRM — chatter / notes threads on deals and contacts (issue #15).

Ported from the manual-notes half of ``cake_os/backend/apps/crm/chatter_service.py``.
Notes are free-text commentary attached to a deal or contact, rendered alongside the
``activity_log`` timeline on the entity's detail view, and editable / soft-archivable.

All validation lives HERE (not just the router), so the HTTP routes and the assistant
tools share one source of truth and cannot drift: entity type/existence, a trimmed
non-empty bounded message, and bounded list windows. Invalid input raises ``ValueError``
(the router maps it to 400; the tools wrap it as ``{"error": ...}``).

Storage is polymorphic ``(entity_type, entity_id)`` with no FK, so orphan safety is
enforced structurally: ``add_note`` checks the target exists and inserts in ONE
transaction (``SELECT ... FOR UPDATE`` on the target row, per the check-then-write
rule in AGENTS.md), and ``service.py`` clears chatter in ``delete_contact`` /
``delete_company`` (both of which also lock the target ``FOR UPDATE``) and every
CRM-truncate path. A SERIAL id is
never reused except by ``TRUNCATE ... RESTART IDENTITY``, which also wipes
``crm_chatter`` — so a reused id can never inherit a deleted entity's notes.
"""

from datetime import datetime, timezone

from core.postgres import get_connection, pg_fetchall, pg_fetchone, row_to_dict
from crm import attachment_service, scoring_service, touch_count_service

# Companies joined in issue #22 (Casey parity): entity_type is free TEXT with no CHECK
# constraint, exactly so this is a zero-migration add. Widening the tuple widens the
# HTTP routes and the agent tools at once — they all validate through here.
CHATTER_ENTITY_TYPES = ("deal", "contact", "company")
_ENTITY_TABLE = {"deal": "deals", "contact": "contacts", "company": "companies"}
MAX_MESSAGE_LEN = 10_000
_MAX_LIMIT = 200
# A note naming more people than this is a broadcast, not a mention. Matches the
# frontend's MAX_MENTIONS in crm/chatterMentions.ts.
MAX_MENTIONS = 20


def _score_chatter_entity(entity_type: str | None, entity_id: int) -> None:
    """#18: a note add/archive/unarchive changes the entity's engagement + recency.
    Covers BOTH deals and contacts (touch-count is deal-only). Never raises."""
    if entity_type == "deal":
        scoring_service.score_on_event(deal_ids=(entity_id,))
    elif entity_type == "contact":
        scoring_service.score_on_event(contact_ids=(entity_id,))


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


def _check_type_and_id(entity_type: str, entity_id: int) -> None:
    """Reject a bad type or a non-positive id (no DB access — the target-row
    existence check happens transactionally inside add_note)."""
    _check_entity_type(entity_type)
    if not isinstance(entity_id, int) or entity_id <= 0:
        raise ValueError("entity_id must be a positive integer")


def _clean_mentions(mentions) -> list[int] | None:
    """Validate a submitted mention list (#235). ``None`` means "not supplied".

    Ids come from the composer's picker, never parsed out of the message text. A bool is
    rejected explicitly: it is an ``int`` subclass, so ``True`` would otherwise mention
    user 1."""
    if mentions is None:
        return None
    if not isinstance(mentions, (list, tuple)):
        raise ValueError("mentions must be a list of user ids")
    ids: list[int] = []
    for m in mentions:
        if type(m) is not int or m <= 0:
            raise ValueError("mentions must be positive integer user ids")
        if m not in ids:
            ids.append(m)
    if len(ids) > MAX_MENTIONS:
        raise ValueError(f"Too many mentions (max {MAX_MENTIONS})")
    return ids


def _apply_mentions(cur, note_id: int, wanted: list[int], actor_id: int | None) -> list[int]:
    """Make the note's stored mention set equal ``wanted``; return who to notify (#235).

    Runs on the caller's cursor, inside the transaction that holds the note row. A newly
    added id must be an ACTIVE seat — an unknown or deactivated id is dropped rather than
    failing the note, because the note is the record and a seat deactivated between the
    picker loading and the post must not cost the user what they typed. An id that is
    ALREADY stored is kept even if that seat has since been deactivated: re-saving an old
    note must not silently strip its highlight.

    Only rows this call actually INSERTED are returned (``ON CONFLICT DO NOTHING
    RETURNING``), minus the acting seat — nobody is told about their own mention, and an
    unrelated edit of a note re-notifies nobody.
    """
    cur.execute("SELECT user_id FROM crm_chatter_mentions WHERE note_id = %s", (note_id,))
    stored = {r[0] for r in cur.fetchall()}
    removed = [uid for uid in stored if uid not in wanted]
    if removed:
        cur.execute(
            "DELETE FROM crm_chatter_mentions WHERE note_id = %s AND user_id = ANY(%s)",
            (note_id, removed),
        )
    to_add = [uid for uid in wanted if uid not in stored]
    if not to_add:
        return []
    cur.execute(
        """INSERT INTO crm_chatter_mentions (note_id, user_id, display_name)
           SELECT %s, u.id, COALESCE(NULLIF(btrim(u.name), ''), u.email)
             FROM unnest(%s::int[]) WITH ORDINALITY AS w(id, ord)
             JOIN users u ON u.id = w.id
            WHERE u.is_active
            ORDER BY w.ord
           ON CONFLICT (note_id, user_id) DO NOTHING
           RETURNING user_id""",
        (note_id, to_add),
    )
    added = {r[0] for r in cur.fetchall()}
    # Keep the submitted order so notifications go out in the order people were picked.
    return [uid for uid in to_add if uid in added and uid != actor_id]


def list_mentions_for_notes(note_ids: list[int]) -> dict[int, list[dict]]:
    """``{note_id: [{user_id, name}]}`` for a page of notes, in ONE query (#235).

    Same batching shape as ``attachment_service.list_for_notes``. Ordered by the row id,
    i.e. the order the people were mentioned in."""
    if not note_ids:
        return {}
    rows = pg_fetchall(
        """SELECT note_id, user_id, display_name FROM crm_chatter_mentions
            WHERE note_id = ANY(%s) ORDER BY note_id, id""",
        (list(note_ids),),
    )
    out: dict[int, list[dict]] = {}
    for r in rows:
        out.setdefault(r["note_id"], []).append(
            {"user_id": r["user_id"], "name": r["display_name"]}
        )
    return out


def _read_mentions(cur, note_id: int) -> list[dict]:
    """One note's mentions, read on the writer's cursor so the returned note agrees with
    what that transaction stored."""
    cur.execute(
        "SELECT user_id, display_name FROM crm_chatter_mentions WHERE note_id = %s ORDER BY id",
        (note_id,),
    )
    return [{"user_id": r[0], "name": r[1]} for r in cur.fetchall()]


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


def add_note(entity_type: str, entity_id: int, message: str,
             author_id: int | None = None) -> dict:
    """Append a note to a deal or contact. Raises ValueError on invalid input or a
    non-existent target.

    ``author_id`` is who WROTE the note (issue #60), which is not who owns the record
    — per-rep activity credits the author. NULL means unattributed, which is the
    honest answer for a note the assistant wrote on someone's behalf: Phase A does
    not thread identity into tool executors, so it undercounts rather than guessing.

    The existence check and the INSERT run in one transaction with the target row
    locked FOR UPDATE (AGENTS.md: check-then-write spans reads and updates → one
    transaction). This serializes against delete_contact (which also locks the row
    first), so a note can never be inserted against a concurrently-deleted target.

    A note written here carries no mentions — see ``post_note``, which the REST route
    uses. The assistant's ``crm_add_note`` stays on this signature on purpose: no agent,
    attended or unattended, can make a mention notification fire.
    """
    note, _ = post_note(entity_type, entity_id, message, author_id=author_id)
    return note


def post_note(entity_type: str, entity_id: int, message: str,
              author_id: int | None = None,
              mentions: list[int] | None = None) -> tuple[dict, list[int]]:
    """``add_note`` plus @-mentions (#235). Returns ``(note, recipients)``.

    Mentions are stored in the note's own transaction; ``recipients`` are the seats the
    CALLER notifies post-commit (never the author). Nothing notifies from here — delivery
    is network I/O and must never hold the target row's lock or roll back a note."""
    text = _clean_message(message)
    _check_type_and_id(entity_type, entity_id)
    wanted = _clean_mentions(mentions) or []
    table = _ENTITY_TABLE[entity_type]
    now = _now()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT 1 FROM {table} WHERE id = %s FOR UPDATE", (entity_id,))
        if cur.fetchone() is None:
            raise ValueError(f"No {entity_type} with id {entity_id}")
        cur.execute(
            """INSERT INTO crm_chatter (entity_type, entity_id, message, created_at, author_id)
               VALUES (%s, %s, %s, %s, %s) RETURNING *""",
            (entity_type, entity_id, text, now, author_id),
        )
        # Hydrate from the INSERT's own row, inside the transaction — a post-commit
        # re-select could return None if a concurrent delete removes the row first.
        note = row_to_dict(cur, cur.fetchone())
        newly: list[int] = []
        note["mentions"] = []
        if wanted:  # the common, mention-free post costs no extra query
            newly = _apply_mentions(cur, note["id"], wanted, author_id)
            note["mentions"] = _read_mentions(cur, note["id"])
    # A note on a deal is fresh touch-count evidence. Schedule AFTER the transaction
    # commits (block exit) so the worker can actually read the note; schedule_recompute is
    # O(1) and never raises, so it can't break a note write. Contact notes don't trigger.
    if entity_type == "deal":
        touch_count_service.schedule_recompute(entity_id)
    _score_chatter_entity(entity_type, entity_id)  # #18: new note → engagement/recency
    return note, newly


def get_chatter(
    entity_type: str,
    entity_id: int,
    limit: int = 50,
    offset: int = 0,
    include_archived: bool = False,
) -> list[dict]:
    """Notes for one entity, newest first. Deterministic order (created_at, id).

    Each note carries its ``attachments`` (#57) — METADATA only, never bytes — fetched in
    ONE batched query for the whole page. Embedding here rather than adding a separate
    lookup endpoint means the REST route and the ``crm_get_chatter`` agent tool inherit
    attachments together and cannot drift. Note this widens what a background turn can
    see by exactly one field of user-typed text (filenames), on the same terms as #22's
    reads: the ceiling is still one ``notify_user``.
    """
    _check_entity_type(entity_type)
    limit = _bounded_limit(limit)
    offset = _bounded_offset(offset)
    archived_filter = "" if include_archived else " AND archived = 0"
    notes = pg_fetchall(
        f"""SELECT * FROM crm_chatter
            WHERE entity_type = %s AND entity_id = %s{archived_filter}
            ORDER BY created_at DESC, id DESC
            LIMIT %s OFFSET %s""",
        (entity_type, entity_id, limit, offset),
    )
    ids = [n["id"] for n in notes]
    attachments = attachment_service.list_for_notes(ids)
    mentions = list_mentions_for_notes(ids)
    for note in notes:
        note["attachments"] = attachments.get(note["id"], [])
        note["mentions"] = mentions.get(note["id"], [])
    return notes


def update_note(note_id: int, message: str) -> dict | None:
    """Edit a note's message; stamps updated_at. Returns None if the note is gone."""
    note, _ = edit_note(note_id, message)
    return note


def edit_note(note_id: int, message: str, mentions: list[int] | None = None,
              actor_id: int | None = None) -> tuple[dict | None, list[int]]:
    """``update_note`` plus @-mentions (#235). Returns ``(note or None, recipients)``.

    ``mentions`` is tri-state (#235): omitted (``None``) leaves the stored set alone; a
    list becomes the new set — people no longer in it lose their row, people newly in it
    gain one, and only THOSE are returned as recipients (minus ``actor_id``, the
    editor). The note row is locked ``FOR UPDATE`` for the whole edit, so two concurrent
    edits of one note apply their mention diffs one after the other, never interleaved —
    an added recipient is told exactly once.
    """
    text = _clean_message(message)
    wanted = _clean_mentions(mentions)
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM crm_chatter WHERE id = %s FOR UPDATE", (note_id,))
        if cur.fetchone() is None:
            return None, []
        cur.execute(
            "UPDATE crm_chatter SET message = %s, updated_at = %s WHERE id = %s RETURNING *",
            (text, _now(), note_id),
        )
        note = row_to_dict(cur, cur.fetchone())
        newly = _apply_mentions(cur, note_id, wanted, actor_id) if wanted is not None else []
        note["mentions"] = _read_mentions(cur, note_id)
    return note, newly


def archive_note(note_id: int) -> bool | None:
    """Soft-hide a note (no hard delete). Returns None if the note does not exist."""
    row = pg_fetchone(
        "UPDATE crm_chatter SET archived = 1 WHERE id = %s RETURNING id, entity_type, entity_id",
        (note_id,),
    )
    if not row:
        return None
    # Archiving removes a note from the touch-count evidence set and usually LOWERS the
    # watermark (it's often the newest note), so force_write lets the CAS repair the count.
    if row.get("entity_type") == "deal":
        touch_count_service.schedule_recompute(row["entity_id"], force_write=True)
    _score_chatter_entity(row.get("entity_type"), row.get("entity_id"))  # #18: active-note count changed
    return True


def unarchive_note(note_id: int) -> bool | None:
    """Restore an archived note. Returns None if the note does not exist."""
    row = pg_fetchone(
        "UPDATE crm_chatter SET archived = 0 WHERE id = %s RETURNING id, entity_type, entity_id",
        (note_id,),
    )
    if not row:
        return None
    # Restoring a note adds it back to the evidence set — force_write so the CAS repair
    # applies even though the watermark/count may not advance.
    if row.get("entity_type") == "deal":
        touch_count_service.schedule_recompute(row["entity_id"], force_write=True)
    _score_chatter_entity(row.get("entity_type"), row.get("entity_id"))  # #18: active-note count changed
    return True
