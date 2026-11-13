"""Real PostgreSQL checks for atomic Quick update and open-first bounded search."""
import os

import psycopg2
import pytest

from core.postgres import pg_execute, pg_fetchall, pg_fetchone
from crm import quick_update as qu, service

pytestmark = pytest.mark.integration
ADMIN_DSN = os.getenv("TEST_ADMIN_DSN", "postgresql://cake:cake_dev@localhost:5432/cake")


@pytest.fixture(scope="module")
def pg_db():
    from core import postgres

    dbname = f"cakecrm_quick_update_{os.getpid()}"
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
def records():
    actor = pg_fetchone("INSERT INTO users (email, name, password_hash) VALUES (%s, %s, 'x') RETURNING id",
                       ("ada@example.test", "Ada"))["id"]
    contact = service.create_contact(name="Canopy buyer")["id"]
    deal = service.create_deal(title="Canopy Foods", stage="qualified", value=1000, contact_id=contact)["id"]
    yield actor, contact, deal
    pg_execute("DELETE FROM users WHERE id = %s", (actor,))


def _notes(deal):
    return pg_fetchall("SELECT message, author_id FROM crm_chatter WHERE entity_type = 'deal' AND entity_id = %s ORDER BY id ASC", (deal,))


def test_one_batch_persists_columns_stage_event_note_and_linked_todo(records):
    actor, contact, deal = records
    result = qu.apply(deal, qu.QuickUpdateBody(value=2500, stage="proposal", note="sent samples", followup="Call Ada"), actor)
    assert result == {"deal_id": deal, "saved": ["value", "stage", "note", "follow-up"]}
    row = service.get_deal(deal)
    assert (row["value"], row["stage"]) == (2500, "proposal")
    assert pg_fetchall("SELECT old_stage, new_stage FROM deal_stage_events WHERE deal_id = %s ORDER BY id ASC", (deal,)) == [
        {"old_stage": "qualified", "new_stage": "proposal"}]
    assert _notes(deal) == [{"message": "sent samples", "author_id": actor}]
    assert pg_fetchall("SELECT title, contact_id, deal_id, owner_id, status, source FROM todos WHERE deal_id = %s ORDER BY id ASC", (deal,)) == [
        {"title": "Call Ada", "contact_id": contact, "deal_id": deal, "owner_id": actor, "status": "next_action", "source": "ui"}]


def test_won_and_lost_keep_closed_on_probability_and_reason_rules(records):
    actor, _, deal = records
    qu.apply(deal, qu.QuickUpdateBody(won=True, closed_on="2026-01-01"), actor)
    row = service.get_deal(deal)
    assert str(row["closed_on"]) == "2026-01-01"
    assert row["probability"] == 100
    qu.apply(deal, qu.QuickUpdateBody(lost=True, lost_reason="price"), actor)
    row = service.get_deal(deal)
    assert (row["stage"], row["lost_reason"], row["probability"], row["closed_on"]) == ("lost", "price", 0, None)
    assert {"message": "Deal lost — price", "author_id": actor} in _notes(deal)


def test_failure_in_followup_rolls_back_every_write(records, monkeypatch):
    actor, _, deal = records
    def fail(*args, **kwargs):
        raise RuntimeError("todo refused")
    monkeypatch.setattr(service, "_create_todo_cur", fail)
    with pytest.raises(RuntimeError, match="todo refused"):
        qu.apply(deal, qu.QuickUpdateBody(value=2500, stage="proposal", note="sent samples", followup="Call Ada"), actor)
    row = service.get_deal(deal)
    assert (row["value"], row["stage"]) == (1000, "qualified")
    assert _notes(deal) == []
    assert pg_fetchall("SELECT id FROM deal_stage_events WHERE deal_id = %s ORDER BY id ASC", (deal,)) == []
    assert pg_fetchall("SELECT id FROM todos WHERE deal_id = %s ORDER BY id ASC", (deal,)) == []


def test_archived_deal_refuses_note_only_update(records):
    actor, _, deal = records
    service.archive_deal(deal, archived=True, reason="Duplicate")
    before = _notes(deal)
    with pytest.raises(ValueError, match="archived"):
        qu.apply(deal, qu.QuickUpdateBody(note="called"), actor)
    assert _notes(deal) == before


def test_search_places_open_deals_before_limit():
    opened = []
    for i in range(30):
        row = service.create_deal(title=f"Canopy {i}", stage="qualified" if i < 4 else "lost")
        if i < 4:
            opened.append(row["id"])
            pg_execute("UPDATE deals SET updated_at = now() - interval '1 day' WHERE id = %s", (row["id"],))
    rows = qu.search_deals("Canopy")
    assert len(rows) == 25
    assert {r["id"] for r in rows[:4]} == set(opened)
    assert all(r["stage"] == "lost" for r in rows[4:])
