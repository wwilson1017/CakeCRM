"""Todo-GTD — leaf module: constants, errors, validation, recurrence math.

Imports nothing from the rest of the GTD code (no cycles) and never imports
FastAPI — the service layer raises the typed errors here and the router maps them
to HTTP status codes.

Ported from cake_os `apps/todo_gtd/common.py` (itself a port of chatty's
`core/todo/service.py` validation helpers), minus the per-user scoping and with the
hardcoded business timezone replaced by this repo's `TIMEZONE` convention.
"""

import calendar
import datetime
import re

from core.localtime import today_local

TODO_STATUSES = (
    "inbox",
    "next_action",
    "waiting_for",
    "delegated",
    "someday_maybe",
    "done",
    "dropped",
)
PROJECT_STATUSES = ("active", "someday", "completed", "dropped")

# Where a todo came from. `capture_web` is the ONLY value an unauthenticated caller
# can produce, which is what makes it useful: it marks rows whose text a stranger
# may have typed. Never client-supplied — every write site passes its own literal.
TODO_SOURCES = ("capture_web", "telegram", "agent", "ui")

# The two statuses that mean "this is finished, stop showing it as work".
FINISHED_STATUSES = ("done", "dropped")

MAX_TEXT_CHARS = 20_000
# Short-field caps enforced at the SERVICE layer, not just on the Pydantic models:
# bulk_update's `fields` dict and the agent tools never pass through those models,
# so the models' max_length alone does not bound what reaches the UPDATE.
MAX_SHORT_CHARS = 500
MAX_TAGS = 50
MAX_BULK_IDS = 500

# '' means no repeat. 'every:N' (N days) is validated by _EVERY_RE, not the tuple.
# The migration's CHECK constraint carries the same two-part rule.
REPEAT_OPTIONS = ("", "daily", "weekdays", "weekly", "monthly", "yearly")
_EVERY_RE = re.compile(r"^every:([1-9][0-9]{0,3})$")

TODO_FIELDS = frozenset(
    {"title", "notes", "project", "project_id", "context", "tags", "status", "star",
     "due_date", "repeat", "auto_star_on_due"}
)
PROJECT_FIELDS = frozenset({"name", "notes", "status"})

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Deterministic capture intercept for the Telegram channel: matches "/capture ..."
# (with the optional @BotName suffix Telegram appends in groups) and bare
# "capture ...". The \b keeps "captured ..." / "recapture ..." conversational.
_CAPTURE_RE = re.compile(r"^\s*(?:/capture(?:@\w+)?|capture)\b[\s:,\-]*", re.IGNORECASE)


class ValidationError(ValueError):
    """Bad input from a client or agent — maps to HTTP 400."""


class NotFoundError(Exception):
    """Row absent — maps to HTTP 404."""


def parse_capture(text: str) -> str | None:
    """Return the capture payload, '' when the command carries no payload, or None
    when `text` is not a capture command at all (the caller's normal path continues).
    """
    m = _CAPTURE_RE.match(text or "")
    if not m:
        return None
    return (text[m.end():] or "").strip()


def today_local_str() -> str:
    """Today's date as 'YYYY-MM-DD' in the configured timezone — the form due dates
    are stored and compared in."""
    return today_local().isoformat()


def validate_status(status: str) -> str:
    if status not in TODO_STATUSES:
        raise ValidationError(f"Invalid status '{status}'. Valid: {', '.join(TODO_STATUSES)}")
    return status


def validate_project_status(status: str) -> str:
    if status not in PROJECT_STATUSES:
        raise ValidationError(
            f"Invalid project status '{status}'. Valid: {', '.join(PROJECT_STATUSES)}"
        )
    return status


def validate_due(due_date) -> str:
    """Normalize a due date to 'YYYY-MM-DD', or '' for none.

    Returns '' rather than None because `todos.due_date` is TEXT NOT NULL DEFAULT ''
    (the existing CRM convention) — ISO strings still compare correctly, and '' is
    the repo's established "no due date".
    """
    if due_date in (None, ""):
        return ""
    if isinstance(due_date, datetime.date):
        return due_date.isoformat()
    due = str(due_date).strip()
    if not due:
        return ""
    if not _DATE_RE.match(due):
        raise ValidationError(f"due_date must be YYYY-MM-DD, got '{due_date}'")
    try:
        # Shape alone would let '2026-13-40' through to Postgres, where it surfaces
        # as a raw driver error (500) instead of a clean 400.
        datetime.date.fromisoformat(due)
    except ValueError:
        raise ValidationError(f"due_date is not a real calendar date: '{due_date}'")
    return due


