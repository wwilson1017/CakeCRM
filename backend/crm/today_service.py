"""The dashboard Today panel (issue #130): one ranked list of what needs you today.

Pure SQL and pure Python — no AI provider, so the panel is identical on a keyless
install. The priority ladder is the whole feature, and it lives in exactly one place:
``build_today_items``, a pure function with no I/O and no clock read, so the ranking is
unit-testable without a database.

The ladder, as decided on the issue::

    1  starred tasks      the human already said "today"; starred+overdue stays rank 1
    2  hot + stale deals  a deal a human marked hot that has since gone quiet (#131)
    3  overdue tasks      most overdue first
    4  reminders due today
    5  tasks due today
    –  every other hot deal  no rank at all: reachable only through the expander

Rank 2 arrived exactly as #130 promised — an insertion, renumbering nothing. What it did
NOT need was a rank 6. Issue #131 rules that a hot deal which was touched recently must
appear "only in the +N more today expanded list", and a sixth rung cannot enforce that:
the collapsed card slices the first five ITEMS, so one overdue task beside one recently
touched hot deal would put both on screen. So those rows carry ``rank: None`` — not on
the ladder — and the client shows the first five RANKED items. A null rank is the payload
saying what it means, rather than the browser learning a second copy of the numbering.

**Hot is a human judgment, stale is SQL.** Nothing here asks a model anything: the
temperature comes from #125's ``deals.deal_temperature`` column, which only a person
writes, and "stale" is ``analytics_service``'s existing definition, imported rather than
re-typed — ``LAST_TOUCH_SQL`` under ``OPEN_PREDICATE_D`` and ``LIVE_PREDICATE_D``, tested
against ``DEFAULT_DEAL_STALE_DAYS``. The panel and the "Needs a touch" list therefore
cannot disagree about what a touch is. The stale test is evaluated **in SQL**, and that
placement is load-bearing rather than stylistic: recomputing it in Python from a floored
day count would answer differently from the interval comparison at the exact boundary
instant, which is two definitions of one word — the thing the imports exist to prevent.

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

**Uncapped, deliberately — and the honest bound is NOT "one day".** The endpoint returns
the full ranked list and the client shows five behind a "+N more" expander, because the
issue specifies that expander over the full list and because ``today_view()`` already
serves exactly these rows uncapped to the GTD home screen. Reminders really are bounded
by one local day, but starred and overdue tasks are a BACKLOG: they accumulate without
limit, so a CRM with a thousand neglected overdue tasks sends all thousand on every
dashboard load. That is parity with the GTD Today page rather than a new exposure, and it
buys a count that is honest by construction — the visible rows and the "+N" are two views
of ONE array, so they cannot disagree the way a rows-query and a separate COUNT can. The
upgrade path, when that stops paying, is a probe-row ``truncated`` flag (#56's idiom)
rather than pagination: the panel's whole job is to be the short list.
"""

from datetime import date, datetime, timezone

from core.localtime import local_day_bounds
from core.postgres import pg_fetchall
from crm import gtd_common
from crm.analytics_service import DEFAULT_DEAL_STALE_DAYS
from crm.service import (
    LAST_TOUCH_SQL,
    LIVE_PREDICATE_D,
    LIVE_TASK_PREDICATE,
    NOT_DROPPED_TASK,
    OPEN_PREDICATE_D,
)
from reminders import service as reminders_service

# Ladder positions. A hot deal that is NOT stale carries no rank at all (see the module
# docstring) — the expander is the only way to it, which is what issue #131 specifies.
RANK_STARRED = 1
RANK_HOT_DEAL = 2
RANK_OVERDUE = 3
RANK_REMINDER = 4
RANK_DUE_TODAY = 5

#: How many hot + stale deals may sit above the user's own commitments. The issue's
#: anti-flood cap: a deal is someone else's problem until you decide otherwise, and a
#: neglected pipeline must never bury the calls and promises you made yourself. Deals
#: past the cap are not dropped — they join the unranked tail, still most-idle first.
HOT_DEAL_SLOTS = 2

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


