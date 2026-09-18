"""Real-Postgres integration for notification recipients (issue #192, Phase B / B3).

The hermetic suite pins query SHAPE. These run the real SQL against the real migrated
schema, which is the only way to prove the things that would otherwise surface on
somebody's install:

  * migration ``20260917173624_notification_recipients.sql`` applies on a schema that
    already has both tables, and leaves BOTH columns NULL — no claim, on purpose;
  * a legacy (NULL) row behaves exactly as the no-claim decision requires: visible to
    every seat, and a legacy PUSH endpoint receives broadcasts and NEVER a targeted send;
  * one seat cannot read or dismiss another's notification;
  * ``ON DELETE CASCADE`` really removes a deleted seat's targeted rows and endpoints
    rather than widening them into broadcasts the whole install can then see.

The "legacy row" cases insert rows with ``user_id`` left NULL, which is byte-identical
to what the migration leaves behind — seeding before the migration would need a second
migration runner pass to prove the same thing.

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

    dbname = f"cakecrm_it192_{os.getpid()}"
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
def seats(pg_db):
    """Two live seats, rebuilt per test. Deleting users cascades the rows under test."""
    from core.postgres import pg_execute, pg_fetchone
    pg_execute("DELETE FROM notifications")
    pg_execute("DELETE FROM push_subscriptions")
    pg_execute("DELETE FROM users")
    ids = {}
    for key, email, role in (("me", "me@example.com", "admin"),
                             ("them", "them@example.com", "member")):
        ids[key] = pg_fetchone(
            "INSERT INTO users (email, name, password_hash, role) "
            "VALUES (%s, %s, '$2b$12$notarealhashnotarealhashnotarealhashnotarealhash', %s) "
            "RETURNING id", (email, key, role))["id"]
    return ids


# ── the migration itself ──────────────────────────────────────────────────────

def test_migration_added_both_nullable_columns_and_claimed_nothing():
    from core.postgres import pg_execute, pg_fetchall, pg_fetchone

    cols = {(r["table_name"], r["column_name"]): r for r in pg_fetchall(
        "SELECT table_name, column_name, is_nullable, data_type "
        "FROM information_schema.columns "
        "WHERE table_name IN ('notifications', 'push_subscriptions') AND column_name = 'user_id' "
        "ORDER BY table_name")}
    assert set(cols) == {("notifications", "user_id"), ("push_subscriptions", "user_id")}
    assert all(c["is_nullable"] == "YES" for c in cols.values())

    # A pre-#192 row is one written without the column. It must stay NULL: claiming
    # notifications would retroactively hide install history from members, and claiming
    # push endpoints would send the admin's targeted pushes to a member's browser.
    pg_execute("INSERT INTO notifications (id, title, message) VALUES ('legacy', 't', 'm')")
    assert pg_fetchone("SELECT user_id FROM notifications WHERE id = 'legacy'")["user_id"] is None


def test_indexes_exist():
    from core.postgres import pg_fetchall
    names = {r["indexname"] for r in pg_fetchall(
        "SELECT indexname FROM pg_indexes "
        "WHERE tablename IN ('notifications', 'push_subscriptions')")}
    assert {"idx_notifications_user", "idx_push_subscriptions_user"} <= names


# ── the bell: mine or broadcast, and nothing else ────────────────────────────

def test_a_seat_sees_its_own_and_the_installs_but_not_anothers(seats):
    from notifications import service

    service.create_notification("mine", "m", [], user_id=seats["me"])
    service.create_notification("theirs", "m", [], user_id=seats["them"])
    service.create_notification("everyones", "m", [])          # broadcast

    titles = {n["title"] for n in service.list_notifications(user_id=seats["me"], limit=50)}
    assert titles == {"mine", "everyones"}
    assert service.get_active_count(user_id=seats["me"]) == 2

    theirs = {n["title"] for n in service.list_notifications(user_id=seats["them"], limit=50)}
    assert theirs == {"theirs", "everyones"}


def test_status_all_does_not_widen_the_recipient_filter(seats):
    from notifications import service
    service.create_notification("theirs", "m", [], user_id=seats["them"])
    rows = service.list_notifications(user_id=seats["me"], status="all", limit=50)
    assert rows == []


def test_one_seat_cannot_dismiss_anothers(seats):
    from notifications import service

    nid = service.create_notification("theirs", "m", [], user_id=seats["them"])
    assert "error" in service.dismiss_notification(nid, user_id=seats["me"])
    assert service.get_active_count(user_id=seats["them"]) == 1     # still theirs, still active

    assert service.dismiss_notification(nid, user_id=seats["them"])["ok"] is True
    assert service.get_active_count(user_id=seats["them"]) == 0


def test_dismiss_all_leaves_other_seats_alone(seats):
    from notifications import service

    service.create_notification("mine", "m", [], user_id=seats["me"])
    service.create_notification("theirs", "m", [], user_id=seats["them"])
    service.create_notification("everyones", "m", [])

    assert service.dismiss_all(user_id=seats["me"])["dismissed"] == 2   # mine + broadcast
    assert service.get_active_count(user_id=seats["them"]) == 1         # their own survives


# ── push subscriptions: the no-claim decision and its self-heal ──────────────

def test_a_legacy_endpoint_gets_broadcasts_only(seats):
    from core.postgres import pg_execute
    from notifications import subscriptions

    pg_execute("INSERT INTO push_subscriptions (id, endpoint, p256dh, auth) "
               "VALUES ('legacy', 'https://push.example/legacy', 'p', 'a')")
    subscriptions.save_subscription("https://push.example/mine", "p", "a", seats["me"])

    broadcast = {s["endpoint"] for s in subscriptions.list_subscriptions()}
    assert broadcast == {"https://push.example/legacy", "https://push.example/mine"}

    targeted = {s["endpoint"] for s in subscriptions.list_subscriptions(user_id=seats["me"])}
    assert targeted == {"https://push.example/mine"}


def test_resubscribing_rebinds_a_handed_down_browser(seats):
    """The self-heal, end to end: one browser, two occupants, one endpoint row."""
    from core.postgres import pg_fetchone
    from notifications import subscriptions

    endpoint = "https://push.example/shared-browser"
    subscriptions.save_subscription(endpoint, "p", "a", seats["them"], "UA")
    subscriptions.save_subscription(endpoint, "p2", "a2", seats["me"], "UA")

    row = pg_fetchone("SELECT user_id, p256dh FROM push_subscriptions WHERE endpoint = %s",
                      (endpoint,))
    assert row["user_id"] == seats["me"]
    assert row["p256dh"] == "p2"
    assert pg_fetchone("SELECT COUNT(*) AS c FROM push_subscriptions")["c"] == 1
    assert subscriptions.list_subscriptions(user_id=seats["them"]) == []


def test_unsubscribe_is_scoped_but_a_legacy_endpoint_is_still_removable(seats):
    from core.postgres import pg_execute
    from notifications import subscriptions

    subscriptions.save_subscription("https://push.example/theirs", "p", "a", seats["them"])
    pg_execute("INSERT INTO push_subscriptions (id, endpoint, p256dh, auth) "
               "VALUES ('legacy', 'https://push.example/legacy', 'p', 'a')")

    assert subscriptions.remove_subscription("https://push.example/theirs", seats["me"]) is False
    assert subscriptions.remove_subscription("https://push.example/legacy", seats["me"]) is True
    assert subscriptions.remove_subscription("https://push.example/theirs", seats["them"]) is True


# ── deleting a seat ───────────────────────────────────────────────────────────

def test_deleting_a_seat_destroys_its_rows_rather_than_broadcasting_them(seats):
    from core.postgres import pg_execute, pg_fetchone
    from notifications import service, subscriptions

    service.create_notification("theirs", "m", [], user_id=seats["them"])
    service.create_notification("everyones", "m", [])
    subscriptions.save_subscription("https://push.example/theirs", "p", "a", seats["them"])

    pg_execute("DELETE FROM users WHERE id = %s", (seats["them"],))

    # SET NULL here would turn one member's private notifications into broadcasts the
    # whole install can suddenly read — which is why this column CASCADEs.
    assert pg_fetchone("SELECT COUNT(*) AS c FROM notifications")["c"] == 1
    assert pg_fetchone("SELECT title FROM notifications")["title"] == "everyones"
    assert pg_fetchone("SELECT COUNT(*) AS c FROM push_subscriptions")["c"] == 0
