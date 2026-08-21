"""Real-Postgres integration for the CRM core — proves what mocks can't: the
migration applies, CRUD + aggregates round-trip, FK ON DELETE SET NULL vs the
explicit cascade behave as ported, the fictional seed is idempotent and advances
the SERIAL sequences, and the first-run demo state machine works end-to-end.

Marked ``integration`` and excluded from the default no-DB run. Uses a throwaway
database in the local dev container (TEST_ADMIN_DSN), same pattern as
test_integration_pg.py, with an autouse per-test CRM cleanup so scenarios don't
contaminate each other.
"""

import io
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
    pg_execute(
        "TRUNCATE companies, contacts, deals, activity_log, tasks, crm_chatter, "
        "crm_field_definitions, crm_field_values, crm_field_provenance, "
        "deal_stage_events, proactive_nudges, deal_ai_touch_evidence RESTART IDENTITY"
    )
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
    assert {"companies", "contacts", "deals", "tasks", "activity_log", "crm_meta", "crm_chatter",
            "crm_field_definitions", "crm_field_values", "crm_field_provenance"} <= names
    meta = pg_fetchone("SELECT * FROM crm_meta WHERE id = 1")
    assert meta and meta["sample_data_loaded"] is False
    # issue #9 migration: durable AI-key-nudge dismissal, default FALSE
    assert meta["ai_key_prompt_dismissed"] is False
    # issue #16 migration: the three AI-touch-count columns on deals (NULL by default)
    deal_cols = {
        r["column_name"]
        for r in pg_fetchall(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'deals'"
        )
    }
    assert {"ai_touch_count", "ai_touch_count_at", "ai_touch_evidence_count"} <= deal_cols


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
    assert pg_fetchone("SELECT COUNT(*) AS c FROM companies")["c"] == 6
    assert pg_fetchone("SELECT COUNT(*) AS c FROM contacts")["c"] == 8
    assert pg_fetchone("SELECT COUNT(*) AS c FROM deals")["c"] == 7
    assert pg_fetchone("SELECT COUNT(*) AS c FROM tasks")["c"] == 8
    assert pg_fetchone("SELECT COUNT(*) AS c FROM activity_log")["c"] == 11
    assert pg_fetchone("SELECT COUNT(*) AS c FROM crm_chatter")["c"] == 4
    assert service.get_crm_meta()["sample_data_loaded"] is True

    # seeded contacts/deals are linked to their companies (rollup demos on day one)
    assert pg_fetchone(
        "SELECT company_id FROM contacts WHERE name = %s", ("Maria Santos",)
    )["company_id"] == 1
    assert pg_fetchone(
        "SELECT company_id FROM deals WHERE title LIKE %s", ("Weekly bread%",)
    )["company_id"] == 1

    # second call is a clean no-op (CRM no longer empty)
    assert service.load_sample_data() == {"ok": True, "seeded": False}

    # the next real inserts get fresh ids — sequences advanced past the fixed demo ids
    assert service.create_contact("New Person")["id"] == 9
    assert service.create_company("New Company")["id"] == 7


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


# ── Companies: CRUD, rollup, uniqueness, delete-unlink (issue #13) ────────────

def test_company_crud_rollup_and_unlink(pg_db):
    from crm import service

    co = service.create_company("Acme", industry="Tech", domain="acme.com")
    assert co["id"] == 1 and co["status"] == "active"

    c = service.create_contact("Ada", company_id=co["id"])
    d = service.create_deal("Big deal", contact_id=c["id"], company_id=co["id"],
                            stage="proposal", value=1000)
    won = service.create_deal("Closed", company_id=co["id"], stage="won", value=9999)
    service.log_activity("call", note="hi", contact_id=c["id"])

    detail = service.get_company_detail(co["id"])
    assert [x["id"] for x in detail["contacts"]] == [c["id"]]
    assert {x["id"] for x in detail["deals"]} == {d["id"], won["id"]}
    assert detail["open_deal_value"] == 1000  # won excluded
    # activity rolled up through the company's contact (activity has no company_id)
    assert any(a["activity"] == "call" for a in detail["activity"])

    # detail joins expose the linked company name
    assert service.get_contact_detail(c["id"])["company_name"] == "Acme"
    assert service.get_deal(d["id"])["company_name"] == "Acme"

    # case/whitespace-insensitive uniqueness is DB-enforced
    with pytest.raises(psycopg2.errors.UniqueViolation):
        service.create_company("  acme ")

    # delete company: contacts/deals are kept but unlinked (ON DELETE SET NULL)
    assert service.delete_company(co["id"]) is True
    assert service.get_contact(c["id"]) is not None
    assert service.get_contact_detail(c["id"])["company_id"] is None
    assert service.get_deal(d["id"])["company_id"] is None


def test_companies_backfill_migration(pg_db):
    """One-shot backfill on its OWN throwaway DB + raw connection (guaranteed
    teardown; never touches the module-global pool that pg_db owns): distinct
    case/whitespace-insensitive company names become companies (lowest-id
    spelling wins), contacts link, deals inherit, empties stay NULL."""
    from pathlib import Path

    migrations = Path(__file__).resolve().parent.parent / "migrations"
    crm_core = (migrations / "20260723221920_crm_core.sql").read_text()
    companies_sql = (migrations / "20260724062314_companies.sql").read_text()

    dbname = f"cakecrm_it_backfill_{os.getpid()}"
    admin = psycopg2.connect(ADMIN_DSN)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
        cur.execute(f'CREATE DATABASE "{dbname}"')
    admin.close()

    dsn = ADMIN_DSN.rsplit("/", 1)[0] + f"/{dbname}"
    conn = None
    try:
        conn = psycopg2.connect(dsn)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(crm_core)
        # legacy company text: case + tab/newline variants + empty/whitespace-only
        cur.executemany(
            "INSERT INTO contacts (name, company) VALUES (%s, %s)",
            [("A", "Acme"), ("B", "\tAcme\n"), ("C", "ACME"),
             ("D", "Beta Corp"), ("E", ""), ("F", "   ")],
        )
        # a deal on a linked contact (A) and one on the empty-company contact (E)
        cur.execute(
            "INSERT INTO deals (contact_id, title) VALUES "
            "((SELECT id FROM contacts WHERE name='A'), 'DealA'), "
            "((SELECT id FROM contacts WHERE name='E'), 'DealE')"
        )
        cur.execute(companies_sql)

        # exactly 2 companies; Acme keeps the lowest-id (contact A) spelling
        cur.execute("SELECT name FROM companies ORDER BY id")
        assert [r[0] for r in cur.fetchall()] == ["Acme", "Beta Corp"]

        # A, B, C collapse to the Acme id; D to Beta; E, F stay NULL
        cur.execute("SELECT name, company_id FROM contacts ORDER BY name")
        links = dict(cur.fetchall())
        assert links["A"] == links["B"] == links["C"] and links["A"] is not None
        assert links["D"] is not None and links["D"] != links["A"]
        assert links["E"] is None and links["F"] is None

        # DealA inherited A's company; DealE (empty-company contact) stays NULL
        cur.execute("SELECT title, company_id FROM deals ORDER BY title")
        deal_links = dict(cur.fetchall())
        assert deal_links["DealA"] == links["A"]
        assert deal_links["DealE"] is None
    finally:
        if conn is not None:
            conn.close()
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


