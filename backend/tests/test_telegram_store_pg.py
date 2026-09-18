"""Real-Postgres integration for the Telegram store — proves what mocks can't.

The migration applies; the bot token survives an encrypt→store→decrypt round-trip;
``advance_offset`` is truly monotonic (GREATEST); and — since #193 — the properties that
live in the SCHEMA rather than in Python: the partial UNIQUE on ``chat_id`` arbitrating
two codes racing for one chat, the ``ON DELETE CASCADE`` that kills a deleted seat's
link, a per-link conversation owned by that seat (nullable FK, recreated when deleted),
and two seats' confirmation batches settling independently instead of through one mutex.

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
    pg_execute("DELETE FROM telegram_links")       # cascades from users too
    pg_execute("DELETE FROM users")
    pg_execute("DELETE FROM assistant_conversations")  # cascades to messages
    pg_execute(
        "UPDATE telegram_settings SET bot_token_enc='', bot_username='', poll_offset=0 "
        "WHERE id=1"
    )
    yield


@pytest.fixture
def seats():
    """Two active seats plus one deactivated, the shapes every link test needs."""
    from users import service as users_service

    admin = users_service.create_user("owner@example.com", "Owner", "pw-owner-123", role="admin")
    member = users_service.create_user("member@example.com", "Member", "pw-member-12", role="member")
    retired = users_service.create_user("gone@example.com", "Gone", "pw-gone-1234", role="member")
    users_service.update_user(retired["id"], is_active=False)
    return {"admin": admin, "member": member, "retired": retired}


# ── Install-wide bot config ─────────────────────────────────────────────────

def test_migration_created_both_tables(pg_db):
    from core.postgres import pg_fetchone
    assert pg_fetchone("SELECT id FROM telegram_settings WHERE id = 1")["id"] == 1
    # Fresh install: the links table exists and is empty (no claim is possible).
    assert pg_fetchone("SELECT COUNT(*) AS n FROM telegram_links")["n"] == 0


def test_connect_roundtrip_and_offset_monotonic(pg_db):
    from telegram import store
    store.connect("123456:REAL-TOKEN", "acmebot")
    assert store.get_bot_token() == "123456:REAL-TOKEN"
    assert store.get_offset() == 0  # reset on connect

    store.advance_offset(10)
    assert store.get_offset() == 10
    store.advance_offset(5)                 # a lower value must NOT move it back
    assert store.get_offset() == 10
    store.advance_offset(11)
    assert store.get_offset() == 11


def test_connect_and_disconnect_clear_every_seats_binding(pg_db, seats):
    """A new or removed bot invalidates every chat id — they are per (user, bot) pair."""
    from core.postgres import pg_fetchone
    from telegram import store

    store.connect("t", "bot")
    code = store.mint_link_code(seats["member"]["id"])
    assert store.claim_link(code, "chat1", "tg1", "Alex") == seats["member"]["id"]
    assert store.get_link(seats["member"]["id"])["chat_id"] == "chat1"

    store.connect("t2", "bot2")  # bot swap
    link = store.get_link(seats["member"]["id"])
    assert link["chat_id"] == "" and link["link_code"] == ""
    # The row itself survives, and so does the seat's own assistant thread.
    assert pg_fetchone("SELECT COUNT(*) AS n FROM telegram_links")["n"] == 1

    code = store.mint_link_code(seats["member"]["id"])
    store.claim_link(code, "chat1", "tg1", "Alex")
    store.disconnect()
    assert store.get_link(seats["member"]["id"])["chat_id"] == ""


# ── Claiming a link ─────────────────────────────────────────────────────────

def test_link_code_is_single_use(pg_db, seats):
    from telegram import store
    store.connect("t", "bot")
    code = store.mint_link_code(seats["member"]["id"])
    assert store.claim_link(code, "chat9", "tg9", "Sam") == seats["member"]["id"]
    link = store.get_link(seats["member"]["id"])
    assert link["chat_id"] == "chat9" and link["telegram_user_id"] == "tg9"
    assert link["link_code"] == ""
    # The same code cannot be replayed by another device.
    assert store.claim_link(code, "chatOTHER", "tgOTHER", "Mallory") is None


def test_one_chat_maps_to_exactly_one_seat(pg_db, seats):
    """The partial UNIQUE on chat_id: a second seat's code cannot steal a bound chat.

    Without it, inbound dispatch would be ambiguous — two rows would match one chat.
    """
    from telegram import store
    store.connect("t", "bot")
    a = store.mint_link_code(seats["admin"]["id"])
    b = store.mint_link_code(seats["member"]["id"])
    assert store.claim_link(a, "chatShared", "tgA", "A") == seats["admin"]["id"]
    # Refused, not stolen — and the loser's code survives for a retry elsewhere.
    assert store.claim_link(b, "chatShared", "tgB", "B") is None
    assert store.get_link(seats["member"]["id"])["link_code"] == b
    assert store.claim_link(b, "chatOwn", "tgB", "B") == seats["member"]["id"]


def test_two_seats_may_each_hold_an_unbound_row(pg_db, seats):
    """The partial index must not collide on the '' unbound state."""
    from telegram import store
    store.mint_link_code(seats["admin"]["id"])
    store.mint_link_code(seats["member"]["id"])
    store.mint_link_code(seats["retired"]["id"])
    from core.postgres import pg_fetchone
    assert pg_fetchone("SELECT COUNT(*) AS n FROM telegram_links")["n"] == 3


def test_minting_again_frees_the_old_device(pg_db, seats):
    from telegram import store
    store.connect("t", "bot")
    code = store.mint_link_code(seats["member"]["id"])
    store.claim_link(code, "chatOld", "tgOld", "Old phone")
    fresh = store.mint_link_code(seats["member"]["id"])
    assert store.get_link(seats["member"]["id"])["chat_id"] == ""
    # The freed chat is claimable again — by this seat or another.
    assert store.claim_link(fresh, "chatOld", "tgNew", "New phone") == seats["member"]["id"]


def test_a_deactivated_seats_code_is_dead(pg_db, seats):
    from telegram import store
    store.connect("t", "bot")
    code = store.mint_link_code(seats["retired"]["id"])
    assert store.claim_link(code, "chatX", "tgX", "Gone") is None


def test_deleting_a_seat_kills_its_link(pg_db, seats):
    """ON DELETE CASCADE — a chat binding is personal data and follows its person."""
    from core.postgres import pg_execute, pg_fetchone
    from telegram import store

    store.connect("t", "bot")
    code = store.mint_link_code(seats["member"]["id"])
    store.claim_link(code, "chatGone", "tgGone", "Bye")
    pg_execute("DELETE FROM users WHERE id = %s", (seats["member"]["id"],))
    assert pg_fetchone("SELECT COUNT(*) AS n FROM telegram_links")["n"] == 0


# ── The inbound gate ────────────────────────────────────────────────────────

def test_find_link_resolves_one_active_seat(pg_db, seats):
    from telegram import store
    store.connect("t", "bot")
    code = store.mint_link_code(seats["member"]["id"])
    store.claim_link(code, "chat1", "tg1", "Alex")

    link = store.find_link("chat1", "tg1")
    assert link["user_id"] == seats["member"]["id"]
    assert link["user"]["email"] == "member@example.com"
    assert link["user"]["is_active"] is True
    # Both identifiers must match.
    assert store.find_link("chat1", "tgOTHER") is None
    assert store.find_link("chatOTHER", "tg1") is None


def test_deactivating_a_seat_cuts_its_telegram_off(pg_db, seats):
    """Inbound AND outbound, in one move — no extra bookkeeping."""
    from telegram import store
    from users import service as users_service

    store.connect("t", "bot")
    code = store.mint_link_code(seats["member"]["id"])
    store.claim_link(code, "chat1", "tg1", "Alex")
    assert store.find_link("chat1", "tg1") is not None
    assert store.get_send_target(seats["member"]["id"]) is not None
    assert store.list_send_targets() != []

    users_service.update_user(seats["member"]["id"], is_active=False)
    assert store.find_link("chat1", "tg1") is None
    assert store.get_send_target(seats["member"]["id"]) is None
    assert store.list_send_targets() == []


def test_unlink_releases_my_own_chat(pg_db, seats):
    from telegram import store
    store.connect("t", "bot")
    code = store.mint_link_code(seats["member"]["id"])
    store.claim_link(code, "chat1", "tg1", "Alex")
    assert store.unlink(seats["member"]["id"]) is True
    assert store.find_link("chat1", "tg1") is None
    assert store.unlink(seats["member"]["id"]) is False  # nothing left to release


# ── Send targets ────────────────────────────────────────────────────────────

def test_send_targets_are_per_seat_and_broadcast_covers_all(pg_db, seats):
    from telegram import store
    store.connect("123:REAL", "bot")
    a = store.mint_link_code(seats["admin"]["id"])
    b = store.mint_link_code(seats["member"]["id"])
    store.claim_link(a, "chatA", "tgA", "A")
    store.claim_link(b, "chatB", "tgB", "B")

    assert store.get_send_target(seats["admin"]["id"]) == ("123:REAL", "chatA")
    assert store.get_send_target(seats["member"]["id"]) == ("123:REAL", "chatB")
    assert sorted(c for _t, c in store.list_send_targets()) == ["chatA", "chatB"]

    # A seat that never linked receives nothing targeted.
    assert store.get_send_target(seats["retired"]["id"]) is None


def test_no_send_targets_without_a_bot(pg_db, seats):
    from telegram import store
    store.connect("t", "bot")
    code = store.mint_link_code(seats["member"]["id"])
    store.claim_link(code, "chat1", "tg1", "Alex")
    store.disconnect()
    assert store.get_send_target(seats["member"]["id"]) is None
    assert store.list_send_targets() == []


# ── Per-link conversation ───────────────────────────────────────────────────

def _bound_link(store, user_id, chat, tg):
    code = store.mint_link_code(user_id)
    store.claim_link(code, chat, tg, "Person")
    return store.find_link(chat, tg)


def test_each_link_gets_a_conversation_its_own_seat_owns(pg_db, seats):
    """The earliest-admin stopgap #191 left here is gone — the link has a real owner."""
    from core.postgres import pg_fetchone
    from telegram import store

    store.connect("t", "bot")
    link = _bound_link(store, seats["member"]["id"], "chat1", "tg1")
    conv = store.get_or_create_conversation(link)
    row = pg_fetchone("SELECT user_id FROM assistant_conversations WHERE id = %s", (conv,))
    assert row["user_id"] == seats["member"]["id"]
    # Stable while it exists, and re-read from the link row.
    assert store.get_or_create_conversation(store.find_link("chat1", "tg1")) == conv


