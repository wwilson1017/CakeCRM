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
    conv = history.create_conversation(user_id=None)
    mid = str(uuid.uuid4())
    history.save_message(conv["id"], mid, "assistant", "text",
                        tool_calls=[{"tool": "crm_dashboard", "tool_use_id": "t1", "args": {"a": 1}}])
    history.merge_tool_result(mid, "t1", "crm_dashboard", '{"n": 3}')
    got = history.get_conversation(conv["id"], user_id=None)
    msg = got["messages"][0]
    assert isinstance(msg["tool_calls"], list)  # NOT a JSON string
    assert msg["tool_calls"][0]["args"] == {"a": 1}
    assert isinstance(msg["tool_results"], list)
    assert msg["tool_results"][0]["content"] == '{"n": 3}'


def test_concurrent_seq_allocation_stays_unique(pg_db):
    from assistant import history
    conv = history.create_conversation(user_id=None)
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
    got = history.get_conversation(conv["id"], user_id=None)
    seqs = [m["seq"] for m in got["messages"]]
    assert sorted(seqs) == list(range(12))  # unique, contiguous


def test_merge_preserves_siblings(pg_db):
    from assistant import history
    conv = history.create_conversation(user_id=None)
    mid = str(uuid.uuid4())
    history.save_message(conv["id"], mid, "assistant", "",
                        tool_calls=[{"tool": "a", "tool_use_id": "t1", "args": {}},
                                    {"tool": "b", "tool_use_id": "t2", "args": {}}])
    history.merge_tool_result(mid, "t1", "a", '{"r": 1}')
    history.merge_tool_result(mid, "t2", "b", '{"r": 2}')
    msg = history.get_conversation(conv["id"], user_id=None)["messages"][0]
    by_id = {r["tool_use_id"]: r["content"] for r in msg["tool_results"]}
    assert by_id == {"t1": '{"r": 1}', "t2": '{"r": 2}'}


def test_claim_pending_tool_is_once_only(pg_db):
    from assistant import engine, history
    conv = history.create_conversation(user_id=None)
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
    out1 = engine.resolve_confirmation(reg, conv["id"], "t2", "approve", user=None)
    out2 = engine.resolve_confirmation(reg, conv["id"], "t2", "approve", user=None)
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
    conv = history.create_conversation(user_id=None)
    m1 = str(uuid.uuid4())
    history.save_message(conv["id"], m1, "assistant", "",
                        tool_calls=[{"tool": "crm_create_contact", "tool_use_id": "t1", "args": {"name": "Z"}}])
    history.merge_tool_result(m1, "t1", "crm_create_contact", '{"ok": true}')
    history.save_message(conv["id"], str(uuid.uuid4()), "assistant", "Shall I create Z?")

    assembled = assembly.assemble_messages(_AsmProvider(), conv["id"])
    assert assembled[-1]["role"] == "assistant"  # the bug scenario the engine ack guards


def test_deny_records_denied_status(pg_db):
    from assistant import engine, history
    conv = history.create_conversation(user_id=None)
    mid = str(uuid.uuid4())
    history.save_message(conv["id"], mid, "assistant", "",
                        tool_calls=[{"tool": "crm_delete_contact", "tool_use_id": "t1", "args": {"contact_id": 1}}])
    history.merge_tool_result(mid, "t1", "crm_delete_contact", history.PENDING_RESULT_JSON)

    class R:
        def execute_tool_sync(self, name, args):
            raise AssertionError("deny must not execute")

    out = engine.resolve_confirmation(R(), conv["id"], "t1", "deny", user=None)
    assert out["decision"] == "deny"
    msg = history.get_conversation(conv["id"], user_id=None)["messages"][0]
    assert history.DENIED_STATUS in msg["tool_results"][0]["content"]


# ── Conversations are owner-only (issue #191) ─────────────────────────────────
# The hermetic tests pin the SQL text; these prove the column, the index, the migration's
# legacy claim and the FK's delete behaviour against a real Postgres, which is the only
# place a wrong ON DELETE rule or a mis-ordered migration shows up.

