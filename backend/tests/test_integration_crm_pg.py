"""Real-Postgres integration for the CRM core — proves what mocks can't: the
migration applies, CRUD + aggregates round-trip, FK ON DELETE SET NULL vs the
explicit cascade behave as ported, the fictional seed is idempotent and advances
the SERIAL sequences, and the first-run demo state machine works end-to-end.

Marked ``integration`` and excluded from the default no-DB run. Uses a throwaway
database in the local dev container (TEST_ADMIN_DSN), same pattern as
test_integration_pg.py, with an autouse per-test CRM cleanup so scenarios don't
contaminate each other.
"""

import os

import psycopg2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

ADMIN_DSN = os.getenv("TEST_ADMIN_DSN", "postgresql://cake:cake_dev@localhost:5432/cake")


@pytest.fixture(scope="module")
def pg_db():
    from core import postgres

    dbname = f"cakecrm_it_crm_{os.getpid()}"
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
    postgres.run_migrations()  # applies EVERY migration, incl. crm_core
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
def _clean_crm(pg_db):
    """Reset all CRM state between tests so scenarios stay isolated."""
    from core.postgres import pg_execute
    pg_execute("TRUNCATE activity_log, tasks, deals, contacts RESTART IDENTITY")
    pg_execute(
        "UPDATE crm_meta SET sample_data_loaded = FALSE, onboarding_dismissed = FALSE, "
        "ai_key_prompt_dismissed = FALSE WHERE id = 1"
    )
    yield


def _client():
    from core.auth import get_current_user
    from crm.router import router as crm_router
    app = FastAPI()
    app.include_router(crm_router, prefix="/api/crm")
    app.dependency_overrides[get_current_user] = lambda: {"sub": "u"}
    return TestClient(app)


# ── Migration ─────────────────────────────────────────────────────────────────