def test_two_seats_get_separate_conversations(pg_db, seats):
    from telegram import store
    store.connect("t", "bot")
    a = _bound_link(store, seats["admin"]["id"], "chatA", "tgA")
    b = _bound_link(store, seats["member"]["id"], "chatB", "tgB")
    assert store.get_or_create_conversation(a) != store.get_or_create_conversation(b)


def test_deleted_conversation_is_recreated(pg_db, seats):
    from assistant import history
    from telegram import store

    store.connect("t", "bot")
    link = _bound_link(store, seats["member"]["id"], "chat1", "tg1")
    conv1 = store.get_or_create_conversation(link)
    # FK is ON DELETE SET NULL, so the link survives its conversation's deletion.
    history.delete_conversation(conv1, user_id=seats["member"]["id"])
    conv2 = store.get_or_create_conversation(store.find_link("chat1", "tg1"))
    assert conv2 and conv2 != conv1
    assert history.conversation_exists(conv2, user_id=seats["member"]["id"])


# ── The per-link confirmation batch gate ────────────────────────────────────

def _pending_batch(history, store, link, msg_id):
    conv = store.get_or_create_conversation(link)
    history.save_message(
        conv, msg_id, "assistant", "doing two things", tool_calls=[
            {"tool": "crm_create_task", "tool_use_id": "tu1", "args": {}},
            {"tool": "crm_update_deal", "tool_use_id": "tu2", "args": {}},
        ],
    )
    history.merge_tool_result(msg_id, "tu1", "crm_create_task", history.PENDING_RESULT_JSON)
    history.merge_tool_result(msg_id, "tu2", "crm_update_deal", history.PENDING_RESULT_JSON)
    store.set_pending_msg(link["id"], msg_id)
    return conv