# ── Company link coherence (issue #35) ────────────────────────────────────────

def test_ingestion_resolves_and_autocreates_companies(pg_db):
    """The write-time half of #35: every ingestion path links a company, creating
    it when the name is new, with the migration's normalization."""
    from crm import service

    ada = service.create_contact("Ada", company="  Acme  ")
    assert ada["company_id"] is not None
    assert service.get_company(ada["company_id"])["name"] == "Acme"  # trimmed
    assert ada["company"] == "  Acme  "  # legacy text preserved verbatim

    # a different spelling resolves to the SAME company (case/whitespace-insensitive)
    bob = service.create_contact("Bob", company="ACME")
    assert bob["company_id"] == ada["company_id"]
    assert service.count_search_companies("Acme") == 1

    # update with new text auto-creates and re-links
    bob2 = service.update_contact(bob["id"], company="Beta Corp")
    assert bob2["company_id"] not in (None, ada["company_id"])
    # ...and clearing the text unlinks (the link is what the UI renders)
    assert service.update_contact(bob["id"], company="")["company_id"] is None

    # an explicit null company_id wins over text sitting beside it (ContactForm)
    assert service.update_contact(ada["id"], company="Acme", company_id=None)["company_id"] is None

    # an explicit id is respected as-is, never overwritten by the text
    beta = service.create_company("Gamma Inc")
    cy = service.create_contact("Cy", company="Totally Different", company_id=beta["id"])
    assert cy["company_id"] == beta["id"]


def test_contact_list_and_search_are_link_authoritative(pg_db):
    """The read-time half of #35: a contact linked to a company is findable and
    displayable by that company's name even with NO legacy free text."""
    from crm import service

    co = service.create_company("Initech")
    linked = service.create_contact("Peter", company_id=co["id"])  # no free text
    assert linked["company"] == ""

    rows = service.list_contacts()["contacts"]
    row = next(r for r in rows if r["id"] == linked["id"])
    assert row["company_name"] == "Initech"  # what the UI renders

    # searchable by company name purely via the join — the reported bug
    found = service.search_contacts("Initech")
    assert [c["id"] for c in found] == [linked["id"]]
    assert service.count_search_contacts("Initech") == 1

    # a rename propagates immediately (no text to go stale)
    service.update_company(co["id"], name="Initech Global")
    assert service.search_contacts("Initech Global")[0]["id"] == linked["id"]
    row = next(r for r in service.list_contacts()["contacts"] if r["id"] == linked["id"])
    assert row["company_name"] == "Initech Global"

    # unlinked contacts stay findable by their legacy text
    service.create_contact("Milton", company="Legacy Only")
    assert service.search_contacts("Legacy Only")[0]["name"] == "Milton"
    # ...and the join never multiplies rows
    assert service.count_search_contacts("") == len(service.list_contacts()["contacts"])


def test_search_matches_old_company_spelling_but_displays_new_name(pg_db):
    """Deliberate, and pinned so nobody 'fixes' it: the legacy text stays in the
    search predicate for LINKED contacts too, so a rename doesn't make the
    contact unfindable by the name the user remembers — while the row it returns
    is labelled with the authoritative new name."""
    from crm import service

    co = service.create_company("Acme")
    c = service.create_contact("Ada", company="Acme")   # links, and keeps the text
    assert c["company_id"] == co["id"]

    service.update_company(co["id"], name="Beta")

    by_new = service.search_contacts("Beta")
    assert [r["id"] for r in by_new] == [c["id"]]

    by_old = service.search_contacts("Acme")            # the stale free text
    assert [r["id"] for r in by_old] == [c["id"]]
    assert by_old[0]["company_name"] == "Beta"          # ...labelled authoritatively
    assert service.count_search_contacts("Acme") == 1   # count agrees with the rows


def test_csv_import_populates_companies_page(pg_db):
    """The headline acceptance: fresh install -> import my contacts -> the
    Companies page is populated, not silently empty."""
    from crm import service

    csv_text = (
        "Name,Email,Company\n"
        "Ada Lovelace,ada@x.io,Acme Corp\n"
        "Bob Stone,bob@x.io,acme corp\n"   # same company, different case
        "Cy Young,cy@x.io,Beta LLC\n"
        ",ghost@x.io,Ghost Co\n"           # skipped: no name
    )
    client = _client()
    resp = client.post(
        "/api/crm/import",
        files={"file": ("contacts.csv", io.BytesIO(csv_text.encode()), "text/csv")},
    )
    assert resp.status_code == 200
    assert resp.json()["imported"] == 3 and resp.json()["skipped"] == 1

    names = sorted(c["name"] for c in service.list_companies()["companies"])
    assert names == ["Acme Corp", "Beta LLC"]  # Ghost Co never created

    # both Acme rows point at the one company, and its rollup sees them
    acme = next(c for c in service.list_companies()["companies"] if c["name"] == "Acme Corp")
    assert {c["name"] for c in service.get_company_detail(acme["id"])["contacts"]} == {
        "Ada Lovelace", "Bob Stone"
    }


