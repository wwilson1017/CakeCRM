"""Real-Postgres integration for the dashboard Today panel (issue #130).

These are the claims a Recorder cannot check, because it agrees with whatever SQL it is
handed: that the TEXT date comparison really selects the right tasks, that the owner
condition really admits unassigned rows, and that the sweeps (`NOT_DROPPED_TASK`, the
live-deal predicate) really exclude what they claim to.

Marked ``integration`` and excluded from the default no-DB run. Fixture data is fresh and
fictional.
"""

import os
import uuid
from datetime import datetime, timedelta

import psycopg2
import pytest

pytestmark = pytest.mark.integration

ADMIN_DSN = os.getenv("TEST_ADMIN_DSN", "postgresql://cake:cake_dev@localhost:5432/cake")


@pytest.fixture(scope="module")
def pg_db():
    from core import postgres

    dbname = f"cakecrm_today_{os.getpid()}"
    admin = psycopg2.connect(ADMIN_DSN)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
        cur.execute(f'CREATE DATABASE "{dbname}"')
    admin.close()

    dsn = ADMIN_DSN.rsplit("/", 1)[0] + f"/{dbname}"
    prev = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = dsn
    postgres.close_pool()
    postgres.init_pool()
    postgres.run_migrations()
    yield dsn

    postgres.close_pool()
    if prev is not None:
        os.environ["DATABASE_URL"] = prev
    else:
        os.environ.pop("DATABASE_URL", None)
    admin = psycopg2.connect(ADMIN_DSN)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (dbname,),
        )
        cur.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
    admin.close()


@pytest.fixture(autouse=True)
def _clean(pg_db):
    from core.postgres import get_connection
    from crm import service

    with get_connection() as conn:
        service._truncate_all(conn.cursor(), include_definitions=True)
    yield


@pytest.fixture
def today(monkeypatch):
    """Freeze the app's day so the fixtures can be written relative to it.

    Computed at run time, never a pasted literal: a hardcoded date passes for the rest of
    the day it was written on and then fails forever with no code change.
    """
    from crm import gtd_common

    day = gtd_common.today_local_str()
    monkeypatch.setattr(gtd_common, "today_local_str", lambda: day)
    return day


