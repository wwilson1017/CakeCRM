"""Real-Postgres integration for issue #6 — proves what mocks can't: the migration
applies, the DB constraints hold (VAPID enc CHECK, the recurring-successor unique
index, the active-alert partial unique index), the atomic reminder claim actually
prevents a double-fire under concurrency, and VAPID keys persist once.

Marked ``integration`` and excluded from the default no-DB run. Admin DSN via
TEST_ADMIN_DSN (defaults to the local dev container).
"""

import os
import threading

import psycopg2
import pytest

pytestmark = pytest.mark.integration

ADMIN_DSN = os.getenv("TEST_ADMIN_DSN", "postgresql://cake:cake_dev@localhost:5432/cake")


@pytest.fixture(scope="module")
def pg_db():
    from core import postgres

    dbname = f"cakecrm_it6_{os.getpid()}"
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
            "WHERE datname = %s AND pid <> pg_backend_pid()", (dbname,))
        cur.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
    admin.close()


@pytest.fixture(autouse=True)
def _clean(pg_db):
    from core.postgres import pg_execute
    pg_execute("TRUNCATE reminders, notifications, push_subscriptions, alerts, vapid_keys")
    pg_execute("UPDATE heartbeat_state SET last_turn_at = NULL, consecutive_errors = 0, "
               "last_failure_alert_at = NULL WHERE id = 1")
    yield


def test_migration_created_tables_and_singletons(pg_db):
    from core.postgres import pg_fetchall, pg_fetchone
    names = {r["table_name"] for r in pg_fetchall(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")}
    assert {"reminders", "notifications", "push_subscriptions", "alerts",
            "vapid_keys", "heartbeat_state"} <= names
    assert pg_fetchone("SELECT id FROM heartbeat_state WHERE id = 1") is not None


def test_vapid_check_rejects_plaintext(pg_db):
    from core.postgres import pg_execute
    with pytest.raises(psycopg2.Error):
        pg_execute("INSERT INTO vapid_keys (id, public_key, private_key_enc) VALUES (1, 'p', 'plaintext')")


def test_recurring_successor_unique_index(pg_db):
    from datetime import datetime, timezone

    from psycopg2.extras import Json

    from core.postgres import pg_execute
    due = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)
    rule = Json({"type": "daily"})
    pg_execute("INSERT INTO reminders (id, message, due_at, recurrence_rule, series_id) "
               "VALUES ('a', 'm', %s, %s, 's1')", (due, rule))
    # Same (series_id, due_at) → the unique index blocks a duplicate successor.
    n = pg_execute("INSERT INTO reminders (id, message, due_at, recurrence_rule, series_id) "
                   "VALUES ('b', 'm', %s, %s, 's1') "
                   "ON CONFLICT (series_id, due_at) WHERE series_id IS NOT NULL DO NOTHING",
                   (due, rule))
    assert n == 0


def test_alert_dedup_partial_unique(pg_db):
    from alerts import service
    from core.postgres import pg_fetchone
    service.create_alert("Heartbeat failing", "1", source="heartbeat", source_id="heartbeat")
    service.create_alert("Heartbeat failing", "2", source="heartbeat", source_id="heartbeat")
    row = pg_fetchone("SELECT count(*) AS n FROM alerts WHERE status = 'active'")
    assert row["n"] == 1                       # deduped to one active row
    row = pg_fetchone("SELECT message FROM alerts WHERE status = 'active'")
    assert row["message"] == "2"               # upsert refreshed the message


def test_atomic_claim_prevents_double_fire(pg_db):
    from datetime import datetime, timezone

    from core.postgres import pg_execute
    from reminders import service
    due = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc).isoformat()
    pg_execute("INSERT INTO reminders (id, message, due_at, status) VALUES ('r1', 'm', %s, 'pending')",
               (datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),))
    reminder = {"id": "r1", "message": "m", "context": "", "due_at": due,
                "recurrence_rule": None, "series_id": None}

    results = []
    barrier = threading.Barrier(2)

    def worker():
        barrier.wait()
        results.append(service.claim_reminder(reminder) is not None)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [False, True]     # exactly one claim wins


