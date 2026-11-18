"""Real Postgres regression coverage for activity-driven deal recency (#385)."""

import os
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import psycopg2
import pytest

from core.postgres import pg_execute, pg_fetchone
from crm import analytics_service, service
from gmail_scan import service as gs

pytestmark = pytest.mark.integration
ADMIN_DSN = os.getenv("TEST_ADMIN_DSN", "postgresql://cake:cake_dev@localhost:5432/cake")


@pytest.fixture(scope="module")
def pg_db():
    from core import postgres

    dbname = f"cakecrm_it_deal_touch_{os.getpid()}"
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
    postgres.run_migrations()   # applies EVERY migration, incl. 20260726230309_gmail_touch_scan
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
def clean(pg_db, monkeypatch):
    pg_execute("TRUNCATE deals, contacts, activity_log, deal_stage_events RESTART IDENTITY CASCADE")
    pg_execute("TRUNCATE gmail_scanned_messages")
    # Keep asynchronous AI work out of these database tests; scoring stays real.
    monkeypatch.setattr(service.touch_count_service, "schedule_recompute", lambda *a: False)


def deal_at(when, stage="lead", archived=False, contact_id=None):
    return pg_fetchone(
        "INSERT INTO deals (title, stage, created_at, updated_at, archived_at, contact_id) "
        "VALUES ('Example deal', %s, %s, %s, %s, %s) RETURNING *",
        (stage, when, when, when if archived else None, contact_id),
    )


def updated_at(deal):
    return pg_fetchone("SELECT updated_at FROM deals WHERE id = %s", (deal["id"],))["updated_at"]


def test_activity_advances_default_list_and_pipeline_recency():
    now = datetime.now(timezone.utc)
    older = deal_at(now - timedelta(days=20))
    newer = deal_at(now - timedelta(days=1))
    assert service.list_deals()[0]["id"] == newer["id"]
    assert service.get_pipeline(stage="lead")["deals"][0]["id"] == newer["id"]

    activity = service.log_activity("call", deal_id=older["id"])

    assert updated_at(older) == activity["created_at"]
    assert updated_at(older) > older["updated_at"]
    assert service.list_deals()[0]["id"] == older["id"]
    assert service.get_pipeline(stage="lead")["deals"][0]["id"] == older["id"]


def test_untouched_old_deal_is_stale_until_activity():
    now = datetime.now(timezone.utc)
    older = deal_at(now - timedelta(days=20))
    fresh = deal_at(now)
    assert "stale" not in analytics_service.get_deal_health(fresh["id"])["flags"]
    assert [d["id"] for d in analytics_service.get_stale_deals()["deals"]] == [older["id"]]
    assert "stale" in analytics_service.get_deal_health(older["id"])["flags"]

    service.log_activity("call", deal_id=older["id"])

    assert analytics_service.get_stale_deals()["deals"] == []
    assert "stale" not in analytics_service.get_deal_health(older["id"])["flags"]


@pytest.mark.parametrize("stage,archived", [("won", False), ("lost", False), ("lead", True)])
def test_activity_preserves_closed_and_archived_dates(stage, archived):
    deal = deal_at(datetime.now(timezone.utc) - timedelta(days=20), stage, archived)
    activity = service.log_activity("call", deal_id=deal["id"])
    assert activity["deal_id"] == deal["id"]
    assert pg_fetchone("SELECT id FROM activity_log WHERE id = %s", (activity["id"],))
    assert updated_at(deal) == deal["updated_at"]


@pytest.mark.parametrize("message_hours,stage", [(1, "lead"), (3, "lead"), (1, "won")])
def test_gmail_message_touches_only_open_deal_without_regressing(message_hours, stage):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    contact = pg_fetchone(
        "INSERT INTO contacts (name, email) VALUES ('Example person', 'person@example.com') RETURNING id"
    )
    deal = deal_at(now - timedelta(hours=2), stage=stage, contact_id=contact["id"])
    message_at = now - timedelta(hours=message_hours)
    result = gs._process_message(
        {"id": "touch-message", "from": "person@example.com", "subject": "Hello",
         "date": format_datetime(message_at)},
        "me@example.com",
    )
    expected_deal_id = deal["id"] if stage == "lead" else None
    assert result == {"outcome": "logged", "deal_id": expected_deal_id}
    activity = pg_fetchone("SELECT * FROM activity_log")
    assert activity["deal_id"] == expected_deal_id
    assert activity["contact_id"] == contact["id"]
    assert datetime.fromisoformat(activity["created_at"]) == message_at
    original = datetime.fromisoformat(deal["updated_at"])
    expected = max(original, message_at) if stage == "lead" else original
    assert datetime.fromisoformat(updated_at(deal)) == expected
