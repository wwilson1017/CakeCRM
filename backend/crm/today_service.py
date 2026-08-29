"""The dashboard Today panel (issue #130): one ranked list of what needs you today.

Pure SQL and pure Python — no AI provider, so the panel is identical on a keyless
install. The priority ladder is the whole feature, and it lives in exactly one place:
``build_today_items``, a pure function with no I/O and no clock read, so the ranking is
unit-testable without a database.

The ladder, as decided on the issue::

    1  starred tasks      the human already said "today"; starred+overdue stays rank 1
    2  hot + stale deals  RESERVED — ships with the #125 follow-up
    3  overdue tasks      most overdue first
    4  reminders due today
    5  tasks due today

Rank 2 is deliberately absent from the emitted values rather than renumbered away: the
follow-up inserts hot deals at 2 without touching any other rank, the frontend, or the
tests that pin this order.

**One clock.** "Today" is read once per request from ``gtd_common.today_local_str()`` —
the identical call ``gtd_service.today_view()`` makes — which is the mechanism behind the
issue's requirement that this panel and the GTD Today view agree on what "due today"
means. The reminder window is derived FROM that captured day rather than from a second
clock read, so a request that straddles local midnight cannot bound tasks to one day and
reminders to the next.

**Reminders are scope-invariant.** ``reminders`` has no owner column and is install-wide
by design (CLAUDE.md, Phase B), so the owner filter applies to the tasks read only and
today's reminders appear in every scope. That is the strongest form of the issue's own
rule that unowned work shows up in "my" view — someone has to catch it, and hiding it
from the one person looking is how it gets missed. They carry no "Unassigned" badge: that
label invites an action ("assign it") which cannot exist for a reminder.

**Uncapped, deliberately.** The endpoint returns the full ranked list and the client
shows five with a "+N more" expander, on ``today_view()``'s precedent — one local day of
open work is already a bounded set. It also makes the count honest by construction: the
visible rows and the "+N" number are two views of ONE array, so they cannot disagree the
way a rows-query and a separate COUNT can. An install with thousands of tasks due today
pays for them here exactly as the GTD Today page already does; the upgrade path is a
probe-row ``truncated`` flag (#56's idiom), not pagination.
"""

from datetime import date, datetime, timezone

from core.localtime import local_day_bounds
from core.postgres import pg_fetchall
from crm import gtd_common
from crm.service import LIVE_TASK_PREDICATE, NOT_DROPPED_TASK
from reminders import service as reminders_service

# Ladder positions. 2 is reserved for hot + stale deals (#125) and is never emitted.
RANK_STARRED = 1
RANK_HOT_DEAL = 2
RANK_OVERDUE = 3
RANK_REMINDER = 4
RANK_DUE_TODAY = 5

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _fetch_today_tasks(today: str, owner_id: int | None) -> list[dict]:
    """Open tasks that are starred or due on/before ``today``.

    Membership mirrors ``gtd_service.today_view()`` so the two surfaces select the same
    rows. ``completed = 0`` excludes 'done' (the #70 CHECK binds the two columns) and
    ``NOT_DROPPED_TASK`` excludes 'dropped' — together the same set as that view's
    ``status NOT IN ('done','dropped')``. Dates compare TEXT-on-TEXT, never ``::date``,
    which cannot cast-error on a malformed row.
    """
    conditions = [
        "completed = 0",
        NOT_DROPPED_TASK,
        LIVE_TASK_PREDICATE,
        "(star OR (due_date != '' AND due_date <= %s))",
    ]
    params: list = [today]
    if owner_id is not None:
        # Deliberately WIDER than list_tasks' strict `owner_id = %s`: the issue rules
        # that unassigned work appears in "my" view, because someone has to catch it.
        conditions.append("(owner_id = %s OR owner_id IS NULL)")
        params.append(owner_id)
    return pg_fetchall(
        "SELECT id, title, due_date, owner_id, star FROM tasks "
        f"WHERE {' AND '.join(conditions)} "
        # Ends on the unique id (#58). Uncapped readers carry the term too, so a later
        # LIMIT cannot silently reintroduce a non-deterministic window.
        "ORDER BY (due_date = '') ASC, due_date ASC, id ASC",
        params,
    )