def test_batch_gate_waits_then_continues(pg_db, seats):
    from assistant import history
    from telegram import store

    store.connect("t", "bot")
    link = _bound_link(store, seats["member"]["id"], "chat1", "tg1")
    conv = _pending_batch(history, store, link, "msg-batch-1")

    assert history.list_pending_tool_uses(conv, "msg-batch-1") == ["tu1", "tu2"]

    # Resolve the first — batch NOT done yet.
    history.merge_tool_result("msg-batch-1", "tu1", "crm_create_task", '{"ok": true}')
    assert store.try_consume_batch(link["id"], "msg-batch-1") is False

    # Resolve the second — batch done → continue exactly once, pending cleared.
    history.merge_tool_result("msg-batch-1", "tu2", "crm_update_deal", history.DENIED_RESULT_JSON)
    assert store.try_consume_batch(link["id"], "msg-batch-1") is True
    assert store.get_link(seats["member"]["id"])["pending_msg_id"] == ""
    # A repeat press after consumption does not re-continue.
    assert store.try_consume_batch(link["id"], "msg-batch-1") is False


def test_two_seats_batches_are_independent(pg_db, seats):
    """The mutex moved onto the link row: one seat's open batch never gates another's."""
    from assistant import history
    from telegram import store

    store.connect("t", "bot")
    a = _bound_link(store, seats["admin"]["id"], "chatA", "tgA")
    b = _bound_link(store, seats["member"]["id"], "chatB", "tgB")
    _pending_batch(history, store, a, "msg-a")
    _pending_batch(history, store, b, "msg-b")

    # Seat B settles while seat A's batch is still wide open.
    history.merge_tool_result("msg-b", "tu1", "crm_create_task", '{"ok": true}')
    history.merge_tool_result("msg-b", "tu2", "crm_update_deal", '{"ok": true}')
    assert store.try_consume_batch(b["id"], "msg-b") is True
    assert store.get_link(seats["admin"]["id"])["pending_msg_id"] == "msg-a"
    assert store.try_consume_batch(a["id"], "msg-a") is False


def test_clear_pending_msg_only_clears_the_named_batch(pg_db, seats):
    """Conditional, so a batch installed after a stale read keeps its live buttons."""
    from telegram import store

    store.connect("t", "bot")
    link = _bound_link(store, seats["member"]["id"], "chat1", "tg1")
    store.set_pending_msg(link["id"], "msgNEW")
    store.clear_pending_msg(link["id"], "msgOLD")   # a stale caller
    assert store.get_link(seats["member"]["id"])["pending_msg_id"] == "msgNEW"
    store.clear_pending_msg(link["id"], "msgNEW")
    assert store.get_link(seats["member"]["id"])["pending_msg_id"] == ""


def test_batch_gate_clears_when_the_message_is_gone(pg_db, seats):
    from assistant import history
    from telegram import store

    store.connect("t", "bot")
    link = _bound_link(store, seats["member"]["id"], "chat1", "tg1")
    conv = _pending_batch(history, store, link, "msg-gone")
    history.delete_conversation(conv, user_id=seats["member"]["id"])
    assert store.try_consume_batch(link["id"], "msg-gone") is False
    assert store.get_link(seats["member"]["id"])["pending_msg_id"] == ""