def _shift(day: str, days: int) -> str:
    return (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")


def _user(name) -> int:
    """A real row, because `tasks.owner_id` is a real FK to `users` (#60's design, and
    the reason a synthetic owner id cannot stand in here)."""
    from core.postgres import pg_fetchone

    return pg_fetchone(
        "INSERT INTO users (email, name, password_hash) VALUES (%s, %s, 'x') RETURNING id",
        (f"{name.lower()}-{uuid.uuid4().hex[:8]}@example.test", name),
    )["id"]


def _task(title, *, due="", star=False, owner=None, status="next_action", deal_id=None) -> int:
    from core.postgres import pg_fetchone

    # `completed` is DERIVED from `status`, never hand-written: a DB CHECK binds the two
    # (#70), so any other pairing is a constraint violation — the same reason
    # `seed_data.py` derives it rather than setting both.
    return pg_fetchone(
        "INSERT INTO tasks (title, due_date, star, owner_id, status, completed, deal_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (title, due, star, owner, status, 1 if status == "done" else 0, deal_id),
    )["id"]


def _deal(title, *, temperature=None, idle_days=0, owner=None, stage="lead",
          archived=False, value=0) -> int:
    """A deal at a chosen idle age.

    `create_deal` writes no `activity_log` row and `score_on_event` never bumps
    `updated_at` (#18), so backdating that column alone is enough to set the deal's last
    touch — which is what `LAST_TOUCH_SQL` reads when there is no activity and no note.
    Written as an interval against the DATABASE's `now()`, never a Python timestamp: the
    predicate under test compares against the server clock, so a client clock is a second
    clock and a source of flakes.
    """
    from core.postgres import pg_execute
    from crm import service

    deal_id = service.create_deal(title=title, value=value, stage=stage, owner_id=owner,
                                  deal_temperature=temperature)["id"]
    pg_execute("UPDATE deals SET updated_at = now() - make_interval(days => %s) WHERE id = %s",
               (idle_days, deal_id))
    if archived:
        service.archive_deal(deal_id, archived=True)
        # archive_deal writes the row, so the idle age has to be re-applied after it.
        pg_execute("UPDATE deals SET updated_at = now() - make_interval(days => %s) WHERE id = %s",
                   (idle_days, deal_id))
    return deal_id


def _activity(deal_id, *, days_ago=0, activity="call"):
    from core.postgres import pg_execute

    pg_execute(
        "INSERT INTO activity_log (deal_id, activity, created_at) "
        "VALUES (%s, %s, now() - make_interval(days => %s))",
        (deal_id, activity, days_ago),
    )


def _note(deal_id, *, days_ago=0, archived=False, message="note"):
    from core.postgres import pg_execute

    pg_execute(
        "INSERT INTO crm_chatter (entity_type, entity_id, message, created_at, archived) "
        "VALUES ('deal', %s, %s, now() - make_interval(days => %s), %s)",
        (deal_id, message, days_ago, 1 if archived else 0),
    )


def _titles(payload) -> list[str]:
    return [i["title"] for i in payload["items"]]


def _deal_rows(payload) -> list[tuple]:
    return [(i["title"], i["rank"], i["why"]) for i in payload["items"] if i["kind"] == "deal"]


def test_full_ladder_end_to_end(today):
    """Every rung, in order, against real SQL — the acceptance criterion's interleave."""
    from crm import today_service

    _task("Starred", star=True, due=_shift(today, -3))
    _task("Overdue older", due=_shift(today, -9))
    _task("Overdue newer", due=_shift(today, -1))
    _task("Due today", due=today)

    assert _titles(today_service.get_today()) == [
        "Starred", "Overdue older", "Overdue newer", "Due today",
    ]


def test_sweeps_exclude_what_they_claim_to(today):
    """Dropped todos, done todos, tasks on archived deals and undated tasks must all
    stay out."""
    from crm import service, today_service

    deal = service.create_deal(title="Archived deal", value=10)["id"]
    service.archive_deal(deal, archived=True)

    _task("Visible", due=today)
    _task("Dropped", due=today, status="dropped")
    _task("On an archived deal", due=today, deal_id=deal)
    _task("Due tomorrow", due=_shift(today, 1))
    _task("No due date", due="")

    assert _titles(today_service.get_today()) == ["Visible"]


def test_owner_scope_admits_unassigned_but_not_a_colleagues(today):
    """The panel's one deliberate divergence from list_tasks' strict owner filter."""
    from crm import today_service

    me, colleague = _user("Ada"), _user("Bo")
    _task("Mine", due=today, owner=me)
    _task("Unassigned", due=today, owner=None)
    _task("A colleague's", due=today, owner=colleague)

    assert _titles(today_service.get_today(owner_id=me)) == ["Mine", "Unassigned"]
    assert sorted(_titles(today_service.get_today())) == ["A colleague's", "Mine", "Unassigned"]


def test_starred_overdue_task_appears_once_at_rank_one(today):
    from crm import today_service

    _task("Both starred and overdue", star=True, due=_shift(today, -5))
    items = today_service.get_today()["items"]
    assert [(i["title"], i["rank"]) for i in items] == [("Both starred and overdue", 1)]


def test_task_membership_matches_the_gtd_today_view_exactly(today):
    """The issue's acceptance criterion, asserted as SET EQUALITY rather than as two
    predicate lists that merely look alike.

    `today_view()` and `_fetch_today_tasks()` are written independently — one says
    `status NOT IN ('done','dropped')`, the other `completed = 0 AND NOT_DROPPED_TASK`
    — so nothing but a test like this stops them drifting apart. The fixtures deliberately
    include a row on each side of every clause the two spell differently.
    """
    from crm import gtd_service, service, today_service

    deal = service.create_deal(title="Archived", value=1)["id"]
    service.archive_deal(deal, archived=True)

    for title, kwargs in [
        ("Starred undated", {"star": True}),
        ("Starred overdue", {"star": True, "due": _shift(today, -2)}),
        ("Overdue", {"due": _shift(today, -1)}),
        ("Due today", {"due": today}),
        ("Due tomorrow", {"due": _shift(today, 1)}),
        ("Undated", {}),
        ("Dropped", {"due": today, "status": "dropped"}),
        ("Done", {"due": today, "status": "done"}),
        ("On an archived deal", {"due": today, "deal_id": deal}),
    ]:
        _task(title, **kwargs)

    gtd_ids = {t["id"] for t in gtd_service.today_view()}
    panel_ids = {i["id"] for i in today_service.get_today()["items"] if i["kind"] == "task"}
    assert panel_ids == gtd_ids
    # Guard against the assertion passing because BOTH are empty or trivially small.
    assert len(panel_ids) == 4


def test_payload_reports_the_day_and_the_next_boundary(today):
    from core.localtime import local_day_bounds
    from crm import today_service

    _, end = local_day_bounds(datetime.strptime(today, "%Y-%m-%d").date())
    payload = today_service.get_today(owner_id=4)
    assert payload["date"] == today
    assert payload["next_refresh_at"] == end.isoformat()
    assert payload["scope"] == {"owner_id": 4}


# ── Hot deals (issue #131) ───────────────────────────────────────────────────

def test_only_a_deal_a_human_marked_hot_reaches_the_panel(today):
    """The issue's rule, and the whole zero-keys story: eligibility is one human-set
    column. Warm, cold and never-triaged are all simply absent — NULL is a real state,
    not missing data, so it earns no fallback."""
    from crm import today_service

    _deal("Hot", temperature="hot", idle_days=30)
    _deal("Warm", temperature="warm", idle_days=30)
    _deal("Cold", temperature="cold", idle_days=30)
    _deal("Never triaged", temperature=None, idle_days=30)

    assert _titles(today_service.get_today()) == ["Hot"]


def test_closed_and_archived_hot_deals_stay_out(today):
    """OPEN_PREDICATE_D and LIVE_PREDICATE_D, imported rather than re-typed — a won deal
    is finished and an archived one was put away, however hot it once was."""
    from crm import today_service

    _deal("Open", temperature="hot", idle_days=30)
    _deal("Won", temperature="hot", idle_days=30, stage="won")
    _deal("Lost", temperature="hot", idle_days=30, stage="lost")
    _deal("Archived", temperature="hot", idle_days=30, archived=True)

    assert _titles(today_service.get_today()) == ["Open"]


def test_hot_and_stale_takes_rank_two_most_idle_first_and_caps_at_two(today):
    """The acceptance criteria in one pass: the slot cap, the ordering, and the demotion
    of the overflow into the unranked tail rather than out of the payload."""
    from crm import today_service

    _deal("Idle 40d", temperature="hot", idle_days=40)
    _deal("Idle 60d", temperature="hot", idle_days=60)
    _deal("Idle 20d", temperature="hot", idle_days=20)

    assert _deal_rows(today_service.get_today()) == [
        ("Idle 60d", 2, "hot_stale"),
        ("Idle 40d", 2, "hot_stale"),
        ("Idle 20d", None, "hot_stale"),
    ]


def test_a_recently_touched_hot_deal_is_present_but_carries_no_rank(today):
    """"NOT in the Top 5, only in the expanded list" — which the payload says by giving
    the row no rung at all, and the client honours by filling its five visible slots from
    ranked rows only."""
    from crm import today_service

    _deal("Chased yesterday", temperature="hot", idle_days=1)

    assert _deal_rows(today_service.get_today()) == [("Chased yesterday", None, "hot")]


def test_the_stale_boundary_is_the_analytics_threshold(today):
    """Not a threshold of this panel's own: `analytics_service`'s, so the Today panel and
    the "Needs a touch" list can never call the same deal stale and fresh."""
    from crm import analytics_service, today_service

    days = analytics_service.DEFAULT_DEAL_STALE_DAYS
    _deal("Just past", temperature="hot", idle_days=days + 1)
    _deal("Just inside", temperature="hot", idle_days=days - 1)

    assert _deal_rows(today_service.get_today()) == [
        ("Just past", 2, "hot_stale"), ("Just inside", None, "hot")
    ]


def test_last_touch_reads_activity_and_live_notes_but_not_archived_ones(today):
    """LAST_TOUCH_SQL is GREATEST(updated_at, newest activity, newest un-archived note).
    All three legs matter here: a deal nobody edited is not stale if somebody logged a
    call or left a note on it — and archiving that note puts it back on the list."""
    from crm import today_service

    _deal("Edited long ago", temperature="hot", idle_days=40)
    called = _deal("Called yesterday", temperature="hot", idle_days=40)
    noted = _deal("Noted yesterday", temperature="hot", idle_days=40)
    archived_note = _deal("Note archived", temperature="hot", idle_days=40)

    _activity(called, days_ago=1)
    _note(noted, days_ago=1)
    _note(archived_note, days_ago=1, archived=True)

    rows = dict((t, (r, w)) for t, r, w in _deal_rows(today_service.get_today()))
    assert rows["Edited long ago"] == (2, "hot_stale")
    assert rows["Note archived"] == (2, "hot_stale")
    assert rows["Called yesterday"] == (None, "hot")
    assert rows["Noted yesterday"] == (None, "hot")
    assert set(rows) == {"Edited long ago", "Note archived", "Called yesterday", "Noted yesterday"}


def test_owner_scope_admits_unassigned_hot_deals_but_not_a_colleagues(today):
    """The panel's widened owner rule reaches deals too: unowned work shows up in "my"
    view because somebody has to catch it."""
    from crm import today_service

    me, colleague = _user("Cy"), _user("Di")
    _deal("Mine", temperature="hot", idle_days=30, owner=me)
    _deal("Unassigned", temperature="hot", idle_days=30, owner=None)
    _deal("Theirs", temperature="hot", idle_days=30, owner=colleague)

    assert sorted(_titles(today_service.get_today(owner_id=me))) == ["Mine", "Unassigned"]
    assert sorted(_titles(today_service.get_today())) == ["Mine", "Theirs", "Unassigned"]


def test_a_hot_deal_carries_the_evidence_the_row_renders(today):
    """The badge needs both numbers: idle time says it is slipping, value says whether
    chasing it is worth the afternoon."""
    from crm import today_service

    _deal("Acme renewal", temperature="hot", idle_days=21, value=30000)

    (item,) = today_service.get_today()["items"]
    assert item["value"] == 30000
    assert item["days_since_touch"] == 21
    assert "idle_seconds" not in item