def _reminder_instant(value) -> datetime | None:
    """A reminder's ``due_at`` as an aware instant, or None if it cannot be read.

    ``pg_fetchall`` runs every value through ``_postprocess_value``, which ISO-formats
    datetimes — so this receives a STRING from Postgres, not a datetime (the reminders
    service reparses for the same reason when it computes a recurrence). Both forms are
    accepted because callers in a transaction may hand over raw rows.

    Parsing matters for ordering, not just display: ISO strings sort lexicographically
    by their offset as well as their instant, so on a DST fall-back day two reminders an
    hour apart can compare backwards. A naive value is read as UTC rather than dropped.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def build_today_items(tasks: list[dict], reminders: list[dict], today: str) -> list[dict]:
    """The ladder. PURE — no I/O, no clock: ``today`` arrives as a parameter.

    Every task lands in exactly one bucket: starred wins outright (so a starred overdue
    task is rank 1, once), and the other two buckets require ``not star``, which makes
    them disjoint by construction. A non-starred task that is neither overdue nor due
    today is unreachable from ``_fetch_today_tasks``' membership and is skipped rather
    than forced into a bucket — a defensive drop keeps a caller that widens the query
    from silently mis-ranking rows.
    """
    starred: list[dict] = []
    overdue: list[dict] = []
    due_today: list[dict] = []

    for task in tasks:
        due = task.get("due_date") or ""
        item = {
            "kind": "task",
            "id": task["id"],
            "title": task.get("title") or "",
            "due_date": due,
            "owner_id": task.get("owner_id"),
        }
        if task.get("star"):
            bucket, rank, why = starred, RANK_STARRED, "starred"
        elif due and due < today:
            bucket, rank, why = overdue, RANK_OVERDUE, "overdue"
        elif due and due == today:
            bucket, rank, why = due_today, RANK_DUE_TODAY, "due_today"
        else:
            continue
        bucket.append({**item, "rank": rank, "why": why})

    # Re-sorted here rather than trusted from the query, so the ladder holds for any
    # caller and the pure function can be tested without reproducing the SQL's order.
    # Undated starred tasks sort last; every key ends on the unique id.
    starred.sort(key=lambda i: (i["due_date"] == "", i["due_date"], i["id"]))
    overdue.sort(key=lambda i: (i["due_date"], i["id"]))
    # Date-only granularity has no finer "earlier due", so id (creation order) decides.
    due_today.sort(key=lambda i: i["id"])

    reminder_items = [
        {
            "kind": "reminder",
            "id": row["id"],
            "rank": RANK_REMINDER,
            "why": "reminder",
            "title": row.get("message") or "",
            # Passed through as stored: an instant renders correctly in any browser
            # timezone, and re-formatting it here would only add a place to drift.
            "due_at": row.get("due_at"),
            "instant": _reminder_instant(row.get("due_at")),
        }
        for row in reminders
    ]
    # Unreadable timestamps sort last instead of being dropped — a reminder you cannot
    # order is still a reminder you need to see.
    reminder_items.sort(key=lambda i: (i["instant"] is None, i["instant"] or _EPOCH, str(i["id"])))
    for item in reminder_items:
        del item["instant"]

    return starred + overdue + reminder_items + due_today


def get_today(owner_id: int | None = None) -> dict:
    """The Today panel's payload: the full ranked list plus the day it was built for.

    ``next_refresh_at`` is the next local midnight as an absolute instant, so the client
    can arm its reload on the SERVER's day boundary. A browser timer cannot: the browser
    rolls over at ITS midnight, which on a default install (``TIMEZONE`` unset, so the
    server is on UTC) is hours away from the server's — leaving a tab open across the
    real boundary showing yesterday's list, the exact failure ``useLocalDay`` exists to
    prevent, merely relocated.
    """
    today = gtd_common.today_local_str()
    # Derived from the captured day, never a second clock read (see the module docstring).
    start, end = local_day_bounds(date.fromisoformat(today))
    tasks = _fetch_today_tasks(today, owner_id)
    reminders = reminders_service.list_pending_between(start, end)
    return {
        "date": today,
        "next_refresh_at": end.isoformat(),
        "scope": {"owner_id": owner_id},
        "items": build_today_items(tasks, reminders, today),
    }