def test_recurring_claim_creates_successor(pg_db):
    # Exercises the RETURNING path against real Postgres: a recurring reminder's
    # claim must build the next occurrence from the fresh row (proves row_to_dict's
    # ISO-stringified due_at parses and the JSONB rule comes back as a dict).
    from datetime import datetime, timezone

    from psycopg2.extras import Json

    from core.postgres import pg_execute, pg_fetchall
    from reminders import service
    due = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)   # far past
    pg_execute(
        "INSERT INTO reminders (id, message, due_at, status, recurrence_rule, series_id) "
        "VALUES ('rec1', 'standup', %s, 'pending', %s, 'rec1')",
        (due, Json({"type": "daily"})),
    )
    reminder = {"id": "rec1"}   # deliberately minimal — claim must use the RETURNING row
    claimed = service.claim_reminder(reminder)
    assert claimed is not None and claimed["message"] == "standup"   # fresh row returned
    pending = pg_fetchall("SELECT id, due_at FROM reminders WHERE status = 'pending' AND series_id = 'rec1'")
    assert len(pending) == 1                    # exactly one successor created
    assert pending[0]["id"] != "rec1"           # a new occurrence, not the fired one


def test_force_turn_blocked_only_while_running(pg_db, monkeypatch):
    # thread-3 fix: the force (run-now) guard blocks a 2nd turn ONLY while one is
    # actually in flight (status 'running'), not merely because a turn ran recently.
    from core.postgres import pg_execute
    from heartbeat import service
    monkeypatch.setattr(service.settings, "heartbeat_enabled", True)
    monkeypatch.setattr(service, "get_ai_provider", lambda *a, **k: object())
    calls = {"n": 0}
    from assistant.background import BackgroundResult
    monkeypatch.setattr(service.background, "run_background_turn",
                        lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1),
                                         BackgroundResult(text="ok", error=False))[1])
    monkeypatch.setattr(service, "ToolRegistry", lambda **k: object())
    monkeypatch.setattr(service.background, "background_allowlist", lambda reg: set())
    monkeypatch.setattr(service.identity, "get_identity", lambda: {"name": "Baker"})

    # A turn that COMPLETED 1 second ago (status 'ok', last_turn_at now) → force runs.
    pg_execute("UPDATE heartbeat_state SET last_turn_at = now(), last_turn_status = 'ok' WHERE id = 1")
    out = service.maybe_run_heartbeat_turn(force=True)
    assert out.get("status") == "ok" and calls["n"] == 1

    # A turn currently RUNNING (status 'running', last_turn_at now) → force blocked.
    pg_execute("UPDATE heartbeat_state SET last_turn_at = now(), last_turn_status = 'running' WHERE id = 1")
    out = service.maybe_run_heartbeat_turn(force=True)
    assert out == {"skipped": "in_progress"} and calls["n"] == 1   # no 2nd turn ran


def test_vapid_persist_once(pg_db, monkeypatch):
    from core.postgres import pg_fetchone
    from notifications import vapid
    first = vapid.get_vapid_keys()
    second = vapid.get_vapid_keys()
    assert first == second                      # stable across calls
    assert pg_fetchone("SELECT count(*) AS n FROM vapid_keys")["n"] == 1


def test_due_reminders_uses_timestamp_comparison(pg_db):
    from datetime import datetime, timedelta, timezone

    from core.postgres import pg_execute
    from reminders import service
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    pg_execute("INSERT INTO reminders (id, message, due_at, status) VALUES ('p', 'past', %s, 'pending')", (past,))
    pg_execute("INSERT INTO reminders (id, message, due_at, status) VALUES ('f', 'future', %s, 'pending')", (future,))
    due = service.get_due_reminders()
    ids = {r["id"] for r in due}
    assert "p" in ids and "f" not in ids