def test_second_backfill_migration_repairs_pre_35_imports(pg_db):
    """The repair half of #35, on its own throwaway DB (same pattern as the #13
    backfill test): contacts written between #13 and #35 get linked.

    Precisely what is and isn't protected:
      - an EXISTING contact link to a different company is never overwritten (E);
      - deals are never touched at all, so a deliberate deal unlink survives;
      - but a CONTACT whose link was deliberately removed while its text still
        names a company IS relinked (D) — accepted and documented in the
        migration header, because nothing distinguishes that from "never linked".
    """
    from pathlib import Path

    migrations = Path(__file__).resolve().parent.parent / "migrations"
    crm_core = (migrations / "20260723221920_crm_core.sql").read_text()
    companies_sql = (migrations / "20260724062314_companies.sql").read_text()
    backfill_sql = (migrations / "20260816203810_company_link_backfill.sql").read_text()

    dbname = f"cakecrm_it_backfill2_{os.getpid()}"
    admin = psycopg2.connect(ADMIN_DSN)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
        cur.execute(f'CREATE DATABASE "{dbname}"')
    admin.close()

    dsn = ADMIN_DSN.rsplit("/", 1)[0] + f"/{dbname}"
    conn = None
    try:
        conn = psycopg2.connect(dsn)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(crm_core)
        cur.execute(companies_sql)  # #13 runs on an empty CRM: creates nothing

        # Now simulate post-#13 ingestion: free text only, no company_id.
        cur.execute("INSERT INTO companies (name) VALUES ('Acme')")
        cur.execute(
            "INSERT INTO contacts (name, company) VALUES "
            "('A', 'ACME'), ('B', 'Beta Corp'), ('C', ''), ('D', 'Acme')"
        )
        # D stands in for a deliberately-unlinked contact (relinked — accepted);
        # E is deliberately linked to a DIFFERENT company (must be left alone)
        cur.execute("INSERT INTO companies (name) VALUES ('Other Co')")
        cur.execute(
            "INSERT INTO contacts (name, company, company_id) VALUES "
            "('E', 'Acme', (SELECT id FROM companies WHERE name='Other Co'))"
        )
        # deals on a backfilled contact (A) and on an already-linked one (E):
        # NEITHER may be touched — unlike #13, this backfill does not inherit
        cur.execute(
            "INSERT INTO deals (contact_id, title) VALUES "
            "((SELECT id FROM contacts WHERE name='A'), 'DealA'), "
            "((SELECT id FROM contacts WHERE name='E'), 'DealE')"
        )
        cur.execute("SELECT name, updated_at FROM contacts ORDER BY name")
        before = dict(cur.fetchall())

        cur.execute(backfill_sql)

        # 'ACME' matched the EXISTING Acme (ON CONFLICT path — no duplicate)
        cur.execute("SELECT name FROM companies ORDER BY name")
        assert [r[0] for r in cur.fetchall()] == ["Acme", "Beta Corp", "Other Co"]

        cur.execute("SELECT name, company_id FROM contacts ORDER BY name")
        links = dict(cur.fetchall())
        acme_id = links["A"]
        assert acme_id is not None and links["D"] == acme_id   # both link to Acme
        assert links["B"] is not None and links["B"] != acme_id
        assert links["C"] is None                              # empty text stays NULL
        # E's deliberate link to a DIFFERENT company is untouched
        cur.execute("SELECT id FROM companies WHERE name = 'Other Co'")
        assert links["E"] == cur.fetchone()[0]

        # No deal is touched: "company_id IS NULL" can no longer be distinguished
        # from a deliberate unlink, so this backfill leaves deals entirely alone
        cur.execute("SELECT title, company_id FROM deals ORDER BY title")
        assert dict(cur.fetchall()) == {"DealA": None, "DealE": None}

        # a backfill is not an edit
        cur.execute("SELECT name, updated_at FROM contacts ORDER BY name")
        assert dict(cur.fetchall()) == before

        # idempotent: re-running changes nothing
        cur.execute(backfill_sql)
        cur.execute("SELECT COUNT(*) FROM companies")
        assert cur.fetchone()[0] == 3
    finally:
        if conn is not None:
            conn.close()
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


# ── Chatter / notes (issue #15) ───────────────────────────────────────────────

def test_chatter_crud_archive_restore_roundtrip(pg_db):
    from crm import chatter_service, service

    d = service.create_deal("Chatter deal", value=1000)
    n1 = chatter_service.add_note("deal", d["id"], "  first note  ")
    n2 = chatter_service.add_note("deal", d["id"], "second note")
    assert n1["message"] == "first note" and n1["updated_at"] is None and n1["archived"] == 0

    # newest first, deterministic
    notes = chatter_service.get_chatter("deal", d["id"])
    assert [n["id"] for n in notes] == [n2["id"], n1["id"]]

    # edit stamps updated_at
    edited = chatter_service.update_note(n1["id"], "first note (edited)")
    assert edited["message"] == "first note (edited)" and edited["updated_at"] is not None

    # archive hides from the default list; include_archived shows it; restore brings it back
    assert chatter_service.archive_note(n1["id"]) is True
    assert [n["id"] for n in chatter_service.get_chatter("deal", d["id"])] == [n2["id"]]
    assert len(chatter_service.get_chatter("deal", d["id"], include_archived=True)) == 2
    assert chatter_service.unarchive_note(n1["id"]) is True
    assert len(chatter_service.get_chatter("deal", d["id"])) == 2

    # missing note ids are falsy, not errors
    assert chatter_service.update_note(999999, "x") is None
    assert chatter_service.archive_note(999999) is None


def test_chatter_rejects_invalid_and_nonexistent_targets(pg_db):
    from crm import chatter_service, service

    with pytest.raises(ValueError):
        chatter_service.add_note("company", 1, "hi")       # bad type
    with pytest.raises(ValueError):
        chatter_service.add_note("deal", 999999, "hi")     # target does not exist
    c = service.create_contact("Has notes")
    with pytest.raises(ValueError):
        chatter_service.add_note("contact", c["id"], "   ")  # blank message


