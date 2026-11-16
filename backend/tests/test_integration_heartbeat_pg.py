"""Real-Postgres integration for issue #6 — proves what mocks can't: the migrations
apply (including #188's drop of the reminders table), the DB constraints hold (VAPID
enc CHECK, the active-alert partial unique index), the force-turn guard blocks only a
live turn, and VAPID keys persist once.

Marked ``integration`` and excluded from the default no-DB run. Admin DSN via
TEST_ADMIN_DSN (defaults to the local dev container).
"""

import os

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
    pg_execute("TRUNCATE notifications, push_subscriptions, alerts, vapid_keys")
    pg_execute("TRUNCATE todos, deals, contacts, proactive_nudges RESTART IDENTITY CASCADE")
    pg_execute("UPDATE heartbeat_state SET last_turn_at = NULL, consecutive_errors = 0, "
               "last_failure_alert_at = NULL, last_turn_status = 'ok', "
               "turn_enabled = TRUE, interval_minutes = NULL, quiet_start = NULL, quiet_end = NULL, "
               "paused_until = NULL, notify_min_gap_hours = 4, last_turn_notify_at = NULL WHERE id = 1")
    yield


def test_migration_created_tables_and_singletons(pg_db):
    from core.postgres import pg_fetchall, pg_fetchone
    names = {r["table_name"] for r in pg_fetchall(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")}
    assert {"notifications", "push_subscriptions", "alerts",
            "vapid_keys", "heartbeat_state"} <= names
    # #188 dropped the #6 reminders table; #283 created a fresh one. Its `people` column,
    # which the #6 shape never had, proves the drop AND the new create both ran, in order.
    assert "reminders" in names
    cols = {r["column_name"] for r in pg_fetchall(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'reminders'")}
    assert "people" in cols
    assert pg_fetchone("SELECT id FROM heartbeat_state WHERE id = 1") is not None


def test_vapid_check_rejects_plaintext(pg_db):
    from core.postgres import pg_execute
    with pytest.raises(psycopg2.Error):
        pg_execute("INSERT INTO vapid_keys (id, public_key, private_key_enc) VALUES (1, 'p', 'plaintext')")


def test_alert_dedup_partial_unique(pg_db):
    from alerts import service
    from core.postgres import pg_fetchone
    service.create_alert("Heartbeat failing", "1", source="heartbeat", source_id="heartbeat")
    service.create_alert("Heartbeat failing", "2", source="heartbeat", source_id="heartbeat")
    row = pg_fetchone("SELECT count(*) AS n FROM alerts WHERE status = 'active'")
    assert row["n"] == 1                       # deduped to one active row
    row = pg_fetchone("SELECT message FROM alerts WHERE status = 'active'")
    assert row["message"] == "2"               # upsert refreshed the message


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


@pytest.fixture
def heartbeat(monkeypatch):
    from assistant.background import BackgroundResult
    from heartbeat import service

    monkeypatch.setattr(service.settings, "heartbeat_enabled", True)
    monkeypatch.setattr(service.settings, "heartbeat_interval_minutes", 30)
    monkeypatch.setattr(service, "get_ai_provider", lambda: object())
    messages = []

    def run(prompt, user_message, *, allowed_tools, registry, model_tier, timeout):
        messages.append(user_message)
        return BackgroundResult(text="HEARTBEAT_OK")

    monkeypatch.setattr(service.background, "run_background_turn", run)
    return service, messages


def _due():
    from core.postgres import pg_execute
    pg_execute("UPDATE heartbeat_state SET last_turn_at = now() - interval '2 days', last_turn_status = 'ok'")


def _todo(title="Call"):
    from core.postgres import pg_fetchone
    return pg_fetchone("INSERT INTO todos (title, due_date) VALUES (%s, '2000-01-01') RETURNING id", (title,))["id"]


@pytest.mark.parametrize("change,local,throttled", [
    ("turn_enabled = TRUE", "12:00", False),
    ("turn_enabled = FALSE", "12:00", True),
    ("paused_until = now() + interval '1 day'", "12:00", True),
    ("paused_until = now() - interval '1 minute'", "12:00", False),
    ("interval_minutes = 1440, last_turn_at = now() - interval '2 hours'", "12:00", True),
    ("interval_minutes = NULL, last_turn_at = now() - interval '2 hours'", "12:00", False),
    ("last_turn_notify_at = now() - interval '1 hour'", "12:00", True),
    ("last_turn_notify_at = now() - interval '1 hour', notify_min_gap_hours = 0", "12:00", False),
    ("quiet_start = '22:00', quiet_end = '07:00'", "23:30", True),
    ("quiet_start = '22:00', quiet_end = '07:00'", "06:59", True),
    ("quiet_start = '22:00', quiet_end = '07:00'", "07:00", False),
    ("quiet_start = '22:00', quiet_end = '07:00'", "12:00", False),
    ("quiet_start = '09:00', quiet_end = '17:00'", "12:00", True),
    ("quiet_start = '09:00', quiet_end = '17:00'", "17:00", False),
])
def test_the_periodic_claim_honours_each_setting(heartbeat, monkeypatch, change, local, throttled):
    from core.postgres import pg_execute, pg_fetchone
    service, messages = heartbeat
    _todo()
    _due()
    pg_execute("UPDATE heartbeat_state SET " + change + " WHERE id = 1")
    before = pg_fetchone("SELECT last_turn_at FROM heartbeat_state WHERE id = 1")
    monkeypatch.setattr(service, "_local_hhmm", lambda: local)
    result = service.maybe_run_heartbeat_turn()
    if throttled:
        assert result == {"skipped": "throttled"}
        assert messages == []
        assert pg_fetchone("SELECT last_turn_at FROM heartbeat_state WHERE id = 1") == before
    else:
        assert result["status"] == "ok"
        assert len(messages) == 1


def test_a_periodic_claim_never_overlaps_a_running_turn(heartbeat, monkeypatch):
    from core.postgres import pg_execute
    service, messages = heartbeat
    _todo()
    monkeypatch.setattr(service.settings, "heartbeat_interval_minutes", 1)
    pg_execute("UPDATE heartbeat_state SET last_turn_status = 'running', last_turn_at = now() - interval '2 minutes'")
    assert service.maybe_run_heartbeat_turn() == {"skipped": "throttled"}
    assert messages == []
    pg_execute("UPDATE heartbeat_state SET last_turn_at = now() - interval '10 minutes'")
    assert service.maybe_run_heartbeat_turn()["status"] == "ok"
    assert len(messages) == 1


def test_run_now_ignores_off_pause_quiet_hours_and_the_gap(heartbeat, monkeypatch):
    from core.postgres import pg_execute
    service, messages = heartbeat
    monkeypatch.setattr(service.settings, "heartbeat_enabled", False)
    monkeypatch.setattr(service, "_local_hhmm", lambda: "23:00")
    pg_execute("UPDATE heartbeat_state SET turn_enabled = FALSE, paused_until = now() + interval '7 days', "
               "quiet_start = '22:00', quiet_end = '07:00', last_turn_notify_at = now(), interval_minutes = 1440")
    assert service.maybe_run_heartbeat_turn(force=True)["status"] == "ok"
    assert service.maybe_run_heartbeat_turn(force=True)["status"] == "ok"
    assert len(messages) == 2


def test_the_same_overdue_todo_is_announced_once(heartbeat):
    from core.postgres import pg_execute, pg_fetchall
    service, messages = heartbeat
    first, second = _todo("Call"), _todo("Follow up")
    service.maybe_run_heartbeat_turn()
    assert len(messages) == 1
    assert f"todo {first}:" in messages[0] and f"todo {second}:" in messages[0]
    assert len(pg_fetchall("SELECT id FROM proactive_nudges WHERE kind = 'heartbeat'")) == 2
    _due()
    service.maybe_run_heartbeat_turn()
    assert len(messages) == 1
    pg_execute("UPDATE todos SET due_date = '' WHERE id = %s", (first,))
    _due()
    service.maybe_run_heartbeat_turn()
    assert len(messages) == 1
    assert len(pg_fetchall("SELECT id FROM proactive_nudges WHERE kind = 'heartbeat'")) == 1
    third = _todo("Send quote")
    _due()
    service.maybe_run_heartbeat_turn()
    assert len(messages) == 2
    assert f"todo {third}:" in messages[-1]
    assert f"todo {first}:" not in messages[-1] and f"todo {second}:" not in messages[-1]
    # A cleared record that returns is new again.
    pg_execute("UPDATE todos SET due_date = '2000-01-01' WHERE id = %s", (first,))
    _due()
    service.maybe_run_heartbeat_turn()
    assert len(messages) == 3 and f"todo {first}:" in messages[-1]


def test_touching_the_first_of_21_stale_deals_announces_nothing(heartbeat):
    from core.postgres import pg_execute, pg_fetchall
    service, messages = heartbeat
    pg_execute("INSERT INTO deals (title, created_at, updated_at) "
               "SELECT 'Example ' || n, now() - interval '30 days', now() - interval '30 days' "
               "FROM generate_series(1, 21) n")
    service.maybe_run_heartbeat_turn()
    assert len(messages) == 1
    assert "(21 new)" in messages[0] and "and 11 more" in messages[0]
    assert len(pg_fetchall("SELECT id FROM proactive_nudges WHERE kind = 'heartbeat' AND entity_type = 'deal'")) == 21
    pg_execute("UPDATE deals SET updated_at = now() WHERE id = (SELECT min(id) FROM deals)")
    _due()
    service.maybe_run_heartbeat_turn()
    assert len(messages) == 1
    assert len(pg_fetchall("SELECT id FROM proactive_nudges WHERE kind = 'heartbeat' AND entity_type = 'deal'")) == 20


def test_a_notification_starts_the_gap(heartbeat, monkeypatch):
    from assistant.background import BackgroundResult
    from core.postgres import pg_execute, pg_fetchone
    service, messages = heartbeat

    def run(prompt, user_message, *, allowed_tools, registry, model_tier, timeout):
        messages.append(user_message)
        registry._notify_user_called = True
        registry._notify_user_delivered = True
        return BackgroundResult(text="Notified")

    monkeypatch.setattr(service.background, "run_background_turn", run)
    first = _todo()
    service.maybe_run_heartbeat_turn()
    assert pg_fetchone("SELECT last_turn_notify_at FROM heartbeat_state")["last_turn_notify_at"] is not None
    second = _todo("Quote")
    _due()
    assert service.maybe_run_heartbeat_turn() == {"skipped": "throttled"}
    assert len(messages) == 1
    pg_execute("UPDATE heartbeat_state SET last_turn_notify_at = now() - interval '5 hours'")
    _due()
    service.maybe_run_heartbeat_turn()
    assert len(messages) == 2
    assert f"todo {second}:" in messages[-1] and f"todo {first}:" not in messages[-1]


def test_settings_round_trip_and_the_database_checks_them(pg_db):
    from core.postgres import pg_execute, pg_fetchone
    from heartbeat.settings import get_settings, update_settings
    update_settings({"interval_minutes": 1440, "quiet_hours": "22:00-07:00", "pause_days": 7})
    result = get_settings()
    assert result["interval_minutes"] == 1440
    assert result["quiet_hours"] == "22:00-07:00"
    assert result["paused_until"] is not None
    assert pg_fetchone("SELECT paused_until > now() + interval '6 days 23 hours' AS valid FROM heartbeat_state")["valid"]
    with pytest.raises(psycopg2.Error):
        pg_execute("UPDATE heartbeat_state SET interval_minutes = 5")
    update_settings({"pause_days": 0, "quiet_hours": "off"})
    assert get_settings()["paused_until"] is None
    assert get_settings()["quiet_hours"] == "off"


def test_snapshot_pruning_leaves_other_nudge_kinds_alone(pg_db):
    from core.postgres import pg_execute, pg_fetchall
    from heartbeat import dedupe
    pg_execute("INSERT INTO proactive_nudges (entity_type, entity_id, kind) VALUES ('deal', 7, 'stale_deal')")
    dedupe.save([("todo", 5)])
    dedupe.save([])
    assert pg_fetchall("SELECT entity_type, entity_id, kind FROM proactive_nudges") == [
        {"entity_type": "deal", "entity_id": 7, "kind": "stale_deal"}]