def test_migration_created_tables_and_singleton(pg_db):
    from core.postgres import pg_fetchall, pg_fetchone
    names = {
        r["table_name"]
        for r in pg_fetchall(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
    }
    assert {"contacts", "deals", "tasks", "activity_log", "crm_meta"} <= names
    meta = pg_fetchone("SELECT * FROM crm_meta WHERE id = 1")
    assert meta and meta["sample_data_loaded"] is False
    # issue #9 migration: durable AI-key-nudge dismissal, default FALSE
    assert meta["ai_key_prompt_dismissed"] is False


# ── Fresh empty install (the acceptance clause, at the data layer) ────────────

def test_fresh_empty_dashboard(pg_db):
    from crm import service
    stats = service.get_dashboard_stats()
    assert stats["total_contacts"] == 0
    assert stats["pipeline_by_stage"] == []
    assert stats["total_pipeline_value"] == 0
    assert stats["overdue_tasks"] == 0 and stats["pending_tasks"] == 0
    assert stats["recent_activity"] == [] and stats["top_deals"] == []
    assert service.list_contacts()["contacts"] == []
    assert service.get_pipeline()["deals"] == []
    assert service.get_demo_status()["show_onboarding"] is True


# ── Full CRUD + FK behavior + search + aggregates ─────────────────────────────

def test_crud_fk_search_and_aggregates(pg_db):
    from crm import service

    c = service.create_contact("Ada Lovelace", email="ada@x.io", company="Analytical",
                               tags="vip, math", status="active")
    assert c["id"] == 1 and c["tags"] == "vip,math"

    d = service.create_deal("Engine build", contact_id=c["id"], stage="proposal", value=5000)
    t = service.create_task("Follow up", contact_id=c["id"], deal_id=d["id"], due_date="2000-01-01")
    a = service.log_activity("call", note="intro", contact_id=c["id"], deal_id=d["id"])
    assert d["contact_id"] == 1 and t["id"] == 1 and a["id"] == 1

    # tag ILIKE search (case-insensitive) + status filter
    assert [r["id"] for r in service.search_contacts("analytical")] == [1]
    assert [r["id"] for r in service.search_contacts("ada", tags="VIP")] == [1]

    # update + complete->reopen via int flag
    service.update_contact(1, status="inactive")
    assert service.get_contact(1)["status"] == "inactive"
    assert service.complete_task(1)["completed"] == 1
    service.update_task(1, completed=0)
    assert service.get_task(1)["completed"] == 0

    # dashboard aggregates over known rows
    stats = service.get_dashboard_stats()
    assert stats["total_contacts"] == 1
    assert stats["overdue_tasks"] == 1  # due 2000-01-01, incomplete
    assert stats["total_pipeline_value"] == 5000

    # delete contact: deal.contact_id -> NULL (SET NULL), task + activity deleted (cascade)
    assert service.delete_contact(1) is True
    assert service.get_contact(1) is None
    surviving = service.get_deal(d["id"])
    assert surviving is not None and surviving["contact_id"] is None
    assert service.get_task(1) is None
    assert service.get_activity_log() == []


# ── Seed idempotency + sequence advance ───────────────────────────────────────

def test_seed_idempotent_and_sequences_advance(pg_db):
    from core.postgres import pg_fetchone
    from crm import service

    out = service.load_sample_data()
    assert out == {"ok": True, "seeded": True}
    assert pg_fetchone("SELECT COUNT(*) AS c FROM contacts")["c"] == 8
    assert pg_fetchone("SELECT COUNT(*) AS c FROM deals")["c"] == 7
    assert pg_fetchone("SELECT COUNT(*) AS c FROM tasks")["c"] == 8
    assert pg_fetchone("SELECT COUNT(*) AS c FROM activity_log")["c"] == 11
    assert service.get_crm_meta()["sample_data_loaded"] is True

    # second call is a clean no-op (CRM no longer empty)
    assert service.load_sample_data() == {"ok": True, "seeded": False}

    # the next real insert gets id 9 — sequence advanced past the fixed demo ids
    assert service.create_contact("New Person")["id"] == 9


# ── First-run demo state machine via the HTTP surface ─────────────────────────

def test_demo_state_machine_over_http(pg_db):
    client = _client()

    status = client.get("/api/crm/demo-status").json()
    assert status == {"empty": True, "sample_data_loaded": False, "show_onboarding": True,
                      "ai_key_prompt_dismissed": False}

    seeded = client.post("/api/crm/load-sample-data").json()
    assert seeded["seeded"] is True
    after = client.get("/api/crm/demo-status").json()
    assert after == {"empty": False, "sample_data_loaded": True, "show_onboarding": False,
                     "ai_key_prompt_dismissed": False}

    # guarded clear wipes example data and restarts identities
    cleared = client.post("/api/crm/demo-clear").json()
    assert cleared == {"ok": True, "cleared": True}
    dash = client.get("/api/crm/dashboard").json()
    assert dash["total_contacts"] == 0

    from crm import service
    meta = service.get_crm_meta()
    assert meta["sample_data_loaded"] is False and meta["onboarding_dismissed"] is True
    # onboarding stays dismissed → the load prompt does not reappear
    assert client.get("/api/crm/demo-status").json()["show_onboarding"] is False


def test_demo_clear_guarded_when_no_sample(pg_db):
    from crm import service
    client = _client()
    # user-entered data, no sample loaded → demo-clear must NOT wipe it
    service.create_contact("Real Customer")
    resp = client.post("/api/crm/demo-clear").json()
    assert resp == {"ok": True, "cleared": False}
    assert service.get_dashboard_stats()["total_contacts"] == 1


def test_dismiss_ai_prompt_persists(pg_db):
    from crm import service
    client = _client()
    before = service.get_crm_meta()
    assert client.get("/api/crm/demo-status").json()["ai_key_prompt_dismissed"] is False
    assert client.post("/api/crm/dismiss-ai-prompt").json() == {"ok": True}
    # durable: reflected in a fresh read
    assert client.get("/api/crm/demo-status").json()["ai_key_prompt_dismissed"] is True
    after = service.get_crm_meta()
    assert after["ai_key_prompt_dismissed"] is True
    # independent of the onboarding flags — dismiss-ai-prompt must not touch them
    assert after["onboarding_dismissed"] == before["onboarding_dismissed"]
    assert after["sample_data_loaded"] == before["sample_data_loaded"]
    assert client.get("/api/crm/demo-status").json()["show_onboarding"] is True


def test_search_pagination_and_contact_unlink(pg_db):
    from crm import service
    for i in range(3):
        service.create_contact(f"Person {i}", company="Acme Corp")
    # accurate total + real pagination (fixes the silent 20-row cap)
    assert service.count_search_contacts("acme") == 3
    page1 = service.search_contacts("acme", limit=2, offset=0)
    page2 = service.search_contacts("acme", limit=2, offset=2)
    assert len(page1) == 2 and len(page2) == 1
    assert {c["id"] for c in page1}.isdisjoint({c["id"] for c in page2})

    # unlink: update_deal with contact_id=None clears the FK
    c = service.create_contact("Linked")
    d = service.create_deal("Deal", contact_id=c["id"])
    assert service.get_deal(d["id"])["contact_id"] == c["id"]
    service.update_deal(d["id"], contact_id=None)
    assert service.get_deal(d["id"])["contact_id"] is None
