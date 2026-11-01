"""Real migrations, persisted finds, conversation cascade and both CRM reset branches."""

import os

import psycopg2
import pytest

pytestmark = pytest.mark.integration
ADMIN_DSN = os.getenv("TEST_ADMIN_DSN", "postgresql://cake:cake_dev@localhost:5432/cake")


@pytest.fixture(scope="module")
def pg_db():
    from core import postgres

    dbname = f"cakecrm_it_dealfinds_{os.getpid()}"
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
        cur.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
    admin.close()


@pytest.mark.parametrize("include_definitions", [False, True])
def test_finds_persist_isolate_warn_cascade_and_reset(pg_db, include_definitions):
    from assistant import history
    from assistant.registry import ToolRegistry
    from core.postgres import get_connection, pg_execute, pg_fetchall
    from crm import service

    conversation = history.create_conversation(user_id=None)["id"]
    other = history.create_conversation(user_id=None)["id"]
    deal = service.create_deal(title="Example renewal")
    args = {"deal_id": deal["id"]}
    first = ToolRegistry()
    first.conversation_id = conversation
    # Two registries may load the same empty set before either search returns.
    contender = ToolRegistry()
    contender.conversation_id = conversation
    assert contender.deal_write_refusal("crm_update_deal", args)
    assert "error" not in first.execute_tool_sync("crm_get_deal", args)
    assert "error" not in contender.execute_tool_sync("crm_get_deal", args)
    rows = pg_fetchall("SELECT deal_id FROM assistant_deal_finds WHERE conversation_id = %s ORDER BY deal_id",
                       (conversation,))
    assert rows == [{"deal_id": deal["id"]}]
    # A second read/registry cannot duplicate the set entry.
    second = ToolRegistry()
    second.conversation_id = conversation
    assert "error" not in second.execute_tool_sync("crm_update_deal", {**args, "title": "Updated renewal"})
    assert "error" not in second.execute_tool_sync("crm_get_deal", args)
    third = ToolRegistry()
    third.conversation_id = other
    assert third.execute_tool_sync("crm_update_deal", args)["ok"] is False
    service.update_deal(deal["id"], stage="lost")
    assert "lost" in second.execute_tool_sync("crm_update_deal", {**args, "title": "Closed renewal"})["closed_deal_warning"]
    pg_execute("DELETE FROM assistant_conversations WHERE id = %s", (conversation,))
    assert pg_fetchall("SELECT * FROM assistant_deal_finds") == []
    third.execute_tool_sync("crm_get_deal", args)
    assert pg_fetchall("SELECT * FROM assistant_deal_finds")
    with get_connection() as conn, conn.cursor() as cur:
        service._truncate_all(cur, include_definitions=include_definitions)
    assert pg_fetchall("SELECT * FROM assistant_deal_finds") == []
    replacement = service.create_deal(title="Replacement renewal")
    fresh = ToolRegistry()
    fresh.conversation_id = other
    assert fresh.execute_tool_sync("crm_update_deal", {"deal_id": replacement["id"]})["ok"] is False
