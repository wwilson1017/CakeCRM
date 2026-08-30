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


def test_name_brand_migration_resets_a_renamed_assistant(pg_db):
    """#71's one-shot reset, on its own throwaway DB (same pattern as the #35 backfill
    test). It has to be proved on a RENAMED row: on a fresh database the table's own
    ``DEFAULT 'Baker'`` already satisfies every other assertion in this file, so a
    migration that was mistyped, inverted, or deleted outright would look identical to
    one that worked. Seeding the pre-#71 state is the only thing that can tell them
    apart.

    The application never reads this column any more, so this test guards the ROLLBACK
    contract specifically: a pre-#71 binary does `SELECT name`, and what it finds must
    still be the brand.
    """
    from pathlib import Path

    import psycopg2 as _pg

    migrations = Path(__file__).resolve().parent.parent / "migrations"
    reset_sql = (migrations / "20260826010825_assistant_name_is_a_brand.sql").read_text()

    conn = _pg.connect(pg_db)
    conn.autocommit = True
    try:
        cur = conn.cursor()
        cur.execute("UPDATE assistant_identity SET name = 'Ace' WHERE id = 1")
        cur.execute("SELECT name FROM assistant_identity WHERE id = 1")
        assert cur.fetchone()[0] == "Ace"  # the pre-#71 state really is in place

        cur.execute(reset_sql)
        cur.execute("SELECT name FROM assistant_identity WHERE id = 1")
        assert cur.fetchone()[0] == "Baker"

        # Idempotent, and a no-op second run touches nothing.
        cur.execute(reset_sql)
        assert cur.rowcount == 0
        cur.execute("SELECT name FROM assistant_identity WHERE id = 1")
        assert cur.fetchone()[0] == "Baker"
    finally:
        # The DB is module-scoped and _clean only truncates the message tables, so an
        # assertion failure above must not leave 'Ace' behind for the next test.
        with conn.cursor() as c:
            c.execute("UPDATE assistant_identity SET name = 'Baker' WHERE id = 1")
        conn.close()


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

        def is_write(self, name):
            return True

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
    assert out2["status"] == "already_resolved"
    assert out2["result"] == {"ok": True}  # reports the canonical persisted outcome


class _AsmProvider:
    """Minimal provider for the assembler: Anthropic-style build_tool_turn."""

    context_window = None
    model = "fake"

    def build_tool_turn(self, text, tool_calls, results):
        msgs = [{"role": "assistant", "content": text or "(tool)"}]
        msgs.append({"role": "user", "content": "tool_result"})
        return msgs


def test_real_assemble_ends_on_assistant_after_pending_wrapup(pg_db):
    """Reproduces the resume scenario against real Postgres: an assistant tool row
    (result resolved) followed by the persisted wrap-up narration row makes the
    assembled sequence END ON AN ASSISTANT TURN — which is exactly why the engine
    appends a user ack before resuming."""
    from assistant import assembly, history
    conv = history.create_conversation()
    m1 = str(uuid.uuid4())
    history.save_message(conv["id"], m1, "assistant", "",
                        tool_calls=[{"tool": "crm_create_contact", "tool_use_id": "t1", "args": {"name": "Z"}}])
    history.merge_tool_result(m1, "t1", "crm_create_contact", '{"ok": true}')
    history.save_message(conv["id"], str(uuid.uuid4()), "assistant", "Shall I create Z?")

    assembled = assembly.assemble_messages(_AsmProvider(), conv["id"])
    assert assembled[-1]["role"] == "assistant"  # the bug scenario the engine ack guards


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