def test_chatter_dropped_on_contact_delete_and_clear(pg_db):
    from core.postgres import pg_fetchone
    from crm import chatter_service, service

    c = service.create_contact("Doomed")
    chatter_service.add_note("contact", c["id"], "note that must not outlive the contact")
    assert pg_fetchone("SELECT COUNT(*) AS n FROM crm_chatter")["n"] == 1
    service.delete_contact(c["id"])
    assert pg_fetchone("SELECT COUNT(*) AS n FROM crm_chatter")["n"] == 0

    # ID reuse after clear_all: a new deal reusing id 1 inherits no old notes
    d = service.create_deal("First deal")
    chatter_service.add_note("deal", d["id"], "old deal-1 note")
    service.clear_all()
    d2 = service.create_deal("New deal reusing id 1")
    assert d2["id"] == d["id"]  # SERIAL restarted
    assert chatter_service.get_chatter("deal", d2["id"]) == []


def test_chatter_http_roundtrip(pg_db):
    from crm import service

    d = service.create_deal("HTTP deal")
    client = _client()
    r = client.post(f"/api/crm/chatter/deal/{d['id']}/note", json={"message": "via http"})
    assert r.status_code == 200
    note_id = r.json()["id"]

    listed = client.get(f"/api/crm/chatter/deal/{d['id']}").json()
    assert listed["count"] == 1 and listed["notes"][0]["message"] == "via http"

    assert client.patch(f"/api/crm/chatter/note/{note_id}", json={"message": "edited"}).status_code == 200
    assert client.post(f"/api/crm/chatter/note/{note_id}/archive").json() == {"ok": True}
    assert client.get(f"/api/crm/chatter/deal/{d['id']}").json()["count"] == 0
    assert client.get(f"/api/crm/chatter/deal/{d['id']}?include_archived=true").json()["count"] == 1
    # blank + missing + bad-type contract at the HTTP layer
    assert client.post(f"/api/crm/chatter/deal/{d['id']}/note", json={"message": " "}).status_code == 400
    assert client.patch("/api/crm/chatter/note/999999", json={"message": "x"}).status_code == 404
    # 'company' became a valid chatter type in issue #22 — probe with one that never is.
    assert client.get("/api/crm/chatter/invoice/1").status_code == 400


# ── Custom fields (issue #19) ─────────────────────────────────────────────────

def test_field_definition_crud_and_entity_scoped_unique(pg_db):
    from crm import field_service as fs

    a = fs.create_field_definition({"entity_type": "contact", "name": "Account Tier",
                                    "field_type": "select", "dropdown_options": ["Gold", "Silver"]})
    assert a["field_key"] == "account_tier" and a["dropdown_options"] == ["Gold", "Silver"]
    assert a["display_order"] == 10

    # A second contact field gets the next server-assigned order (max+10).
    b = fs.create_field_definition({"entity_type": "contact", "name": "Region", "field_type": "text"})
    assert b["display_order"] == 20

    # Same name on a DIFFERENT entity type is allowed (UNIQUE is per entity_type).
    fs.create_field_definition({"entity_type": "deal", "name": "Account Tier", "field_type": "text"})

    # Duplicate (entity_type, name) is rejected by the UNIQUE index.
    with pytest.raises(psycopg2.errors.UniqueViolation):
        fs.create_field_definition({"entity_type": "contact", "name": "Account Tier", "field_type": "text"})

    # Update touches only mutable columns; field_key stays put.
    updated = fs.update_field_definition(a["id"], {"name": "Tier", "is_required": True})
    assert updated["name"] == "Tier" and updated["is_required"] == 1 and updated["field_key"] == "account_tier"

    assert fs.delete_field_definition(a["id"]) is True
    assert fs.delete_field_definition(a["id"]) is False


def test_field_values_upsert_get_validation_and_clear(pg_db):
    from crm import field_service as fs, service
    client = _client()

    contact = service.create_contact("Grace Hopper")
    num = fs.create_field_definition({"entity_type": "contact", "name": "Budget", "field_type": "number"})
    sel = fs.create_field_definition({"entity_type": "contact", "name": "Tier", "field_type": "select",
                                      "dropdown_options": ["A", "B"]})

    # Unset defs appear with value=None.
    rows = fs.get_field_values("contact", contact["id"])
    assert {r["field_key"] for r in rows} == {"budget", "tier"}
    assert all(r["value"] is None for r in rows)

    # PUT upsert via the router.
    r = client.put(f"/api/crm/contact/{contact['id']}/fields",
                   json={"values": {str(num["id"]): "5000", str(sel["id"]): "A"}})
    assert r.status_code == 200 and r.json()["updated"] == 2
    by_key = {row["field_key"]: row["value"] for row in fs.get_field_values("contact", contact["id"])}
    assert by_key == {"budget": "5000", "tier": "A"}

    # Re-PUT updates in place (no duplicate rows — UNIQUE(entity,entity_id,field)).
    client.put(f"/api/crm/contact/{contact['id']}/fields", json={"values": {str(num["id"]): "6000"}})
    from core.postgres import pg_fetchone
    cnt = pg_fetchone("SELECT COUNT(*) AS c FROM crm_field_values WHERE field_id = %s", (num["id"],))
    assert cnt["c"] == 1

    # Type validation rejects a bad number (400).
    bad = client.put(f"/api/crm/contact/{contact['id']}/fields", json={"values": {str(num["id"]): "abc"}})
    assert bad.status_code == 400

    # Empty string clears a number/select field (the divergence-from-source bug fix).
    ok = client.put(f"/api/crm/contact/{contact['id']}/fields",
                    json={"values": {str(num["id"]): "", str(sel["id"]): ""}})
    assert ok.status_code == 200
    by_key = {row["field_key"]: row["value"] for row in fs.get_field_values("contact", contact["id"])}
    assert by_key == {"budget": "", "tier": ""}


def test_definition_delete_cascades_values(pg_db):
    from core.postgres import pg_fetchone
    from crm import field_service as fs, service

    contact = service.create_contact("Katherine Johnson")
    d = fs.create_field_definition({"entity_type": "contact", "name": "Note", "field_type": "text"})
    fs.set_field_values("contact", contact["id"], {str(d["id"]): "hi"}, "u")
    assert pg_fetchone("SELECT COUNT(*) AS c FROM crm_field_values")["c"] == 1
    fs.delete_field_definition(d["id"])                       # FK ON DELETE CASCADE
    assert pg_fetchone("SELECT COUNT(*) AS c FROM crm_field_values")["c"] == 0


