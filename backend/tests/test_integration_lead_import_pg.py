"""Real Postgres lead imports: ownership, atomicity, expiry and concurrent consumption."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import psycopg2
import pytest

from assistant import history
from core.postgres import get_connection, pg_execute, pg_fetchall, pg_fetchone
from crm import chatter_service, last_contact, lead_import as imp, service

pytestmark = pytest.mark.integration
ADMIN_DSN = os.getenv("TEST_ADMIN_DSN", "postgresql://cake:cake_dev@localhost:5432/cake")


@pytest.fixture(scope="module")
def pg_db():
    from core import postgres

    dbname = f"cakecrm_it_leadimp_{os.getpid()}"
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
    # A FRESH database matters: the migration runner records applied filenames, so
    # re-applying this new migration against a dirty one would silently skip it.
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
def clean(pg_db):
    with get_connection() as conn:
        service._truncate_all(conn.cursor(), include_definitions=True)
        conn.cursor().execute("DELETE FROM assistant_conversations")
    for user_id in (7, 8):
        pg_execute(
            "INSERT INTO users (id, email, name, password_hash, role) "
            "VALUES (%s, %s, 'Rep', '!', 'member') ON CONFLICT (id) DO NOTHING",
            (user_id, f"rep{user_id}@example.test"),
        )


CSV = (b"Name,Email,Company,Notes\n"
       b"Ana Lopez,ANA@example.test,Northwind Foods,Wants samples\n"
       b"Bo Chen,bo@example.test, northwind foods ,\n"
       b"Cy Dee,,Harbor Bakery,\n")


def count(table):
    return pg_fetchone(f"SELECT COUNT(*) AS n FROM {table}")["n"]


def store_file():
    cid = history.create_conversation(user_id=7)["id"]
    history.save_message(cid, "m1", "user", "see file", files=[
        {"id": "f1", "filename": "leads.csv", "data": CSV},
    ])
    return cid


def test_attachment_preview_later_execute_notes_ownership_and_scores():
    store_file()
    contact = service.create_contact("Existing", email="ana@example.test")
    old_score = contact["lead_score"]
    assert "error" in imp.preview(user_id=8, attachment_id="f1")
    preview = imp.preview(user_id=7, attachment_id="f1", source="Spring trade show")
    assert {k: preview[k] for k in ("leads", "contacts_new", "contacts_existing", "companies_new")} == {
        "leads": 3, "contacts_new": 2, "contacts_existing": 1, "companies_new": 2,
    }
    assert count("companies") == count("deals") == 0
    batch = preview["batch_id"]
    assert "Import 3 leads" in imp.pending_binding({"batch_id": batch}, 7)["summary"]["headline"]
    assert imp.pending_binding({"batch_id": batch}, 8) is None
    assert imp.execute(batch, user_id=8) == {"error": imp.EXPIRED}
    assert count("companies") == 0
    result = imp.execute(batch, note="Met at the spring trade show", user_id=7)
    assert result["ok"] and result["deals_created"] == result["notes_created"] == 3
    assert result["contacts_created"] == 2 and result["contacts_linked"] == 1
    deals = pg_fetchall("SELECT * FROM deals ORDER BY id")
    assert all(d["stage"] == "lead" and d["owner_id"] == 7 for d in deals)
    assert all(d["probability"] == 0 and d["currency"] == "USD" and d["value"] == 0 for d in deals)
    assert deals[0]["contact_id"] == contact["id"]
    assert deals[0]["company_id"] == deals[1]["company_id"]
    assert deals[0]["notes"] == "Notes: Wants samples"
    assert all(d["lead_score"] is not None for d in deals)
    assert count("companies") == 2
    assert all(c["owner_id"] is None for c in pg_fetchall("SELECT owner_id FROM companies"))
    assert all(c["owner_id"] == 7 and c["source"] == "Spring trade show" and c["notes"] == ""
               for c in pg_fetchall("SELECT * FROM contacts WHERE id <> %s", (contact["id"],)))
    notes = pg_fetchall("SELECT * FROM crm_chatter ORDER BY id")
    assert len(notes) == 3
    assert {n["entity_id"] for n in notes} == {d["id"] for d in deals}
    assert all(n["entity_type"] == "deal" and n["message"] == "Met at the spring trade show"
               and n["author_id"] == 7 and not n["system_note"] for n in notes)
    touches = pg_fetchall(f"SELECT {last_contact.last_contact_sql('deal', 'd.id')} AS touched FROM deals d")
    assert all(t["touched"] is not None for t in touches)
    assert service.get_contact(contact["id"])["lead_score"] != old_score
    assert count("assistant_lead_import_previews") == 0
    assert imp.execute(batch, user_id=7) == {"error": imp.EXPIRED}
    assert count("deals") == 3


def test_failure_rolls_back_every_row_and_keeps_preview(monkeypatch):
    batch = imp.preview(user_id=7, text=CSV.decode())["batch_id"]
    real_insert = chatter_service.insert_note_cur
    calls = []

    def fail_second(*args, **kwargs):
        calls.append(args)
        if len(calls) == 2:
            raise RuntimeError("forced mid-import failure")
        return real_insert(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(chatter_service, "insert_note_cur", fail_second)
        with pytest.raises(RuntimeError, match="forced mid-import"):
            imp.execute(batch, note="Met them", user_id=7)
    for table in ("companies", "contacts", "deals", "crm_chatter"):
        assert count(table) == 0, table
    assert count("assistant_lead_import_previews") == 1
    assert imp.execute(batch, note="Met them", user_id=7)["deals_created"] == 3


def test_expiry_is_plain_and_next_preview_sweeps_it():
    batch = imp.preview(user_id=7, text="Name\nAna")["batch_id"]
    pg_execute("UPDATE assistant_lead_import_previews SET expires_at = now() - interval '1 minute'")
    assert imp.execute(batch, user_id=7) == {"error": imp.EXPIRED}
    assert "expired" in imp.EXPIRED
    assert count("deals") == 0
    assert imp.pending_binding({"batch_id": batch}, 7) is None
    imp.preview(user_id=7, text="Name\nBo")
    assert pg_fetchone("SELECT batch_id FROM assistant_lead_import_previews WHERE batch_id = %s", (batch,)) is None
    assert count("assistant_lead_import_previews") == 1


def test_conversation_deletion_cascades_stored_bytes():
    cid = store_file()
    assert count("assistant_message_files") == 1
    assert history.delete_conversation(cid, user_id=7)
    assert count("assistant_message_files") == 0


def test_two_concurrent_executes_import_once():
    batch = imp.preview(user_id=7, text=CSV.decode())["batch_id"]
    ready = Barrier(2)

    def run():
        ready.wait(timeout=10)
        return imp.execute(batch, note="Met them", user_id=7)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run) for _ in range(2)]
        results = [f.result(timeout=20) for f in futures]
    assert sum(r.get("ok", False) for r in results) == 1
    assert {"error": imp.EXPIRED} in results
    assert count("deals") == count("crm_chatter") == 3
    assert count("companies") == 2
    assert count("assistant_lead_import_previews") == 0


def test_execute_rechecks_contacts_and_reuses_email_only():
    text = "Name,Email,Company\nAna,a@example.test,Northwind\nAgain,A@example.test,Northwind\nBo,,\nBo,,\n,,Only Company\n"
    batch = imp.preview(user_id=7, text=text)["batch_id"]
    archived = service.create_contact("Archived", email="a@example.test")
    service.update_contact(archived["id"], status="archived", archive_reason="old")
    existing = service.create_contact("Added after preview", email="a@example.test")
    result = imp.execute(batch, user_id=7)
    assert result["contacts_created"] == 2 and result["contacts_linked"] == 1
    assert result["deals_created"] == 5 and result["notes_created"] == 0
    deals = pg_fetchall("SELECT * FROM deals ORDER BY id")
    assert deals[0]["contact_id"] == deals[1]["contact_id"] == existing["id"]
    assert deals[2]["contact_id"] != deals[3]["contact_id"]
    assert deals[4]["contact_id"] is None
    assert count("crm_chatter") == 1  # only the earlier archive audit


@pytest.mark.parametrize("include_definitions", [False, True])
def test_crm_reset_clears_previews_but_preserves_conversation_files(include_definitions):
    store_file()
    imp.preview(user_id=7, attachment_id="f1")
    with get_connection() as conn:
        service._truncate_all(conn.cursor(), include_definitions=include_definitions)
    assert count("assistant_lead_import_previews") == 0
    assert count("assistant_message_files") == 1
