"""Reminders (issue #283): one-time and repeating reminders as data.

A reminder is a note delivered verbatim to named CRM users at a date (and optional
time), with no model involved when it fires. The REST router, the Baker tools and the
heartbeat's ``reminders_tick`` all go through this module.

**Firing** (``fire_due``) ports cake_os's claim-before-send (#3710): one transaction
locks the due rows (``FOR UPDATE SKIP LOCKED``), marks them fired and inserts each
repeating row's successor; only after that commits does anything get delivered. Two
overlapping ticks therefore never both deliver a row. It is an AT-MOST-ONCE delivery
attempt: a crash between the commit and the send loses that one occurrence, chosen
over a duplicate.

**Delivery** is ``notifications.delivery.deliver_notification`` per person — the bell,
web push and Telegram when that seat linked it. Never email (AGENTS.md hard line).

**Access**: ``owner_id`` is not access control (AGENTS.md) — any member may read, edit
or void any reminder, as with todos. Only an UPCOMING row can change; fired and voided
rows are history, and nothing is ever deleted.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from core.localtime import tz
from core.postgres import get_connection, pg_fetchall, pg_fetchone, row_to_dict
from crm.gtd_common import NotFoundError, ValidationError, validate_due
from crm.service import _check_assembly_cursor
from notifications.delivery import deliver_notification
from reminders import recurrence
from users import service as users_service

logger = logging.getLogger(__name__)

MAX_NOTE_CHARS = 2000
MAX_VOID_REASON_CHARS = 500
MAX_PEOPLE = 50
FIRE_BATCH = 50  # simplification: a larger backlog drains over the following ticks
LINK = "/crm/reminders"
STATUSES = ("upcoming", "fired", "voided")

# TIME is projected as text: core.postgres only ISO-formats date/datetime, and a raw
# datetime.time would break json.dumps in the tool layer.
_COLS = ("id, owner_id, created_by, note, people, due_date, "
         "to_char(due_time, 'HH24:MI') AS due_time, due_at, repeat, anchor_date, series_id, "
         "status, void_reason, fired_at, voided_at, created_at, updated_at")

_EDITABLE = frozenset({"note", "due_date", "due_time", "people", "repeat"})


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def _clean_note(note) -> str:
    note = str(note or "").strip()
    if not note:
        raise ValidationError("A reminder needs a note")
    if len(note) > MAX_NOTE_CHARS:
        raise ValidationError(f"The note is longer than {MAX_NOTE_CHARS} characters")
    return note


def _active_ids(ids) -> set[int]:
    if not ids:
        return set()
    rows = pg_fetchall("SELECT id FROM users WHERE id = ANY(%s) AND is_active", (list(ids),))
    return {int(r["id"]) for r in rows}


def _resolve_people(people, owner_id: int | None) -> list[int]:
    """The ids to notify: deduplicated, all active users. Empty means the owner."""
    if not people:
        if owner_id is None:
            raise ValidationError("A reminder needs at least one person to notify")
        people = [owner_id]
    try:
        ids = sorted({int(p) for p in people if not isinstance(p, bool)})
    except (TypeError, ValueError):
        raise ValidationError("people must be a list of user ids") from None
    if not ids:
        raise ValidationError("people must be a list of user ids")
    if len(ids) > MAX_PEOPLE:
        raise ValidationError(f"A reminder can notify at most {MAX_PEOPLE} people")
    missing = sorted(set(ids) - _active_ids(ids))
    if missing:
        raise ValidationError(f"Not an active user: {', '.join(map(str, missing))}")
    return ids


def _when(due_date, due_time, *, now: datetime, zone) -> tuple[date, object, datetime]:
    """Validate a date + optional time and return (date, time | None, utc instant).

    A date before today is refused, and so is a TIMED instant already past. A date-only
    reminder for today is accepted at any hour and simply fires on the next tick."""
    day = validate_due(due_date)
    if not day:
        raise ValidationError("due_date is required")
    d = date.fromisoformat(day)
    t = recurrence.parse_time(due_time)
    at = recurrence.due_instant(d, t, zone)
    if d < now.astimezone(zone).date():
        raise ValidationError("That date has already passed. Pick today or a later date.")
    if t is not None and at <= now:
        raise ValidationError("That time has already passed. Pick a later time.")
    return d, t, at


def create_reminder(*, note, due_date, due_time=None, people=None, repeat=None,
                    owner_id: int | None, created_by: int | None,
                    now: datetime | None = None) -> dict:
    zone = tz()
    note = _clean_note(note)
    repeat = recurrence.validate_repeat(repeat)
    d, t, at = _when(due_date, due_time, now=_now(now), zone=zone)
    ids = _resolve_people(people, owner_id)
    return pg_fetchone(
        f"""INSERT INTO reminders (owner_id, created_by, note, people, due_date, due_time,
                                    due_at, repeat, anchor_date)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING {_COLS}""",
        (owner_id, created_by, note, ids, d, t, at, repeat, d),
    )


def get_reminder(reminder_id: int) -> dict | None:
    return pg_fetchone(f"SELECT {_COLS} FROM reminders WHERE id = %s", (reminder_id,))


def list_reminders(*, mine_user_id: int | None = None, status: str | None = None,
                   due_from=None, due_to=None, limit: int = 200, sort: str | None = None,
                   after_id: int | None = None) -> list[dict]:
    """Reminders, filtered. ``mine_user_id`` keeps the ones that seat owns or receives.

    ``sort='id'`` is the keyset order the collection sweep pages with (``after_id``)."""
    try:
        _check_assembly_cursor(after_id, sort or "")
    except ValueError as e:
        raise ValidationError(str(e)) from None
    where, params = [], []
    if mine_user_id is not None:
        where.append("(owner_id = %s OR %s = ANY(people))")
        params += [mine_user_id, mine_user_id]
    if status:
        if status not in STATUSES:
            raise ValidationError(f"status must be one of {', '.join(STATUSES)}")
        where.append("status = %s")
        params.append(status)
    if due_from:
        where.append("due_date >= %s")
        params.append(validate_due(due_from, "due_from"))
    if due_to:
        where.append("due_date <= %s")
        params.append(validate_due(due_to, "due_to"))
    if after_id is not None:
        where.append("id > %s")
        params.append(after_id)
    sql = f"SELECT {_COLS} FROM reminders"
    if where:
        sql += " WHERE " + " AND ".join(where)
    params.append(max(1, min(int(limit or 200), 1000)))
    if sort == "id":
        return pg_fetchall(sql + " ORDER BY id ASC LIMIT %s", params)
    return pg_fetchall(
        sql + " ORDER BY (status <> 'upcoming') ASC, due_at ASC, id ASC LIMIT %s", params)


def update_reminder(reminder_id: int, fields: dict, *, now: datetime | None = None) -> dict:
    """Change an upcoming reminder. The schedule is re-validated only when the patch
    touches it, so a note or people edit on a row the tick has not reached yet works."""
    unknown = set(fields) - _EDITABLE
    if unknown:
        raise ValidationError(f"Cannot change: {', '.join(sorted(unknown))}")
    zone = tz()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT {_COLS} FROM reminders WHERE id = %s FOR UPDATE", (reminder_id,))
        raw = cur.fetchone()
        if raw is None:
            raise NotFoundError(f"Reminder {reminder_id} not found")
        row = row_to_dict(cur, raw)
        if row["status"] != "upcoming":
            raise ValidationError("Only an upcoming reminder can be changed")
        sets, params = [], []
        if "note" in fields:
            sets.append("note = %s")
            params.append(_clean_note(fields["note"]))
        if "people" in fields:
            sets.append("people = %s")
            params.append(_resolve_people(fields["people"], row["owner_id"]))
        if "repeat" in fields:
            sets.append("repeat = %s")
            params.append(recurrence.validate_repeat(fields["repeat"]))
        if "due_date" in fields or "due_time" in fields:
            d, t, at = _when(fields.get("due_date", row["due_date"]),
                             fields.get("due_time", row["due_time"]), now=_now(now), zone=zone)
            # A new date re-anchors the series; the series id is kept.
            sets += ["due_date = %s", "due_time = %s", "due_at = %s", "anchor_date = %s"]
            params += [d, t, at, d]
        if not sets:
            return row
        cur.execute(
            f"UPDATE reminders SET {', '.join(sets)}, updated_at = now() "
            f"WHERE id = %s RETURNING {_COLS}",
            (*params, reminder_id),
        )
        return row_to_dict(cur, cur.fetchone())


def void_reminder(reminder_id: int, reason) -> dict:
    """Void an upcoming reminder with a reason. Voiding a repeating one ends the series:
    its successor only exists once it fires."""
    reason = str(reason or "").strip()
    if not reason:
        raise ValidationError("Voiding a reminder needs a reason")
    if len(reason) > MAX_VOID_REASON_CHARS:
        raise ValidationError(f"The reason is longer than {MAX_VOID_REASON_CHARS} characters")
    row = pg_fetchone(
        f"""UPDATE reminders SET status = 'voided', void_reason = %s, voided_at = now(),
                                 updated_at = now()
            WHERE id = %s AND status = 'upcoming' RETURNING {_COLS}""",
        (reason, reminder_id),
    )
    if row is None:
        if get_reminder(reminder_id) is None:
            raise NotFoundError(f"Reminder {reminder_id} not found")
        raise ValidationError("Only an upcoming reminder can be voided")
    return row


def remind_again(reminder_id: int, *, due_date, due_time=None, owner_id: int | None,
                 created_by: int | None, now: datetime | None = None) -> dict:
    """A new one-time reminder copying another's note and people (any status).

    People who have since been deactivated are dropped; if nobody is left, the caller."""
    src = get_reminder(reminder_id)
    if src is None:
        raise NotFoundError(f"Reminder {reminder_id} not found")
    active = _active_ids(src["people"])
    people = [p for p in src["people"] if p in active] or None
    return create_reminder(note=src["note"], due_date=due_date, due_time=due_time,
                           people=people, repeat=None, owner_id=owner_id,
                           created_by=created_by, now=now)


def fire_due(now: datetime | None = None) -> dict:
    """The heartbeat's pass: claim every due reminder, spawn successors, then deliver."""
    zone = tz()
    now = _now(now)
    spawned = 0
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            f"SELECT {_COLS} FROM reminders WHERE status = 'upcoming' AND due_at <= %s "
            "ORDER BY due_at ASC, id ASC LIMIT %s FOR UPDATE SKIP LOCKED",
            (now, FIRE_BATCH),
        )
        rows = [row_to_dict(cur, r) for r in cur.fetchall()]
        for row in rows:
            cur.execute(
                "UPDATE reminders SET status = 'fired', fired_at = %s, updated_at = now() "
                "WHERE id = %s",
                (now, row["id"]),
            )
            if row["repeat"]:
                due = datetime.fromisoformat(row["due_at"])
                nd, nat = recurrence.next_due(
                    row["repeat"], date.fromisoformat(row["anchor_date"]),
                    recurrence.parse_time(row["due_time"]), after=max(due, now), zone=zone)
                cur.execute(
                    "INSERT INTO reminders (owner_id, created_by, note, people, due_date, "
                    "due_time, due_at, repeat, anchor_date, series_id) "
                    "SELECT owner_id, created_by, note, people, %s, due_time, %s, repeat, "
                    "anchor_date, COALESCE(series_id, id) FROM reminders WHERE id = %s "
                    "ON CONFLICT (series_id, due_date) WHERE series_id IS NOT NULL DO NOTHING",
                    (nd, nat, row["id"]),
                )
                spawned += cur.rowcount
    # Committed: the claim is durable before anything is sent.
    attempted = _deliver(rows) if rows else 0
    return {"fired": len(rows), "spawned": spawned, "attempted": attempted}


def _deliver(rows: list[dict]) -> int:
    """One notification per active person per row. Never raises."""
    attempted = 0
    try:
        active = _active_ids({p for r in rows for p in r["people"]})
        names = users_service.users_by_id()
    except Exception:
        logger.warning("reminder delivery could not read users", exc_info=True)
        return 0
    for row in rows:
        try:
            setter = users_service.display_name(names.get(row["owner_id"]))
            for uid in row["people"]:
                if uid not in active:
                    continue
                message = row["note"]
                if uid != row["owner_id"] and row["owner_id"] is not None:
                    message = f"{message}\n— set by {setter}"
                deliver_notification("Reminder", message, user_id=uid, link=LINK)
                attempted += 1
        except Exception:
            logger.warning("reminder %s delivery failed", row.get("id"), exc_info=True)
    return attempted