def test_set_values_partial_validation_rolls_back(pg_db):
    """One valid + one invalid field in a single write → the whole batch rolls back;
    the valid field is NOT persisted (proves the txn guarantee mocks can't show)."""
    from crm import field_service as fs, service

    contact = service.create_contact("Dorothy Vaughan")
    text = fs.create_field_definition({"entity_type": "contact", "name": "Notes", "field_type": "text"})
    num = fs.create_field_definition({"entity_type": "contact", "name": "Score", "field_type": "number"})
    with pytest.raises(ValueError):
        fs.set_field_values("contact", contact["id"],
                            {str(text["id"]): "keep me", str(num["id"]): "not-a-number"}, "u")
    # The valid text field must not have persisted.
    by_key = {row["field_key"]: row["value"] for row in fs.get_field_values("contact", contact["id"])}
    assert by_key == {"notes": None, "score": None}


def test_delete_contact_drops_field_values_no_orphan(pg_db):
    from core.postgres import pg_execute, pg_fetchone
    from crm import field_service as fs, service

    contact = service.create_contact("Mary Jackson")           # id 1
    d = fs.create_field_definition({"entity_type": "contact", "name": "Clearance", "field_type": "text"})
    fs.set_field_values("contact", contact["id"], {str(d["id"]): "secret"}, "u")

    service.delete_contact(contact["id"])
    # The value row is gone — no polymorphic orphan left behind.
    assert pg_fetchone(
        "SELECT COUNT(*) AS c FROM crm_field_values WHERE entity_type='contact' AND entity_id=%s",
        (contact["id"],),
    )["c"] == 0
    # A new contact reusing the same id inherits nothing.
    pg_execute("INSERT INTO contacts (id, name) VALUES (%s, %s)", (contact["id"], "Reused"))
    assert all(r["value"] is None for r in fs.get_field_values("contact", contact["id"]))


def test_delete_company_drops_field_values_no_orphan(pg_db):
    from core.postgres import pg_fetchone
    from crm import field_service as fs, service

    company = service.create_company("Hidden Figures Inc")
    d = fs.create_field_definition({"entity_type": "company", "name": "Segment", "field_type": "text"})
    fs.set_field_values("company", company["id"], {str(d["id"]): "enterprise"}, "u")

    assert service.delete_company(company["id"]) is True
    assert pg_fetchone(
        "SELECT COUNT(*) AS c FROM crm_field_values WHERE entity_type='company' AND entity_id=%s",
        (company["id"],),
    )["c"] == 0


def test_is_required_toggle_via_put(pg_db):
    """PUT flips is_required (Pydantic bool → INTEGER column) on real Postgres."""
    from crm import field_service as fs
    client = _client()
    d = fs.create_field_definition({"entity_type": "deal", "name": "Priority", "field_type": "text"})
    assert d["is_required"] == 0
    r = client.put(f"/api/crm/fields/{d['id']}", json={"is_required": True})
    assert r.status_code == 200 and r.json()["is_required"] == 1


def test_field_definitions_do_not_block_sample_seed(pg_db):
    """Decision 9: a bare field definition (no entities/values) must NOT suppress the
    first-run sample-data prompt."""
    from crm import field_service as fs, service

    fs.create_field_definition({"entity_type": "contact", "name": "Persona", "field_type": "text"})
    status = service.get_demo_status()
    assert status["empty"] is True and status["show_onboarding"] is True
    assert service.load_sample_data() == {"ok": True, "seeded": True}
    # The definition survived the (insert-only) seed.
    assert [d["field_key"] for d in fs.list_field_definitions("contact")] == ["persona"]


def test_demo_clear_preserves_definitions_clear_all_wipes(pg_db):
    """R2: demo-clear keeps the user's field schema (wipes values); clear_all wipes it."""
    from core.postgres import pg_fetchone
    from crm import field_service as fs, service

    service.load_sample_data()
    d = fs.create_field_definition({"entity_type": "contact", "name": "Owner", "field_type": "text"})
    fs.set_field_values("contact", 1, {str(d["id"]): "will"}, "u")

    assert service.clear_demo_data()["cleared"] is True
    # Definitions survive demo-clear; values are gone; entities are gone.
    assert [x["field_key"] for x in fs.list_field_definitions("contact")] == ["owner"]
    assert pg_fetchone("SELECT COUNT(*) AS c FROM crm_field_values")["c"] == 0
    assert pg_fetchone("SELECT COUNT(*) AS c FROM contacts")["c"] == 0

    service.clear_all()
    assert fs.list_field_definitions() == []               # clear_all is the full wipe


def test_update_cannot_strip_select_options(pg_db):
    """PUT that clears a select's options is rejected (reopening the accept-anything
    hole the create path guards) — API returns 400."""
    from crm import field_service as fs
    client = _client()
    d = fs.create_field_definition({"entity_type": "contact", "name": "Tier", "field_type": "select",
                                    "dropdown_options": ["A", "B"]})
    assert client.put(f"/api/crm/fields/{d['id']}", json={"dropdown_options": []}).status_code == 400
    assert client.put(f"/api/crm/fields/{d['id']}", json={"dropdown_options": None}).status_code == 400
    # a valid options change still works
    r = client.put(f"/api/crm/fields/{d['id']}", json={"dropdown_options": ["A", "B", "C"]})
    assert r.status_code == 200 and r.json()["dropdown_options"] == ["A", "B", "C"]


def test_get_values_404s_for_missing_entity(pg_db):
    client = _client()
    assert client.get("/api/crm/contact/999999/fields").status_code == 404


# ── Analytics (#20): real SQL for win/loss, activity volume, read-time aging ──

