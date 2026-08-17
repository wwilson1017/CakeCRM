"""Real-Postgres integration for the Gmail touch-scan job (issue #17) — proves what
the hermetic FakeCursor tests can't: the migration applies, the ledger's ON CONFLICT
gives true per-message idempotency (a re-scan logs no second touch), the unmatched
suggestion fires exactly once, and — critically — the CRM reset paths STILL SUCCEED
with the ledger present (the no-FK design; an inbound FK would make TRUNCATE reject).

Marked ``integration`` and excluded from the default no-DB run. Throwaway database in
the local dev container (TEST_ADMIN_DSN), same pattern as test_integration_crm_pg.py.
Only gmail.store + gmail.client are mocked; every DB write hits the real database.
"""

import os

import psycopg2
import pytest

from gmail_scan import service as gs

pytestmark = pytest.mark.integration

ADMIN_DSN = os.getenv("TEST_ADMIN_DSN", "postgresql://cake:cake_dev@localhost:5432/cake")


@pytest.fixture(scope="module")
def pg_db():
    from core import postgres

    dbname = f"cakecrm_it_gmailscan_{os.getpid()}"
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
def _clean(pg_db):
    from core.postgres import pg_execute
    pg_execute("TRUNCATE companies, contacts, deals, activity_log, tasks, crm_chatter, "
               "crm_field_values, crm_field_provenance, deal_stage_events RESTART IDENTITY")
    pg_execute("TRUNCATE gmail_scanned_messages, gmail_unmatched_correspondents")
    pg_execute("UPDATE gmail_scan_state SET last_scan_at = NULL, last_status = '', "
               "last_error = '', last_messages_seen = 0, last_messages_new = 0, "
               "last_touches_logged = 0 WHERE id = 1")
    pg_execute("DELETE FROM alerts WHERE source = 'gmail_touch_scan'")
    yield


@pytest.fixture(autouse=True)
def _connected(monkeypatch):
    monkeypatch.setattr(gs.store, "is_connected", lambda *a, **k: True)
    monkeypatch.setattr(gs.store, "get_row", lambda *a, **k: {"email": "me@own.com"})


def _reset_due():
    """Simulate the scan interval elapsing so the next run re-claims (proves the LEDGER,
    not the cadence throttle, provides idempotency)."""
    from core.postgres import pg_execute
    pg_execute("UPDATE gmail_scan_state SET last_scan_at = NULL WHERE id = 1")


def _seed_contact(email, name="Person"):
    from core.postgres import pg_fetchone
    return pg_fetchone("INSERT INTO contacts (name, email) VALUES (%s, %s) RETURNING id",
                       (name, email))["id"]


def _seed_deal(contact_id, stage="lead"):
    from core.postgres import pg_fetchone
    return pg_fetchone("INSERT INTO deals (contact_id, title, stage) VALUES (%s, %s, %s) RETURNING id",
                       (contact_id, "Deal", stage))["id"]


# ── migration ────────────────────────────────────────────────────────────────