@pytest.fixture
def seats(pg_db):
    """Two real users, cleaned up afterwards (the DB is module-scoped)."""
    from core.postgres import pg_execute
    from users import service as users_service

    pg_execute("DELETE FROM users")
    ada = users_service.create_user("ada@example.com", "Ada", "pw-ada-123456", role="admin")
    bo = users_service.create_user("bo@example.com", "Bo", "pw-bo-123456", role="member")
    yield ada, bo
    pg_execute("DELETE FROM users")


def test_migration_added_the_owner_column_and_its_index(pg_db):
    from core.postgres import pg_fetchall, pg_fetchone
    col = pg_fetchone(
        "SELECT data_type, is_nullable FROM information_schema.columns "
        "WHERE table_name = 'assistant_conversations' AND column_name = 'user_id'"
    )
    # Nullable on purpose: a NULL owner is invisible to every seat, the fail-safe way.
    assert col is not None and col["is_nullable"] == "YES"
    idx = {r["indexname"] for r in pg_fetchall(
        "SELECT indexname FROM pg_indexes WHERE tablename = 'assistant_conversations'")}
    assert "idx_assistant_conv_user_updated" in idx


def test_the_owner_fk_cascades_rather_than_orphaning(pg_db, seats):
    """Personal data follows its person — the totp_config idiom, not owner_id's SET NULL.

    SET NULL would be the worse failure: a departed seat's private threads would become
    unowned rows that survive forever, and (per Decision 1c's reasoning) the next
    migration to claim unowned rows would hand them to an admin.
    """
    from assistant import history
    from core.postgres import pg_execute, pg_fetchone
    ada, bo = seats
    gone = history.create_conversation(user_id=ada["id"])
    kept = history.create_conversation(user_id=bo["id"])

    pg_execute("DELETE FROM users WHERE id = %s", (ada["id"],))
    assert pg_fetchone(
        "SELECT 1 AS ok FROM assistant_conversations WHERE id = %s", (gone["id"],)) is None
    # Only theirs: a cascade that took the whole table would also pass a weaker test.
    assert pg_fetchone(
        "SELECT 1 AS ok FROM assistant_conversations WHERE id = %s", (kept["id"],)) is not None


def test_every_read_and_write_path_is_scoped_to_the_owner(pg_db, seats):
    from assistant import history
    ada, bo = seats
    mine = history.create_conversation(user_id=ada["id"])
    theirs = history.create_conversation(user_id=bo["id"])
    history.save_message(mine["id"], "m-ada", "user", "my private note")
    history.save_message(theirs["id"], "m-bo", "user", "bo's private note")

    # List: each seat sees only their own thread.
    assert [c["id"] for c in history.list_conversations(user_id=ada["id"])] == [mine["id"]]
    assert [c["id"] for c in history.list_conversations(user_id=bo["id"])] == [theirs["id"]]

    # Get: a foreign conversation is None — the same answer a missing one gives, so the
    # route's 404 carries no information about whether it exists.
    assert history.get_conversation(mine["id"], user_id=ada["id"])["id"] == mine["id"]
    assert history.get_conversation(theirs["id"], user_id=ada["id"]) is None
    assert history.get_conversation("no-such-id", user_id=ada["id"]) is None

    # Exists (the engine's resume check): scoped the same way.
    assert history.conversation_exists(mine["id"], user_id=ada["id"]) is True
    assert history.conversation_exists(theirs["id"], user_id=ada["id"]) is False

    # Rename and delete are refusals, not silent successes on someone else's row.
    assert history.rename_conversation(theirs["id"], "hijacked", user_id=ada["id"]) is None
    assert history.delete_conversation(theirs["id"], user_id=ada["id"]) is False
    assert history.get_conversation(theirs["id"], user_id=bo["id"])["title"] != "hijacked"

    # And the owner's own operations still work.
    assert history.rename_conversation(mine["id"], "My thread", user_id=ada["id"]) == "My thread"
    assert history.delete_conversation(mine["id"], user_id=ada["id"]) is True