def test_analytics_end_to_end(pg_db):
    """Proves the four analytics queries against real Postgres: FILTER aggregates,
    the last-touch GREATEST (incl. archived-chatter exclusion), age bucketing,
    stale ordering, the shared activity window, and JSON-clean (Decimal-coerced)
    output — none of which the hermetic suite can exercise."""
    import json

    from core.postgres import pg_execute
    from crm import service

    # 1) Zero state on a fresh CRM (autouse _clean_crm truncated everything).
    zero = service.get_analytics()
    assert zero["win_loss"]["win_rate_pct"] is None
    assert zero["win_loss"]["total_pipeline_value"] == 0
    assert [b["count"] for b in zero["aging"]["buckets"]] == [0, 0, 0, 0]
    assert zero["aging"]["stale_count"] == 0 and zero["aging"]["stale_deals"] == []
    assert len(zero["activity"]["daily"]) == 30
    assert all(d["count"] == 0 for d in zero["activity"]["daily"])
    assert zero["activity"]["by_type"] == []

    # 2) Seed a known scenario.
    co = service.create_company("Acme")
    c = service.create_contact("Ada")

    def deal(title, stage, value):
        return service.create_deal(title, contact_id=c["id"], company_id=co["id"],
                                   stage=stage, value=value)

    # 3 won (each a 10-day created→updated span → avg_days_to_close == 10.0), 1 lost.
    won_ids = [deal(f"Won {i}", "won", 1000)["id"] for i in range(3)]
    deal("Lost", "lost", 500)
    for wid in won_ids:
        pg_execute(
            "UPDATE deals SET created_at = now() - make_interval(days => 10), "
            "updated_at = now() WHERE id = %s",
            (wid,),
        )

    # 3 open deals: A rescued-by-activity (fresh), B stale (31-90), C stale (91+).
    a = deal("Open A", "lead", 100)["id"]
    b = deal("Open B", "qualified", 200)["id"]
    cc = deal("Open C", "proposal", 300)["id"]
    pg_execute("UPDATE deals SET created_at = now() - make_interval(days => 20), "
               "updated_at = now() - make_interval(days => 20) WHERE id = %s", (a,))
    pg_execute("UPDATE deals SET created_at = now() - make_interval(days => 45), "
               "updated_at = now() - make_interval(days => 20) WHERE id = %s", (b,))
    pg_execute("UPDATE deals SET created_at = now() - make_interval(days => 100), "
               "updated_at = now() - make_interval(days => 40) WHERE id = %s", (cc,))

    # A call TODAY on A refreshes its last-touch (proves activity_log rescues from
    # staleness AND counts toward volume); an email 5 days ago counts toward volume.
    service.log_activity("call", note="ping", contact_id=c["id"], deal_id=a)
    em = service.log_activity("email", note="fu", contact_id=c["id"])
    pg_execute("UPDATE activity_log SET created_at = now() - make_interval(days => 5) "
               "WHERE id = %s", (em["id"],))

    # A FRESH but ARCHIVED chatter note on B must NOT count as a touch (archived = 0).
    pg_execute("INSERT INTO crm_chatter (entity_type, entity_id, message, archived) "
               "VALUES ('deal', %s, 'archived note', 1)", (b,))

    # 3) Assertions.
    r = service.get_analytics(stale_days=14)
    assert r["win_loss"]["deals_won"] == 3 and r["win_loss"]["deals_lost"] == 1
    assert r["win_loss"]["win_rate_pct"] == 75.0
    assert r["win_loss"]["open_deals"] == 3
    assert r["win_loss"]["avg_days_to_close"] == 10.0
    assert r["win_loss"]["total_pipeline_value"] == 600  # 100 + 200 + 300 (open only)

    buckets = {x["label"]: x["count"] for x in r["aging"]["buckets"]}
    assert buckets == {"0-7": 0, "8-30": 1, "31-90": 1, "91+": 1}  # A, B, C

    assert r["aging"]["stale_count"] == 2  # B and C; A rescued by today's call
    assert [d["id"] for d in r["aging"]["stale_deals"]] == [cc, b]  # stalest first (40d, 20d)

    assert r["activity"]["total"] == 2
    assert {t["activity"] for t in r["activity"]["by_type"]} == {"call", "email"}

    json.dumps(r)  # JSON-clean: no stray Decimal/date leaks through the shapers

    # Route smoke: the full superset over HTTP.
    resp = _client().get("/api/crm/analytics")
    assert resp.status_code == 200 and resp.json()["win_loss"]["win_rate_pct"] == 75.0


# ── Pipeline payload: company_name + derived last_activity_at blend (issue #21) ─