def _fetch_hot_deals(owner_id: int | None) -> list[dict]:
    """Live, open deals a human marked hot — the whole set, with their idle time.

    Membership is only the two things a person cannot argue with: the temperature they
    set, and the deal still being live and open. Whether a hot deal is STALE is projected
    as ``is_stale`` rather than filtered on, because the ladder needs both kinds — stale
    ones earn a slot above the user's commitments, the rest are reachable through the
    expander — and one read answers for both.

    ``is_stale`` is ``analytics_service.get_stale_deals``' own comparison, over the same
    imported ``LAST_TOUCH_SQL``: strictly older than ``DEFAULT_DEAL_STALE_DAYS``. It is
    computed HERE, in SQL, and not in Python from ``idle_seconds`` — the interval test and
    a floored-day test disagree at the exact boundary instant, and "stale" must mean one
    thing across the two panels that show it.

    ``idle_seconds`` is exact on purpose. The ladder promotes only ``HOT_DEAL_SLOTS`` deals,
    so ordering decides which ones a rep actually sees, and two deals idle 14d1h and 14d23h
    floor to the same whole day — the tiebreak would then be the id, which is not
    most-idle-first at all. The displayed day count is derived from this one number, so the
    order and the label can never tell different stories.

    Uncapped, like ``_fetch_today_tasks`` — the panel's whole payload is uncapped by design
    (see the module docstring), and hot is a temperature somebody has to click, so the set
    is bounded by human effort rather than by data volume. The ORDER BY still ends on the
    unique ``d.id`` (#58) so a later ``LIMIT`` cannot quietly reintroduce a
    non-deterministic window.
    """
    conditions = [
        "d.deal_temperature = 'hot'",
        OPEN_PREDICATE_D,
        LIVE_PREDICATE_D,
    ]
    # psycopg2 substitutes placeholders in TEXT order, and the stale test sits in the
    # SELECT list — ahead of the WHERE clause. So the threshold is bound FIRST; appending
    # it beside the owner filter would silently swap the two.
    params: list = [DEFAULT_DEAL_STALE_DAYS]
    if owner_id is not None:
        # The panel's widened owner rule, same as the tasks read: unassigned work appears
        # in "my" view because somebody has to catch it.
        conditions.append("(d.owner_id = %s OR d.owner_id IS NULL)")
        params.append(owner_id)
    return pg_fetchall(
        f"""SELECT d.id, d.title, d.value, d.owner_id,
                   EXTRACT(EPOCH FROM (now() - {LAST_TOUCH_SQL}))::float8 AS idle_seconds,
                   ({LAST_TOUCH_SQL} < now() - make_interval(days => %s)) AS is_stale
              FROM deals d
             WHERE {' AND '.join(conditions)}
             ORDER BY idle_seconds DESC, d.id ASC""",
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


def _rank_hot_deals(deals) -> tuple[list[dict], list[dict]]:
    """Hot deals, split into the promoted few and the unranked tail. PURE.

    One sort, one walk. Every hot deal is ordered most-idle first, then the first
    ``HOT_DEAL_SLOTS`` **stale** ones take rank 2 and everything else takes no rank. A
    stale deal is by definition idler than a fresh one, so the stale rows that overflow
    the cap sort above the recently-touched ones in the tail with no second key — the
    ordering rule stays "most idle first" everywhere it is applied.

    ``why`` describes the DEAL and ``rank`` decides the SLOT, so an overflowing stale row
    keeps saying it is going cold even while it sits in the tail. Overflow is demoted
    rather than dropped: the panel silently hiding the most neglected deals in the CRM
    would be a worse failure than showing them one click away.

    ``idle_seconds`` is the sort key and is deleted before returning — an implementation
    detail, not payload, the same call ``build_today_items`` makes for a reminder's parsed
    ``instant``. The displayed whole-day count is derived from it, so a row can never be
    ordered by one number and labelled with another. It is clamped at zero: a deal whose
    ``updated_at`` sits slightly in the future (clock skew on a restore, say) is "idle 0d",
    never "idle -1d".
    """
    items = []
    for row in deals:
        idle = float(row.get("idle_seconds") or 0)
        items.append({
            "kind": "deal",
            "id": row["id"],
            "title": row.get("title") or "",
            "value": row.get("value") or 0,
            "owner_id": row.get("owner_id"),
            "days_since_touch": max(0, int(idle // 86400)),
            "why": "hot_stale" if row.get("is_stale") else "hot",
            "idle_seconds": idle,
        })
    items.sort(key=lambda i: (-i["idle_seconds"], i["id"]))

    promoted: list[dict] = []
    tail: list[dict] = []
    for item in items:
        del item["idle_seconds"]
        if item["why"] == "hot_stale" and len(promoted) < HOT_DEAL_SLOTS:
            promoted.append({**item, "rank": RANK_HOT_DEAL})
        else:
            # No rank, deliberately: the collapsed card shows ranked rows only, so these
            # are reachable through "+N more today" and nowhere else.
            tail.append({**item, "rank": None})
    return promoted, tail


def build_today_items(
    tasks: list[dict], reminders: list[dict], today: str, deals: list[dict] | tuple = (),
) -> list[dict]:
    """The ladder. PURE — no I/O, no clock: ``today`` arrives as a parameter.

    Every task lands in exactly one bucket: starred wins outright (so a starred overdue
    task is rank 1, once), and the other two buckets require ``not star``, which makes
    them disjoint by construction. A non-starred task that is neither overdue nor due
    today is unreachable from ``_fetch_today_tasks``' membership and is skipped rather
    than forced into a bucket — a defensive drop keeps a caller that widens the query
    from silently mis-ranking rows.

    ``deals`` defaults to empty so the task-and-reminder ladder stays testable on its own
    terms. The default is safe only because something asserts the real caller passes them
    — ``test_get_today_feeds_the_fetched_deals_into_the_ladder`` — since a defaulted
    parameter is otherwise exactly how a source silently drops out of a merged list.
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

    hot_stale, hot_tail = _rank_hot_deals(deals)
    return starred + hot_stale + overdue + reminder_items + due_today + hot_tail


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
    deals = _fetch_hot_deals(owner_id)
    reminders = reminders_service.list_pending_between(start, end)
    return {
        "date": today,
        "next_refresh_at": end.isoformat(),
        "scope": {"owner_id": owner_id},
        "items": build_today_items(tasks, reminders, today, deals=deals),
    }
