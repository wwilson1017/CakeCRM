"""Real-Postgres integration for the dashboard Today panel (issue #130).

These are the claims a Recorder cannot check, because it agrees with whatever SQL it is
handed: that the TEXT date comparison really selects the right tasks, that the
timestamptz window really brackets one local day of reminders, that the owner condition
really admits unassigned rows, and that the sweeps (`NOT_DROPPED_TASK`, the live-deal
predicate, `status = 'pending'`) really exclude what they claim to.

Marked ``integration`` and excluded from the default no-DB run. Fixture data is fresh and
fictional.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone

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
    from core.postgres import get_connection, pg_execute
    from crm import service

    with get_connection() as conn:
        service._truncate_all(conn.cursor(), include_definitions=True)
    pg_execute("DELETE FROM reminders")
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

    return pg_fetchone(
        "INSERT INTO tasks (title, due_date, star, owner_id, status, completed, deal_id) "
        "VALUES (%s, %s, %s, %s, %s, 0, %s) RETURNING id",
        (title, due, star, owner, status, deal_id),
    )["id"]


def _reminder(message, due_at, status="pending") -> str:
    from core.postgres import pg_execute

    rid = str(uuid.uuid4())
    pg_execute(
        "INSERT INTO reminders (id, message, due_at, status) VALUES (%s, %s, %s, %s)",
        (rid, message, due_at, status),
    )
    return rid


def _titles(payload) -> list[str]:
    return [i["title"] for i in payload["items"]]


def test_full_ladder_end_to_end(today):
    """Every rung, in order, against real SQL — the acceptance criterion's interleave."""
    from core.localtime import local_day_bounds
    from crm import today_service

    start, _ = local_day_bounds(datetime.strptime(today, "%Y-%m-%d").date())
    _task("Starred", star=True, due=_shift(today, -3))
    _task("Overdue older", due=_shift(today, -9))
    _task("Overdue newer", due=_shift(today, -1))
    _task("Due today", due=today)
    _reminder("Ring back", start + timedelta(hours=14))

    assert _titles(today_service.get_today()) == [
        "Starred", "Overdue older", "Overdue newer", "Ring back", "Due today",
    ]


def test_sweeps_exclude_what_they_claim_to(today):
    """Dropped todos, done todos, tasks on archived deals, and non-pending or
    wrong-day reminders must all stay out."""
    from core.localtime import local_day_bounds
    from crm import service, today_service

    start, end = local_day_bounds(datetime.strptime(today, "%Y-%m-%d").date())
    deal = service.create_deal(title="Archived deal", value=10)["id"]
    service.archive_deal(deal, archived=True)

    _task("Visible", due=today)
    _task("Dropped", due=today, status="dropped")
    _task("On an archived deal", due=today, deal_id=deal)
    _task("Due tomorrow", due=_shift(today, 1))
    _task("No due date", due="")
    _reminder("Fired already", start + timedelta(hours=2), status="fired")
    _reminder("Tomorrow", end + timedelta(hours=2))
    _reminder("Yesterday", start - timedelta(hours=2))

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


def test_reminders_survive_the_owner_scope(today):
    """They have no owner column — narrowing the scope must not hide today's reminders
    from the one person looking."""
    from core.localtime import local_day_bounds
    from crm import today_service

    start, _ = local_day_bounds(datetime.strptime(today, "%Y-%m-%d").date())
    _reminder("Install-wide ping", start + timedelta(hours=10))
    _task("A colleague's", due=today, owner=_user("Bo"))

    assert _titles(today_service.get_today(owner_id=_user("Ada"))) == ["Install-wide ping"]


def test_starred_overdue_task_appears_once_at_rank_one(today):
    from crm import today_service

    _task("Both starred and overdue", star=True, due=_shift(today, -5))
    items = today_service.get_today()["items"]
    assert [(i["title"], i["rank"]) for i in items] == [("Both starred and overdue", 1)]


def test_reminder_window_is_the_local_day_not_a_utc_one(today, monkeypatch):
    """The bug this endpoint's day math exists to prevent: on a UTC window, an evening
    reminder west of Greenwich falls on the wrong calendar day."""
    from core.localtime import local_day_bounds
    from crm import today_service

    monkeypatch.setenv("TIMEZONE", "America/Chicago")
    start, end = local_day_bounds(datetime.strptime(today, "%Y-%m-%d").date())
    # 23:30 local — inside the local day, but already TOMORROW in UTC.
    late = start + timedelta(hours=23, minutes=30)
    assert late.astimezone(timezone.utc).date() != late.date()
    _reminder("Late tonight", late)
    _reminder("Just after midnight", end + timedelta(minutes=1))

    assert _titles(today_service.get_today()) == ["Late tonight"]


def test_payload_reports_the_day_and_the_next_boundary(today):
    from core.localtime import local_day_bounds
    from crm import today_service

    _, end = local_day_bounds(datetime.strptime(today, "%Y-%m-%d").date())
    payload = today_service.get_today(owner_id=4)
    assert payload["date"] == today
    assert payload["next_refresh_at"] == end.isoformat()
    assert payload["scope"] == {"owner_id": 4}