def test_get_pipeline_last_activity_blends_activity_and_chatter(pg_db):
    """The board's last_activity_at = MAX across deal-level activity_log rows and
    un-archived deal chatter notes; NULL when a deal has neither. Real SQL proves what
    the hermetic SQL-shape test can't: the UNION-ALL/GROUP BY blend, the NULL/never case,
    the archived-fallback, and that multiple event rows never multiply the deal."""
    from core.postgres import pg_execute
    from crm import chatter_service, service

    co = service.create_company("Acme Corp")
    d = service.create_deal("Big deal", stage="proposal", value=1000, company_id=co["id"])
    did = d["id"]

    def board_deal():
        return next(x for x in service.get_pipeline()["deals"] if x["id"] == did)

    def last_ymd():
        v = board_deal()["last_activity_at"]
        return None if v is None else str(v)[:10]

    # 1. No activity of any kind → NULL (the client's "no activity logged" bucket);
    #    company join is populated on the board payload.
    deal = board_deal()
    assert deal["last_activity_at"] is None
    assert deal["company_name"] == "Acme Corp"

    # 2. Activity-log row only → last_activity is that row's date.
    pg_execute("INSERT INTO activity_log (activity, deal_id, created_at) VALUES (%s, %s, %s)",
               ("call", did, "2026-01-10T09:00:00+00:00"))
    assert last_ymd() == "2026-01-10"

    # 3. A NEWER chatter note wins over the older activity row (add_note uses now()).
    n_new = chatter_service.add_note("deal", did, "just talked to them")
    assert last_ymd() > "2026-01-10"

    # 4. Archiving the newest note falls back to the older activity-log row.
    assert chatter_service.archive_note(n_new["id"]) is True
    assert last_ymd() == "2026-01-10"

    # 5. A chatter note OLDER than the activity row does not win.
    pg_execute("INSERT INTO crm_chatter (entity_type, entity_id, message, created_at) VALUES (%s, %s, %s, %s)",
               ("deal", did, "old note", "2025-06-01T00:00:00+00:00"))
    assert last_ymd() == "2026-01-10"

    # 6. Multiple activity + chatter rows must NOT multiply the deal row.
    pg_execute("INSERT INTO activity_log (activity, deal_id, created_at) VALUES (%s, %s, %s)",
               ("email", did, "2025-12-01T00:00:00+00:00"))
    pg_execute("INSERT INTO crm_chatter (entity_type, entity_id, message, created_at) VALUES (%s, %s, %s, %s)",
               ("deal", did, "another old note", "2025-05-01T00:00:00+00:00"))
    deals = service.get_pipeline()["deals"]
    assert len([x for x in deals if x["id"] == did]) == 1
    assert last_ymd() == "2026-01-10"  # newest overall is still the Jan-10 activity row

    # 7. Chatter attached to a DIFFERENT entity type (contact) never counts for the deal.
    pg_execute("INSERT INTO crm_chatter (entity_type, entity_id, message, created_at) VALUES (%s, %s, %s, %s)",
               ("contact", did, "wrong-entity note", "2030-01-01T00:00:00+00:00"))
    assert last_ymd() == "2026-01-10"

    # 8. A SECOND deal must have an INDEPENDENT last_activity_at — proves the join key
    #    (la.deal_id = d.id over the GROUP BY) attributes rows per deal with no cross-deal
    #    leakage — and the stage-filtered branch (the crm_get_pipeline assistant-tool path)
    #    returns the same per-deal value against real SQL, not just the mocked shape test.
    d2 = service.create_deal("Small deal", stage="lead", value=200, company_id=co["id"])
    did2 = d2["id"]
    pg_execute("INSERT INTO activity_log (activity, deal_id, created_at) VALUES (%s, %s, %s)",
               ("meeting", did2, "2026-03-15T12:00:00+00:00"))
    board = {x["id"]: x for x in service.get_pipeline()["deals"]}
    assert str(board[did]["last_activity_at"])[:10] == "2026-01-10"   # deal 1 unaffected by deal 2's row
    assert str(board[did2]["last_activity_at"])[:10] == "2026-03-15"  # deal 2 sees only its own
    lead_only = service.get_pipeline(stage="lead")["deals"]
    assert [x["id"] for x in lead_only] == [did2]
    assert str(lead_only[0]["last_activity_at"])[:10] == "2026-03-15"


# ── Lead scoring (issue #18) — proves NULLS-LAST ordering, triggers, no-updated_at ──

