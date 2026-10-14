"""Real-Postgres integration for the reminders engine (issue #283).

What a fake cursor cannot prove: that two overlapping ticks deliver a due row once
(``FOR UPDATE SKIP LOCKED`` plus the status flip), that a series spawns from its anchor
through the real ``ON CONFLICT`` arbiter, that the CHECKs and the unique backstop hold,
and that the scheduler's own entrypoint reaches the database.

Marked ``integration`` and excluded from the default no-DB run. Fixture data is fictional.
"""

import os
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg2
import pytest

pytestmark = pytest.mark.integration

ADMIN_DSN = os.getenv("TEST_ADMIN_DSN", "postgresql://cake:cake_dev@localhost:5432/cake")
CHI = ZoneInfo("America/Chicago")


@pytest.fixture(scope="module")
def pg_db():
    from core import postgres

    dbname = f"cakecrm_reminders_{os.getpid()}"
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
def sent(monkeypatch):
    """Pin the zone and record deliveries instead of sending them."""
    from reminders import service

    calls = []
    lock = threading.Lock()

    def record(title, message, *, user_id, link):
        with lock:
            calls.append((user_id, message))

    monkeypatch.setattr(service, "tz", lambda: CHI)
    monkeypatch.setattr(service, "deliver_notification", record)
    return calls


def _user(name) -> int:
    from core.postgres import pg_fetchone

    return pg_fetchone(
        "INSERT INTO users (email, name, password_hash) VALUES (%s, %s, 'x') RETURNING id",
        (f"{name.lower()}-{uuid.uuid4().hex[:8]}@example.test", name),
    )["id"]


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def _rows():
    from core.postgres import pg_fetchall

    return pg_fetchall("SELECT id, due_date, status, series_id, anchor_date, due_at "
                       "FROM reminders ORDER BY id")