def test_a_null_owner_row_is_invisible_to_every_seat(pg_db, seats):
    """The fail-safe direction: an unowned row (a legacy install with no admin at all,
    or an unattended turn) is unreachable rather than readable by anyone."""
    from assistant import history
    ada, _ = seats
    orphan = history.create_conversation(user_id=None)
    assert history.get_conversation(orphan["id"], user_id=ada["id"]) is None
    assert history.conversation_exists(orphan["id"], user_id=ada["id"]) is False
    assert [c["id"] for c in history.list_conversations(user_id=ada["id"])] == []
    # ... but a trusted internal caller still reaches it.
    assert history.conversation_exists(orphan["id"], user_id=None) is True


def _run_claim_migration():
    """Re-run just M1's legacy-claim UPDATE against the current database."""
    from pathlib import Path

    from core.postgres import pg_execute
    sql = (Path(__file__).resolve().parent.parent / "migrations"
           / "20260917173429_assistant_conversations_owner.sql").read_text()
    # The ALTER/CREATE INDEX halves are IF NOT EXISTS, so the whole file is re-runnable.
    return pg_execute(sql)


def test_the_legacy_claim_picks_the_earliest_admin(pg_db):
    """An UPGRADE: pre-#191 rows have no owner and must land on the install's owner.

    Seeded as a genuinely unowned row, because on a fresh database every assertion here
    would pass against a migration that was mistyped or deleted outright — the table
    would simply be empty. Seeding the pre-migration state is the only thing that can
    tell a working claim from an absent one.
    """
    from assistant import history
    from core.postgres import pg_execute, pg_fetchone
    from users import service as users_service
    pg_execute("DELETE FROM users")
    first = users_service.create_user("first@example.com", "First", "pw-first-12345", role="admin")
    users_service.create_user("later@example.com", "Later", "pw-later-12345", role="admin")
    users_service.create_user("member@example.com", "Member", "pw-member-1234", role="member")
    try:
        conv = history.create_conversation(user_id=None)
        _run_claim_migration()
        row = pg_fetchone(
            "SELECT user_id FROM assistant_conversations WHERE id = %s", (conv["id"],))
        assert row["user_id"] == first["id"]

        # Idempotent: a second run claims nothing further and moves nobody's rows.
        _run_claim_migration()
        assert pg_fetchone(
            "SELECT user_id FROM assistant_conversations WHERE id = %s",
            (conv["id"],))["user_id"] == first["id"]
    finally:
        pg_execute("DELETE FROM users")


def test_the_legacy_claim_is_a_no_op_with_no_admin(pg_db):
    """A FRESH install runs this migration before the bootstrap admin exists.

    The MIN(id) subquery is then NULL. The row count is what makes that safe — on a fresh
    install the table is empty too — so the only way to reach a NULL owner is a database
    that has conversations but no admin, and those rows stay invisible rather than being
    handed to an arbitrary member.
    """
    from assistant import history
    from core.postgres import pg_execute, pg_fetchone
    from users import service as users_service
    pg_execute("DELETE FROM users")
    assert users_service.earliest_admin_id() is None
    conv = history.create_conversation(user_id=None)
    _run_claim_migration()
    assert pg_fetchone(
        "SELECT user_id FROM assistant_conversations WHERE id = %s",
        (conv["id"],))["user_id"] is None

    # A members-only install is the same case: the claim is admin-scoped by design.
    member = users_service.create_user("solo@example.com", "Solo", "pw-solo-123456", role="member")
    try:
        _run_claim_migration()
        assert pg_fetchone(
            "SELECT user_id FROM assistant_conversations WHERE id = %s",
            (conv["id"],))["user_id"] is None
        assert member["role"] == "member"
    finally:
        pg_execute("DELETE FROM users")