def validate_repeat(repeat) -> str:
    r = str(repeat or "").strip().lower()
    if r == "none":
        r = ""
    if r in REPEAT_OPTIONS or _EVERY_RE.match(r):
        return r
    raise ValidationError(
        f"Invalid repeat '{repeat}'. Valid: daily, weekdays, weekly, monthly, yearly, "
        "every:N (N days), or empty for none"
    )


def validate_tags(tags) -> list[str]:
    if tags is None:
        return []
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise ValidationError("tags must be a list of strings")
    cleaned = [t.strip() for t in tags if t.strip()]
    if len(cleaned) > MAX_TAGS:
        raise ValidationError(f"too many tags (max {MAX_TAGS})")
    if any(len(t) > MAX_SHORT_CHARS for t in cleaned):
        raise ValidationError(f"tag too long (max {MAX_SHORT_CHARS} characters)")
    return cleaned


def validate_notes(notes) -> str:
    text = str(notes or "")
    if len(text) > MAX_TEXT_CHARS:
        raise ValidationError(f"notes too long (max {MAX_TEXT_CHARS} characters)")
    return text


def validate_short(value, field: str) -> str:
    text = str(value or "").strip()
    if len(text) > MAX_SHORT_CHARS:
        raise ValidationError(f"{field} too long (max {MAX_SHORT_CHARS} characters)")
    return text


def validate_title(title) -> str:
    if title is not None and not isinstance(title, str):
        raise ValidationError("title must be a string")
    text = (title or "").strip()
    if not text:
        raise ValidationError("title is required")
    if len(text) > MAX_TEXT_CHARS:
        raise ValidationError(f"title too long (max {MAX_TEXT_CHARS} characters)")
    return text


def _advance(repeat: str, base: datetime.date) -> datetime.date:
    """One repeat interval after `base`, clamping month-end/Feb-29 overflow."""
    if repeat == "daily":
        return base + datetime.timedelta(days=1)
    if repeat == "weekdays":
        nxt = base + datetime.timedelta(days=1)
        while nxt.weekday() >= 5:  # 5=Sat, 6=Sun
            nxt += datetime.timedelta(days=1)
        return nxt
    if repeat == "weekly":
        return base + datetime.timedelta(days=7)
    if repeat == "monthly":
        y, m = (base.year, base.month + 1) if base.month < 12 else (base.year + 1, 1)
        return base.replace(year=y, month=m, day=min(base.day, calendar.monthrange(y, m)[1]))
    if repeat == "yearly":
        y = base.year + 1
        return base.replace(year=y, day=min(base.day, calendar.monthrange(y, base.month)[1]))
    m = _EVERY_RE.match(repeat)
    if m:
        return base + datetime.timedelta(days=int(m.group(1)))
    raise ValidationError(f"Invalid repeat '{repeat}'")


def next_due(repeat: str, due_date, today: datetime.date | None = None) -> str:
    """Due date for the next occurrence of a repeating todo.

    `today` defaults to the configured-timezone date. A caller that must also
    compare the RESULT against today (the auto-star spawn) passes its own single
    read, so the two can never straddle midnight and disagree.

    Advances from the old due date, re-anchoring to today only when even the
    advanced date is already past — so completing late never spawns an
    already-overdue copy. A due-today result is not overdue, hence the strict `<`.

    # simplification: next-due derives from the previous occurrence with month-end
    # clamping, so a monthly Jan-31 repeat drifts to the 28th after February.
    # Storing an anchor day is the upgrade path if that matters.
    """
    today = today or today_local()
    # Callers normally pass the ISO string a row read produces, but a real date
    # object is an equally valid representation — accept it rather than depend on
    # a stringification invariant.
    if isinstance(due_date, datetime.date):
        base = due_date
    else:
        try:
            base = datetime.date.fromisoformat(due_date) if due_date else today
        except (ValueError, TypeError):
            base = today
    nxt = _advance(repeat, base)
    if nxt < today:
        nxt = _advance(repeat, today)
    return nxt.isoformat()
