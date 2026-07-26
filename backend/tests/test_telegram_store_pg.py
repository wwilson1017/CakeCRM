"""Real-Postgres integration for the Telegram store — proves what mocks can't:
the migration applies, the bot token survives an encrypt→store→decrypt round-trip, the
link code is single-use, ``advance_offset`` is truly monotonic (GREATEST), a deleted
conversation is recreated (nullable FK ON DELETE SET NULL), and the confirmation-batch
gate reads real ``assistant_messages.tool_results`` in one transaction.

Marked ``integration`` and excluded from the default no-DB run (per the repo's testing
rules — never skip/xfail; the lane that provisions PostgreSQL runs it).
"""

import os

import psycopg2
import pytest

pytestmark = pytest.mark.integration

ADMIN_DSN = os.getenv("TEST_ADMIN_DSN", "postgresql://cake:cake_dev@localhost:5432/cake")


@pytest.fixture(scope="module")
def pg_db():
    from core import postgres

    dbname = f"cakecrm_tg_it_{os.getpid()}"
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
    pg_execute("DELETE FROM assistant_conversations")  # cascades to messages
    pg_execute(
        "UPDATE telegram_settings SET bot_token_enc='', bot_username='', linked_chat_id='', "
        "linked_user_id='', linked_name='', link_code='', conversation_id=NULL, "
        "pending_msg_id='', poll_offset=0 WHERE id=1"
    )
    yield


def test_migration_created_telegram_settings(pg_db):
    from core.postgres import pg_fetchone
    row = pg_fetchone("SELECT id FROM telegram_settings WHERE id = 1")
    assert row is not None and row["id"] == 1


def test_connect_roundtrip_and_offset_monotonic(pg_db):
    from telegram import store
    code = store.connect("123456:REAL-TOKEN", "acmebot")
    assert code and store.get_bot_token() == "123456:REAL-TOKEN"
    assert store.get_offset() == 0  # reset on connect

    store.advance_offset(10)
    assert store.get_offset() == 10
    store.advance_offset(5)                 # a lower value must NOT move it back
    assert store.get_offset() == 10
    store.advance_offset(11)
    assert store.get_offset() == 11


def test_link_code_is_single_use(pg_db):
    from telegram import store
    code = store.connect("t", "bot")
    assert store.link_chat(code, "chat9", "user9", "Sam") is True
    s = store.get_settings()
    assert s["linked"] and s["linked_user_id"] == "user9"
    # The same code cannot be replayed by another device.
    assert store.link_chat(code, "chatOTHER", "userOTHER", "Mallory") is False


def test_deleted_conversation_is_recreated(pg_db):
    from assistant import history
    from telegram import store
    store.connect("t", "bot")
    conv1 = store.get_or_create_conversation()
    assert store.get_or_create_conversation() == conv1  # stable while it exists
    history.delete_conversation(conv1)                  # FK → conversation_id SET NULL
    conv2 = store.get_or_create_conversation()
    assert conv2 and conv2 != conv1
    assert history.conversation_exists(conv2)


def test_batch_gate_waits_then_continues(pg_db):
    from assistant import history
    from telegram import store
    store.connect("t", "bot")
    conv = store.get_or_create_conversation()

    # A single assistant iteration with two write tool calls, both pending.
    msg_id = "msg-batch-1"
    history.save_message(
        conv, msg_id, "assistant", "doing two things", tool_calls=[
            {"tool": "crm_create_task", "tool_use_id": "tu1", "args": {}},
            {"tool": "crm_update_deal", "tool_use_id": "tu2", "args": {}},
        ],
    )
    history.merge_tool_result(msg_id, "tu1", "crm_create_task", history.PENDING_RESULT_JSON)
    history.merge_tool_result(msg_id, "tu2", "crm_update_deal", history.PENDING_RESULT_JSON)
    store.set_pending_msg(msg_id)

    assert history.list_pending_tool_uses(conv, msg_id) == ["tu1", "tu2"]

    # Resolve the first — batch NOT done yet.
    history.merge_tool_result(msg_id, "tu1", "crm_create_task", '{"ok": true}')
    assert store.try_consume_batch(msg_id) is False

    # Resolve the second — batch done → continue exactly once, pending cleared.
    history.merge_tool_result(msg_id, "tu2", "crm_update_deal", history.DENIED_RESULT_JSON)
    assert store.try_consume_batch(msg_id) is True
    assert store.get_settings()["pending_msg_id"] == ""
    # A repeat press after consumption does not re-continue.
    assert store.try_consume_batch(msg_id) is False
