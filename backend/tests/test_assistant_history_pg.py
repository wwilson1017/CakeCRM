"""Real-Postgres integration for the assistant history — proves what mocks can't:
the migration applies, JSONB round-trips as Python lists (no json.loads), seq
allocation stays unique under concurrency, merge_tool_result is a race-safe
read-modify-write, and claim_pending_tool executes a confirmation exactly once.

Marked ``integration`` and excluded from the default no-DB run (needs TEST_ADMIN_DSN).
"""

import os
import threading
import uuid

import psycopg2
import pytest

pytestmark = pytest.mark.integration

ADMIN_DSN = os.getenv("TEST_ADMIN_DSN", "postgresql://cake:cake_dev@localhost:5432/cake")


@pytest.fixture(scope="module")
def pg_db():
    from core import postgres

    dbname = f"cakecrm_it_asst_{os.getpid()}"
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
    from core.postgres import pg_execute
    pg_execute("TRUNCATE assistant_messages, assistant_conversations RESTART IDENTITY CASCADE")
    yield


def test_migration_tables_and_singleton(pg_db):
    from core.postgres import pg_fetchall, pg_fetchone
    names = {r["table_name"] for r in pg_fetchall(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")}
    assert {"assistant_conversations", "assistant_messages", "assistant_identity"} <= names
    ident = pg_fetchone("SELECT name, personality FROM assistant_identity WHERE id = 1")
    assert ident["name"] == "Baker" and ident["personality"] == ""


def test_jsonb_roundtrips_as_python_lists(pg_db):
    from assistant import history
    conv = history.create_conversation()
    mid = str(uuid.uuid4())
    history.save_message(conv["id"], mid, "assistant", "text",
                        tool_calls=[{"tool": "crm_dashboard", "tool_use_id": "t1", "args": {"a": 1}}])
    history.merge_tool_result(mid, "t1", "crm_dashboard", '{"n": 3}')
    got = history.get_conversation(conv["id"])
    msg = got["messages"][0]
    assert isinstance(msg["tool_calls"], list)  # NOT a JSON string
    assert msg["tool_calls"][0]["args"] == {"a": 1}
    assert isinstance(msg["tool_results"], list)
    assert msg["tool_results"][0]["content"] == '{"n": 3}'


def test_concurrent_seq_allocation_stays_unique(pg_db):
    from assistant import history
    conv = history.create_conversation()
    errors = []

    def _save():
        try:
            history.save_message(conv["id"], str(uuid.uuid4()), "user", "hi")
        except Exception as e:  # a duplicate seq would raise IntegrityError
            errors.append(e)

    threads = [threading.Thread(target=_save) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    got = history.get_conversation(conv["id"])
    seqs = [m["seq"] for m in got["messages"]]
    assert sorted(seqs) == list(range(12))  # unique, contiguous


def test_merge_preserves_siblings(pg_db):
    from assistant import history
    conv = history.create_conversation()
    mid = str(uuid.uuid4())
    history.save_message(conv["id"], mid, "assistant", "",
                        tool_calls=[{"tool": "a", "tool_use_id": "t1", "args": {}},
                                    {"tool": "b", "tool_use_id": "t2", "args": {}}])
    history.merge_tool_result(mid, "t1", "a", '{"r": 1}')
    history.merge_tool_result(mid, "t2", "b", '{"r": 2}')
    msg = history.get_conversation(conv["id"])["messages"][0]
    by_id = {r["tool_use_id"]: r["content"] for r in msg["tool_results"]}
    assert by_id == {"t1": '{"r": 1}', "t2": '{"r": 2}'}


def test_claim_pending_tool_is_once_only(pg_db):
    from assistant import engine, history
    conv = history.create_conversation()
    mid = str(uuid.uuid4())
    history.save_message(conv["id"], mid, "assistant", "",
                        tool_calls=[{"tool": "crm_create_contact", "tool_use_id": "t1", "args": {"name": "Z"}}])
    history.merge_tool_result(mid, "t1", "crm_create_contact", history.PENDING_RESULT_JSON)

    # first claim returns the canonical call; second finds nothing pending
    first = history.claim_pending_tool(conv["id"], "t1")
    assert first["tool"] == "crm_create_contact" and first["args"] == {"name": "Z"}
    assert history.claim_pending_tool(conv["id"], "t1") is None

    # resolve_confirmation via a counting registry proves execute-once
    class R:
        def __init__(self):
            self.n = 0

        def execute_tool_sync(self, name, args):
            self.n += 1
            return {"ok": True}

    # re-arm one pending call, then approve twice
    mid2 = str(uuid.uuid4())
    history.save_message(conv["id"], mid2, "assistant", "",
                        tool_calls=[{"tool": "crm_create_contact", "tool_use_id": "t2", "args": {}}])
    history.merge_tool_result(mid2, "t2", "crm_create_contact", history.PENDING_RESULT_JSON)
    reg = R()
    out1 = engine.resolve_confirmation(reg, conv["id"], "t2", "approve")
    out2 = engine.resolve_confirmation(reg, conv["id"], "t2", "approve")
    assert reg.n == 1  # executed exactly once
    assert out1["result"] == {"ok": True}
    assert out2 == {"status": "already_resolved"}


def test_deny_records_denied_status(pg_db):
    from assistant import engine, history
    conv = history.create_conversation()
    mid = str(uuid.uuid4())
    history.save_message(conv["id"], mid, "assistant", "",
                        tool_calls=[{"tool": "crm_delete_contact", "tool_use_id": "t1", "args": {"contact_id": 1}}])
    history.merge_tool_result(mid, "t1", "crm_delete_contact", history.PENDING_RESULT_JSON)

    class R:
        def execute_tool_sync(self, name, args):
            raise AssertionError("deny must not execute")

    out = engine.resolve_confirmation(R(), conv["id"], "t1", "deny")
    assert out["decision"] == "deny"
    msg = history.get_conversation(conv["id"])["messages"][0]
    assert history.DENIED_STATUS in msg["tool_results"][0]["content"]