def test_lead_score_migration_columns_and_index(pg_db):
    from core.postgres import pg_fetchall
    for table in ("deals", "contacts"):
        cols = {r["column_name"] for r in pg_fetchall(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s", (table,))}
        assert {"lead_score", "lead_score_at"} <= cols, table
    meta_cols = {r["column_name"] for r in pg_fetchall(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'crm_meta'")}
    assert "scores_refreshed_at" in meta_cols
    idx = {r["indexname"] for r in pg_fetchall(
        "SELECT indexname FROM pg_indexes WHERE tablename = 'contacts'")}
    assert "idx_contacts_lead_score" in idx


def test_check_constraint_rejects_out_of_range(pg_db):
    from core.postgres import get_connection
    with pytest.raises(psycopg2.errors.CheckViolation):
        with get_connection() as conn:
            conn.cursor().execute("INSERT INTO contacts (name, lead_score) VALUES ('X', 200)")


def test_create_persists_score_in_range(pg_db):
    from crm import service
    deal = service.create_deal("Big deal", stage="negotiation", value=50000)
    assert deal["lead_score"] is not None and 1 <= deal["lead_score"] <= 99
    contact = service.create_contact("Ada", email="a@b.c", status="active")
    assert contact["lead_score"] is not None and 1 <= contact["lead_score"] <= 99


def test_terminal_deal_scores_100_and_0(pg_db):
    from crm import service
    won = service.create_deal("Won", stage="won", value=1000)
    lost = service.create_deal("Lost", stage="lost", value=1000)
    assert won["lead_score"] == 100 and lost["lead_score"] == 0


def test_adding_note_recomputes_but_does_not_bump_updated_at(pg_db):
    from core.postgres import pg_fetchone
    from crm import chatter_service, service
    deal = service.create_deal("Deal", stage="qualified", value=20000)
    before = pg_fetchone("SELECT lead_score, updated_at FROM deals WHERE id = %s", (deal["id"],))
    for _ in range(6):
        chatter_service.add_note("deal", deal["id"], "made progress")
    after = pg_fetchone("SELECT lead_score, updated_at FROM deals WHERE id = %s", (deal["id"],))
    assert after["updated_at"] == before["updated_at"]  # a score write never bumps updated_at
    assert after["lead_score"] >= before["lead_score"]  # engagement rose


def test_contact_sort_nulls_last_and_id_tiebreak(pg_db):
    from core.postgres import pg_execute
    from crm import service
    # Three scored contacts + two never-scored (lead_score NULL).
    ids = [service.create_contact(f"C{i}", status="active")["id"] for i in range(5)]
    pg_execute("UPDATE contacts SET lead_score = 90 WHERE id = %s", (ids[0],))
    pg_execute("UPDATE contacts SET lead_score = 50 WHERE id = %s", (ids[1],))
    pg_execute("UPDATE contacts SET lead_score = 90 WHERE id = %s", (ids[2],))  # tie with ids[0]
    pg_execute("UPDATE contacts SET lead_score = NULL WHERE id IN (%s, %s)", (ids[3], ids[4]))
    rows = service.list_contacts(sort="lead_score", limit=50)["contacts"]
    order = [r["id"] for r in rows]
    scored = [r["id"] for r in rows if r["lead_score"] is not None]
    unscored = [r["id"] for r in rows if r["lead_score"] is None]
    # scored (DESC, ties by id DESC) precede all NULLs
    assert scored == [ids[2], ids[0], ids[1]]   # 90(id2), 90(id0 — lower id after), 50
    assert set(unscored) == {ids[3], ids[4]}
    assert order.index(ids[1]) < order.index(ids[3])  # every scored row before any NULL


def test_backfill_scope_null_only_unscored(pg_db):
    from core.postgres import pg_execute, pg_fetchone
    from crm import scoring_service, service
    d1 = service.create_deal("A", stage="lead")["id"]
    d2 = service.create_deal("B", stage="proposal", value=30000)["id"]
    # Wipe d1's score to simulate an unscored row; pin d2's to a sentinel we can detect.
    pg_execute("UPDATE deals SET lead_score = NULL WHERE id = %s", (d1,))
    pg_execute("UPDATE deals SET lead_score = 7 WHERE id = %s", (d2,))
    out = scoring_service.backfill_scores("null")
    assert out["deals_scored"] >= 1
    assert pg_fetchone("SELECT lead_score FROM deals WHERE id = %s", (d1,))["lead_score"] is not None
    assert pg_fetchone("SELECT lead_score FROM deals WHERE id = %s", (d2,))["lead_score"] == 7  # untouched


def test_concurrent_recompute_serialized_by_advisory_lock(pg_db):
    """Two threads recomputing the same deal must not corrupt the row; the per-entity
    advisory lock serializes them and the final score is a valid computed value."""
    import threading

    from core.postgres import pg_fetchone
    from crm import scoring_service, service
    deal_id = service.create_deal("Race", stage="negotiation", value=40000)["id"]
    errors = []

    def worker():
        try:
            for _ in range(15):
                scoring_service.recompute_deal(deal_id)
        except Exception as e:  # the whole point is to catch a corruption/deadlock
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    final = pg_fetchone("SELECT lead_score FROM deals WHERE id = %s", (deal_id,))
    assert final["lead_score"] is not None and 1 <= final["lead_score"] <= 99


def test_score_refresh_rescoring_stale_rows_only(pg_db):
    """run_score_refresh_if_due drives its session-lock connection across the inner per-entity
    recompute connections on a REAL pool — the one path mocks can't exercise. A stale row is
    rescored; a fresh row is left alone; a second immediate pass finds nothing stale."""
    from datetime import datetime, timedelta, timezone

    from core.postgres import pg_execute, pg_fetchone
    from crm import scoring_service, service

    now = datetime.now(timezone.utc)
    # Reset the coarse due-gate so this pass is due regardless of test ordering.
    pg_execute("UPDATE crm_meta SET scores_refreshed_at = NULL WHERE id = 1")
    stale_deal = service.create_deal("Dormant", stage="qualified", value=20000)["id"]
    fresh_deal = service.create_deal("Active", stage="proposal", value=30000)["id"]
    stale_contact = service.create_contact("Old", status="active")["id"]
    # Age one deal + one contact past the 24h staleness cutoff; pin the fresh deal's score
    # to a sentinel so we can prove it was NOT recomputed.
    old = now - timedelta(hours=48)
    pg_execute("UPDATE deals SET lead_score = 3, lead_score_at = %s WHERE id = %s", (old, stale_deal))
    pg_execute("UPDATE deals SET lead_score = 42, lead_score_at = %s WHERE id = %s", (now, fresh_deal))
    pg_execute("UPDATE contacts SET lead_score = 3, lead_score_at = %s WHERE id = %s", (old, stale_contact))

    out = scoring_service.run_score_refresh_if_due(now=now)
    assert out is not None and out["deals_scored"] == 1 and out["contacts_scored"] == 1

    # stale rows recomputed (lead_score_at advanced past the cutoff; value may coincide, so
    # the advanced timestamp is the proof it ran)
    d = pg_fetchone("SELECT lead_score, lead_score_at FROM deals WHERE id = %s", (stale_deal,))
    assert d["lead_score_at"] > old.isoformat()
    c = pg_fetchone("SELECT lead_score_at FROM contacts WHERE id = %s", (stale_contact,))
    assert c["lead_score_at"] > old.isoformat()
    # fresh deal untouched (sentinel intact)
    assert pg_fetchone("SELECT lead_score FROM deals WHERE id = %s", (fresh_deal,))["lead_score"] == 42

    # second immediate pass: the drain stamped scores_refreshed_at, so the coarse 24h gate
    # closes -> None (no rescan).
    assert scoring_service.run_score_refresh_if_due(now=now) is None


def test_update_deal_stage_response_carries_fresh_score(pg_db):
    # P2 fix: the mutation response must reflect the post-recompute score (the kanban merges it).
    from crm import service
    deal = service.create_deal("Deal", stage="qualified", value=20000)
    assert deal["lead_score"] not in (None, 100)
    won = service.update_deal_stage(deal["id"], "won")
    assert won["lead_score"] == 100  # not the stale pre-recompute value
    reopened = service.update_deal(deal["id"], stage="negotiation")
    assert reopened["lead_score"] not in (None, 100)  # fresh, recomputed from the new stage


def test_deal_engagement_counts_logged_activities(pg_db):
    # P3 fix: activity_log is a touch surface — a deal worked only via logged calls must not
    # read as zero-engagement.
    from core.postgres import pg_fetchone
    from crm import service
    bare = service.create_deal("Bare", stage="qualified", value=20000)
    worked = service.create_deal("Worked", stage="qualified", value=20000)
    for _ in range(6):
        service.log_activity("call", deal_id=worked["id"])
    bare_score = pg_fetchone("SELECT lead_score FROM deals WHERE id = %s", (bare["id"],))["lead_score"]
    worked_score = pg_fetchone("SELECT lead_score FROM deals WHERE id = %s", (worked["id"],))["lead_score"]
    assert worked_score > bare_score


def test_provenance_confirm_notes_excluded_from_engagement(pg_db):
    # Housekeeping confirm-notes (provenance_service.confirm inserts them directly) must NOT
    # count as customer engagement; real notes must.
    from core.postgres import pg_execute
    from crm import chatter_service, scoring_service, service
    deal = service.create_deal("D", stage="qualified", value=20000)
    for _ in range(5):
        pg_execute("INSERT INTO crm_chatter (entity_type, entity_id, message) VALUES ('deal', %s, %s)",
                   (deal["id"], "Confirmed AI-populated value for 'probability'."))
    assert scoring_service.score_deal(deal["id"])["factors"]["engagement"]["value"] == 0
    chatter_service.add_note("deal", deal["id"], "Real customer note")
    assert scoring_service.score_deal(deal["id"])["factors"]["engagement"]["value"] == 1