def test_the_migration_created_the_new_table(pg_db):
    from core.postgres import pg_fetchall

    cols = {r["column_name"] for r in pg_fetchall(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'reminders'")}
    assert {"people", "due_at", "anchor_date", "series_id", "void_reason"} <= cols


def test_two_concurrent_ticks_deliver_a_due_reminder_once(sent):
    from reminders import service

    a, b = _user("Ada"), _user("Bo")
    created = _utc(2026, 10, 13, 12)
    r = service.create_reminder(note="Send the quote", due_date="2026-10-14", due_time="10:00",
                                people=[a, b], owner_id=a, created_by=a, now=created)
    now = _utc(2026, 10, 14, 15, 1)
    barrier = threading.Barrier(2)
    results = []

    def run():
        barrier.wait()
        results.append(service.fire_due(now=now))

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(x["fired"] for x in results) == [0, 1]
    assert sorted(u for u, _ in sent) == sorted([a, b])
    row = service.get_reminder(r["id"])
    assert row["status"] == "fired" and row["fired_at"]


def test_a_monthly_reminder_spawns_its_successor_from_the_anchor(sent):
    from reminders import service

    a = _user("Ada")
    root = service.create_reminder(note="Invoice run", due_date="2026-01-31", repeat="monthly",
                                   owner_id=a, created_by=a, now=_utc(2026, 1, 1))
    assert service.fire_due(now=_utc(2026, 1, 31, 15))["spawned"] == 1
    rows = _rows()
    assert [(r["due_date"], r["status"]) for r in rows] == [
        ("2026-01-31", "fired"), ("2026-02-28", "upcoming")]
    assert rows[1]["series_id"] == root["id"] and rows[1]["anchor_date"] == "2026-01-31"
    service.fire_due(now=_utc(2026, 2, 28, 15))
    rows = _rows()
    assert rows[-1]["due_date"] == "2026-03-31"  # no drift to the 28th
    assert rows[-1]["series_id"] == root["id"]
    assert sum(r["status"] == "upcoming" for r in rows) == 1


def test_a_late_repeating_reminder_fires_once_and_skips_to_the_next_future_time(sent):
    from reminders import service

    a = _user("Ada")
    service.create_reminder(note="Stand-up", due_date="2026-10-09", repeat="daily",
                            owner_id=a, created_by=a, now=_utc(2026, 10, 1))
    now = _utc(2026, 10, 14, 16)  # 11:00 Chicago, five days late
    out = service.fire_due(now=now)
    assert out == {"fired": 1, "spawned": 1, "attempted": 1}
    nxt = _rows()[-1]
    assert nxt["due_date"] == "2026-10-15"
    assert datetime.fromisoformat(nxt["due_at"]) > now
    assert service.fire_due(now=now)["fired"] == 0


def test_void_ends_the_series_and_the_check_refuses_a_reasonless_void(sent):
    from core.postgres import pg_execute
    from reminders import service

    a = _user("Ada")
    r = service.create_reminder(note="Weekly review", due_date="2026-10-14", repeat="weekly",
                                owner_id=a, created_by=a, now=_utc(2026, 10, 1))
    voided = service.void_reminder(r["id"], "No longer needed")
    assert voided["status"] == "voided" and voided["void_reason"] == "No longer needed"
    assert service.fire_due(now=_utc(2026, 10, 30))["fired"] == 0
    assert len(_rows()) == 1
    other = service.create_reminder(note="x", due_date="2026-10-20", owner_id=a, created_by=a,
                                    now=_utc(2026, 10, 1))
    with pytest.raises(psycopg2.errors.CheckViolation):
        pg_execute("UPDATE reminders SET status = 'voided' WHERE id = %s", (other["id"],))


def test_inactive_people_are_refused_at_create_and_skipped_at_delivery(sent):
    from core.postgres import pg_execute
    from crm.gtd_common import ValidationError
    from reminders import service

    a, b = _user("Ada"), _user("Bo")
    service.create_reminder(note="Renewal call", due_date="2026-10-14", people=[a, b],
                            owner_id=a, created_by=a, now=_utc(2026, 10, 1))
    pg_execute("UPDATE users SET is_active = FALSE WHERE id = %s", (b,))
    with pytest.raises(ValidationError, match="Not an active user"):
        service.create_reminder(note="y", due_date="2026-10-20", people=[b], owner_id=a,
                                created_by=a, now=_utc(2026, 10, 1))
    service.fire_due(now=_utc(2026, 10, 14, 15))
    assert [u for u, _ in sent] == [a]


def test_the_successor_unique_guard_holds(sent):
    from core.postgres import pg_execute
    from reminders import service

    a = _user("Ada")
    root = service.create_reminder(note="Daily", due_date="2026-10-14", repeat="daily",
                                   owner_id=a, created_by=a, now=_utc(2026, 10, 1))
    service.fire_due(now=_utc(2026, 10, 14, 15))
    with pytest.raises(psycopg2.errors.UniqueViolation):
        pg_execute(
            "INSERT INTO reminders (note, people, due_date, due_at, anchor_date, series_id) "
            "VALUES ('dup', %s, '2026-10-15', now(), '2026-10-14', %s)", ([a], root["id"]))


def test_remind_again_and_a_today_date_only_create(sent):
    from reminders import service

    a = _user("Ada")
    src = service.create_reminder(note="Follow up", due_date="2026-10-14", owner_id=a,
                                  created_by=a, now=_utc(2026, 10, 14, 20))  # 15:00 Chicago
    assert date.fromisoformat(src["due_date"]) == date(2026, 10, 14)
    again = service.remind_again(src["id"], due_date="2026-10-21", due_time="08:15",
                                 owner_id=a, created_by=a, now=_utc(2026, 10, 14, 20))
    assert again["note"] == "Follow up" and again["people"] == [a]
    assert again["due_time"] == "08:15" and again["repeat"] is None


def test_scheduler_entrypoint_fires_through_the_seam(sent, monkeypatch):
    from heartbeat import service as heartbeat
    from reminders import service

    a = _user("Ada")
    service.create_reminder(note="Ping", due_date="2026-10-14", owner_id=a, created_by=a,
                            now=_utc(2026, 10, 1))
    real = service.fire_due
    monkeypatch.setattr(service, "fire_due", lambda: real(now=_utc(2026, 10, 14, 15) + timedelta(0)))
    assert heartbeat.reminders_tick() == {"fired": 1, "spawned": 0, "attempted": 1}