def test_migration_created_tables_and_singleton(pg_db):
    from core.postgres import pg_fetchall, pg_fetchone
    names = {r["table_name"] for r in pg_fetchall(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")}
    assert {"gmail_scan_state", "gmail_scanned_messages",
            "gmail_unmatched_correspondents"} <= names
    assert pg_fetchone("SELECT id FROM gmail_scan_state WHERE id = 1") is not None
    idx = pg_fetchall("SELECT indexname FROM pg_indexes WHERE tablename = 'contacts'")
    assert any(r["indexname"] == "idx_contacts_email_lower" for r in idx)


# ── idempotency (the load-bearing requirement) ──────────────────────────────

def test_double_scan_logs_exactly_one_touch(pg_db, monkeypatch):
    from core.postgres import pg_fetchall, pg_fetchone
    cid = _seed_contact("dana@client.com", "Dana")
    did = _seed_deal(cid)
    msg = {"id": "gmail-msg-1", "from": "Dana <Dana@Client.com>", "subject": "Following up",
           "date": ""}
    monkeypatch.setattr(gs, "call_gmail", lambda *a, **k: [msg])

    first = gs.run_scan_if_due()
    assert first["status"] == "ok" and first["logged"] == 1 and first["new"] == 1
    after_first = pg_fetchone("SELECT last_status, last_touches_logged FROM gmail_scan_state WHERE id = 1")
    assert after_first["last_status"] == "ok" and after_first["last_touches_logged"] == 1

    _reset_due()
    second = gs.run_scan_if_due()
    assert second["status"] == "ok" and second["new"] == 0 and second["logged"] == 0
    after_second = pg_fetchone("SELECT last_touches_logged FROM gmail_scan_state WHERE id = 1")
    assert after_second["last_touches_logged"] == 0          # dedup pass logged nothing

    acts = pg_fetchall("SELECT activity, contact_id, deal_id FROM activity_log")
    assert len(acts) == 1                                    # exactly ONE touch, not two
    assert acts[0]["activity"] == "email" and acts[0]["contact_id"] == cid
    assert acts[0]["deal_id"] == did                          # single open deal → attributed
    ledger = pg_fetchall("SELECT outcome FROM gmail_scanned_messages")
    assert len(ledger) == 1 and ledger[0]["outcome"] == "logged"


def test_multiple_open_deals_logs_contact_only(pg_db, monkeypatch):
    from core.postgres import pg_fetchall
    cid = _seed_contact("multi@client.com")
    _seed_deal(cid, "lead")
    _seed_deal(cid, "proposal")                               # two open → ambiguous
    monkeypatch.setattr(gs, "call_gmail",
                        lambda *a, **k: [{"id": "m-multi", "from": "multi@client.com",
                                          "subject": "x", "date": ""}])
    gs.run_scan_if_due()
    acts = pg_fetchall("SELECT contact_id, deal_id FROM activity_log")
    assert len(acts) == 1 and acts[0]["contact_id"] == cid
    assert acts[0]["deal_id"] is None                         # no fabrication


# ── unmatched suggestion fires exactly once ─────────────────────────────────

def test_unmatched_frequent_correspondent_alerts_once(pg_db, monkeypatch):
    from core.postgres import pg_fetchall, pg_fetchone
    msgs = [{"id": f"u{i}", "from": "Stranger <stranger@x.com>", "subject": "hi", "date": ""}
            for i in range(3)]
    monkeypatch.setattr(gs, "call_gmail", lambda *a, **k: msgs)
    gs.run_scan_if_due()

    row = pg_fetchone("SELECT message_count, alerted_at FROM gmail_unmatched_correspondents "
                      "WHERE email = 'stranger@x.com'")
    assert row["message_count"] == 3 and row["alerted_at"] is not None
    active = pg_fetchall("SELECT source_id FROM alerts WHERE source = 'gmail_touch_scan' "
                         "AND status = 'active'")
    assert len(active) == 1 and active[0]["source_id"] == "stranger@x.com"
    assert pg_fetchall("SELECT id FROM activity_log") == []   # unmatched → no touch logged

    # A 4th message must NOT raise a second alert (fires at most once).
    _reset_due()
    monkeypatch.setattr(gs, "call_gmail",
                        lambda *a, **k: [{"id": "u4", "from": "stranger@x.com",
                                          "subject": "again", "date": ""}])
    gs.run_scan_if_due()
    row = pg_fetchone("SELECT message_count FROM gmail_unmatched_correspondents "
                      "WHERE email = 'stranger@x.com'")
    assert row["message_count"] == 4
    active = pg_fetchall("SELECT id FROM alerts WHERE source = 'gmail_touch_scan' AND status = 'active'")
    assert len(active) == 1                                    # still exactly one


# ── CRM reset compatibility (validates the no-FK ledger design, F1) ─────────

def test_clear_all_succeeds_with_ledger_present(pg_db, monkeypatch):
    from core.postgres import pg_fetchall
    from crm import service as crm_service
    cid = _seed_contact("dana@client.com", "Dana")
    _seed_deal(cid)
    monkeypatch.setattr(gs, "call_gmail",
                        lambda *a, **k: [{"id": "gmail-msg-x", "from": "dana@client.com",
                                          "subject": "y", "date": ""}])
    gs.run_scan_if_due()
    assert pg_fetchall("SELECT message_id FROM gmail_scanned_messages")   # ledger populated

    # The pinned TRUNCATE would raise if the ledger held an inbound FK to contacts/
    # deals/activity_log. With plain-INTEGER audit columns it must succeed.
    crm_service.clear_all()

    assert pg_fetchall("SELECT id FROM contacts") == []       # CRM wiped
    assert pg_fetchall("SELECT message_id FROM gmail_scanned_messages")   # ledger survives (accepted)


def test_crash_mid_transaction_rolls_back_and_self_heals(pg_db, monkeypatch):
    # The core self-heal guarantee: a raise INSIDE the per-message transaction (after the
    # ledger claim) rolls the claim back, so the message is neither logged nor marked
    # scanned — and the next pass reprocesses it successfully.
    from core.postgres import pg_fetchall
    cid = _seed_contact("heal@client.com", "Heal")
    _seed_deal(cid)
    monkeypatch.setattr(gs, "call_gmail",
                        lambda *a, **k: [{"id": "heal-1", "from": "heal@client.com",
                                          "subject": "z", "date": ""}])

    original_build_note = gs._build_note

    def boom(*a, **k):
        raise RuntimeError("crash mid-transaction")   # fires after the claim, before commit

    monkeypatch.setattr(gs, "_build_note", boom)
    gs.run_scan_if_due()
    # Claim rolled back with the txn → message absent, nothing logged.
    assert pg_fetchall("SELECT message_id FROM gmail_scanned_messages") == []
    assert pg_fetchall("SELECT id FROM activity_log") == []

    # Recover: the same message self-heals on the next pass.
    monkeypatch.setattr(gs, "_build_note", original_build_note)
    _reset_due()
    gs.run_scan_if_due()
    acts = pg_fetchall("SELECT contact_id FROM activity_log")
    assert len(acts) == 1 and acts[0]["contact_id"] == cid
    ledger = pg_fetchall("SELECT outcome FROM gmail_scanned_messages")
    assert len(ledger) == 1 and ledger[0]["outcome"] == "logged"
