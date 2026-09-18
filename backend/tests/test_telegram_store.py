"""Telegram store — the Python-level branches that must be correct.

Hermetic: ``get_connection`` is a FakeConn (fixture), ``pg_fetchone``/``pg_fetchall``/
``pg_execute`` are monkeypatched, encryption runs for real (per the autouse
encryption_key fixture). We assert the decisive logic — the link claim's
compare-and-consume and its refusals, the per-link exactly-once batch gate, the
conditional pending-marker clear, active-seat filtering on both the inbound gate and the
send targets, token encryption round-trip, monotonic offset SQL — not Postgres semantics
(the partial UNIQUE indexes and the cascade are covered by ``test_telegram_store_pg``
against a real database).
"""

import psycopg2
import pytest

from telegram import store


def _joined_link(**over):
    """A row as the link+user SELECT returns it, before ``_link_row`` splits it."""
    row = {
        "id": 7, "user_id": 2, "link_code": "", "chat_id": "chat1",
        "telegram_user_id": "tg1", "telegram_name": "Alex",
        "conversation_id": "conv1", "pending_msg_id": "",
        "u_id": 2, "u_email": "member@cakecrm.test", "u_name": "Member",
        "u_role": "member", "u_is_active": True,
    }
    row.update(over)
    return row


# ── Bot token / offset (install-wide) ───────────────────────────────────────

def test_get_bot_token_roundtrip(monkeypatch):
    from core.encryption import encrypt_value

    enc = encrypt_value("123456:ABC-DEF")
    assert enc.startswith("enc:v1:")
    monkeypatch.setattr(store, "pg_fetchone", lambda *a, **k: {"bot_token_enc": enc})
    assert store.get_bot_token() == "123456:ABC-DEF"


def test_get_bot_token_empty_when_unset(monkeypatch):
    monkeypatch.setattr(store, "pg_fetchone", lambda *a, **k: {"bot_token_enc": ""})
    assert store.get_bot_token() == ""


def test_get_settings_carries_no_per_seat_state(monkeypatch):
    """Since #193 the singleton is install config only — link state lives per seat."""
    monkeypatch.setattr(store, "pg_fetchone", lambda *a, **k: {
        "bot_token_enc": "enc:v1:X", "bot_username": "acmebot", "poll_offset": 3,
    })
    s = store.get_settings()
    assert s["connected"] is True
    for gone in ("linked", "linked_chat_id", "linked_user_id", "link_code", "pending_msg_id"):
        assert gone not in s


def test_connect_stores_encrypted_token_and_clears_every_link(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, store)
    store.connect("999:XYZ", "mybot")
    updates = [e for e in conn.executed if "UPDATE telegram_settings" in e[0]]
    assert updates, "connect must UPDATE telegram_settings"
    assert any(isinstance(p, str) and p.startswith("enc:v1:") for p in updates[-1][1])
    assert "poll_offset = 0" in updates[-1][0]
    # A new bot invalidates every chat binding — cleared in the SAME transaction.
    link_clears = [e for e in conn.executed if "UPDATE telegram_links" in e[0]]
    assert link_clears and "chat_id = ''" in link_clears[-1][0]
    assert conn.entries == 1, "token swap and link clearing must ride one transaction"


def test_disconnect_wipes_bot_and_links_in_one_transaction(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, store)
    store.disconnect()
    assert any("bot_token_enc = ''" in e[0] for e in conn.executed)
    assert any("UPDATE telegram_links" in e[0] and "chat_id = ''" in e[0] for e in conn.executed)
    assert conn.entries == 1


def test_advance_offset_is_monotonic(monkeypatch):
    captured = {}
    monkeypatch.setattr(store, "pg_execute", lambda sql, params=(): captured.update(sql=sql, params=params))
    store.advance_offset(42)
    assert "GREATEST(poll_offset" in captured["sql"]
    assert captured["params"] == (42,)


# ── Minting and claiming a per-seat link ────────────────────────────────────

def test_mint_link_code_upserts_and_frees_the_old_device(monkeypatch):
    captured = {}
    monkeypatch.setattr(store, "pg_execute", lambda sql, params=(): captured.update(sql=sql, params=params))
    code = store.mint_link_code(2)
    assert code and len(code) >= 20  # 128 bits of token_urlsafe
    assert "ON CONFLICT (user_id) DO UPDATE" in captured["sql"]
    assert "chat_id = ''" in captured["sql"] and "pending_msg_id = ''" in captured["sql"]
    # The seat's assistant thread survives a change of device.
    assert "conversation_id" not in captured["sql"]
    assert captured["params"] == (2, code)


def test_claim_link_binds_the_chat_and_consumes_the_code(monkeypatch, fake_conn):
    # SELECT the code's row → (link_id, user_id); the chat-conflict probe finds nothing.
    conn = fake_conn(monkeypatch, store, fetchone_results=[(7, 2), None])
    assert store.claim_link("SECRET123", "chat1", "tg1", "Alex") == 2
    updates = [e for e in conn.executed if "UPDATE telegram_links" in e[0]]
    assert updates and "link_code = ''" in updates[-1][0]  # single-use
    assert tuple(updates[-1][1]) == ("chat1", "tg1", "Alex", 7)


def test_claim_link_locks_only_the_link_row(monkeypatch, fake_conn):
    """FOR UPDATE OF l — never lock the joined users row other requests are writing."""
    conn = fake_conn(monkeypatch, store, fetchone_results=[(7, 2), None])
    store.claim_link("SECRET123", "chat1", "tg1", "Alex")
    selects = [e[0] for e in conn.executed if "FOR UPDATE" in e[0]]
    assert selects and "FOR UPDATE OF l" in selects[0]


def test_claim_link_rejects_unknown_or_expired_code(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, store, fetchone_results=[None])
    assert store.claim_link("WRONG", "chat1", "tg1", "Alex") is None
    assert not [e for e in conn.executed if "UPDATE telegram_links" in e[0]]


def test_claim_link_refuses_a_chat_already_bound_to_another_seat(monkeypatch, fake_conn):
    """One private chat speaks for exactly one seat — never steal it with a new code."""
    conn = fake_conn(monkeypatch, store, fetchone_results=[(7, 2), (1,)])
    assert store.claim_link("SECRET123", "chat1", "tg1", "Alex") is None
    assert not [e for e in conn.executed if "UPDATE telegram_links" in e[0]]


def test_claim_link_requires_an_active_seat(monkeypatch, fake_conn):
    """The join carries `u.is_active`, so a deactivated seat's code is dead."""
    conn = fake_conn(monkeypatch, store, fetchone_results=[(7, 2), None])
    store.claim_link("SECRET123", "chat1", "tg1", "Alex")
    assert any("u.is_active" in e[0] for e in conn.executed if "FOR UPDATE" in e[0])


def test_claim_link_rejects_empty_code_without_touching_the_db(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, store, fetchone_results=[(7, 2), None])
    assert store.claim_link("", "chat1", "tg1", "Alex") is None
    assert store.claim_link("code", "", "tg1", "Alex") is None
    assert conn.executed == []


def test_claim_link_losing_the_chat_race_returns_none_not_an_exception(monkeypatch):
    """Two codes racing for one chat: the unique index arbitrates, the loser rolls back.

    Caught OUTSIDE the transaction block, so the losing claim writes nothing and its
    code survives for a retry.
    """
    from contextlib import contextmanager

    @contextmanager
    def exploding_conn():
        class Cur:
            def execute(self, sql, params=()):
                if sql.strip().startswith("UPDATE"):
                    raise psycopg2.errors.UniqueViolation("uq_telegram_links_chat")

            def fetchone(self):
                return (7, 2) if not getattr(self, "_probed", False) else None

        cur = Cur()

        class C:
            def cursor(self_inner):
                return cur
        yield C()

    monkeypatch.setattr(store, "get_connection", exploding_conn)
    assert store.claim_link("SECRET123", "chat1", "tg1", "Alex") is None


# ── The inbound authorization gate ──────────────────────────────────────────

def test_find_link_returns_the_link_with_its_public_user(monkeypatch):
    monkeypatch.setattr(store, "pg_fetchone", lambda *a, **k: _joined_link())
    link = store.find_link("chat1", "tg1")
    assert link["id"] == 7 and link["user_id"] == 2
    assert link["user"] == {
        "id": 2, "email": "member@cakecrm.test", "name": "Member",
        "role": "member", "is_active": True,
    }
    # The joined column aliases must not leak into the link dict.
    assert not [k for k in link if k.startswith("u_")]


def test_find_link_matches_both_identifiers_and_requires_an_active_seat(monkeypatch):
    captured = {}

    def fetchone(sql, params=()):
        captured.update(sql=sql, params=params)
        return _joined_link()

    monkeypatch.setattr(store, "pg_fetchone", fetchone)
    store.find_link("chat1", "tg1")
    assert "l.chat_id = %s" in captured["sql"]
    assert "l.telegram_user_id = %s" in captured["sql"]
    assert "u.is_active" in captured["sql"]
    assert captured["params"] == ("chat1", "tg1")


def test_find_link_none_for_missing_identifiers(monkeypatch):
    called = []
    monkeypatch.setattr(store, "pg_fetchone", lambda *a, **k: called.append(1))
    assert store.find_link("", "tg1") is None
    assert store.find_link("chat1", "") is None
    assert called == []


def test_find_link_none_when_nothing_matches(monkeypatch):
    monkeypatch.setattr(store, "pg_fetchone", lambda *a, **k: None)
    assert store.find_link("chat1", "tg1") is None


def test_unlink_reports_whether_anything_was_bound(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, store, rowcounts={"UPDATE telegram_links": 1})
    assert store.unlink(2) is True
    assert any("chat_id <> ''" in e[0] for e in conn.executed)

    conn = fake_conn(monkeypatch, store, rowcounts={"UPDATE telegram_links": 0})
    assert store.unlink(2) is False


# ── Send targets ────────────────────────────────────────────────────────────

def test_get_send_target_pairs_token_and_chat_from_one_row(monkeypatch):
    from core.encryption import encrypt_value

    captured = {}

    def fetchone(sql, params=()):
        captured.update(sql=sql)
        return {"bot_token_enc": encrypt_value("123:REAL"), "chat_id": "chat1"}

    monkeypatch.setattr(store, "pg_fetchone", fetchone)
    assert store.get_send_target(2) == ("123:REAL", "chat1")
    assert "u.is_active" in captured["sql"], "a deactivated seat must not be delivered to"


def test_get_send_target_none_when_unlinked(monkeypatch):
    monkeypatch.setattr(store, "pg_fetchone", lambda *a, **k: None)
    assert store.get_send_target(2) is None


def test_list_send_targets_decrypts_once_for_every_chat(monkeypatch):
    from core.encryption import encrypt_value

    enc = encrypt_value("123:REAL")
    captured = {}

    def fetchall(sql, params=()):
        captured.update(sql=sql)
        return [{"bot_token_enc": enc, "chat_id": "chatA"},
                {"bot_token_enc": enc, "chat_id": "chatB"}]

    monkeypatch.setattr(store, "pg_fetchall", fetchall)
    assert store.list_send_targets() == [("123:REAL", "chatA"), ("123:REAL", "chatB")]
    assert "u.is_active" in captured["sql"]


def test_list_send_targets_empty_when_nobody_linked(monkeypatch):
    monkeypatch.setattr(store, "pg_fetchall", lambda *a, **k: [])
    assert store.list_send_targets() == []


# ── Per-link conversation ───────────────────────────────────────────────────

def test_get_or_create_conversation_reuses_a_live_owned_conversation(monkeypatch):
    seen = {}

    def exists(conv_id, user_id=None):
        seen.update(conv_id=conv_id, user_id=user_id)
        return True

    monkeypatch.setattr(store, "conversation_exists", exists)
    link = {"id": 7, "user_id": 2, "conversation_id": "conv1"}
    assert store.get_or_create_conversation(link) == "conv1"
    # Scoped to the link's own seat — the earliest-admin stopgap #191 left is gone.
    assert seen == {"conv_id": "conv1", "user_id": 2}


def test_get_or_create_conversation_mints_one_owned_by_the_link(monkeypatch):
    created = {}
    monkeypatch.setattr(store, "conversation_exists", lambda c, user_id=None: False)
    monkeypatch.setattr(store, "create_conversation",
                        lambda *, user_id: created.update(user_id=user_id) or {"id": "convNEW"})
    monkeypatch.setattr(store, "pg_execute", lambda sql, params=(): None)
    link = {"id": 7, "user_id": 2, "conversation_id": ""}
    assert store.get_or_create_conversation(link) == "convNEW"
    assert created == {"user_id": 2}
    assert link["conversation_id"] == "convNEW"  # the caller's row is kept in step


def test_get_or_create_conversation_recreates_a_deleted_one(monkeypatch):
    monkeypatch.setattr(store, "conversation_exists", lambda c, user_id=None: False)
    monkeypatch.setattr(store, "create_conversation", lambda *, user_id: {"id": "convNEW"})
    monkeypatch.setattr(store, "pg_execute", lambda sql, params=(): None)
    link = {"id": 7, "user_id": 2, "conversation_id": "convGONE"}
    assert store.get_or_create_conversation(link) == "convNEW"


# ── The per-link confirmation batch gate ────────────────────────────────────

def test_try_consume_batch_continues_when_all_resolved(monkeypatch, fake_conn):
    resolved = [{"tool_use_id": "t1", "content": '{"status": "denied_by_user"}'}]
    conn = fake_conn(monkeypatch, store, fetchone_results=[("msgA", "conv1"), (resolved,)])
    assert store.try_consume_batch(7, "msgA") is True
    assert any("pending_msg_id = ''" in e[0] for e in conn.executed)


def test_try_consume_batch_locks_the_link_not_the_singleton(monkeypatch, fake_conn):
    """The mutex moved off the singleton, so two seats never wait on each other."""
    resolved = [{"tool_use_id": "t1", "content": '{"status": "denied_by_user"}'}]
    conn = fake_conn(monkeypatch, store, fetchone_results=[("msgA", "conv1"), (resolved,)])
    store.try_consume_batch(7, "msgA")
    locks = [e for e in conn.executed if "FOR UPDATE" in e[0]]
    assert locks and "FROM telegram_links" in locks[0][0]
    assert locks[0][1] == (7,)


def test_try_consume_batch_waits_when_sibling_pending(monkeypatch, fake_conn):
    mixed = [
        {"tool_use_id": "t1", "content": '{"status": "denied_by_user"}'},
        {"tool_use_id": "t2", "content": '{"status": "pending_user_approval"}'},
    ]
    conn = fake_conn(monkeypatch, store, fetchone_results=[("msgA", "conv1"), (mixed,)])
    assert store.try_consume_batch(7, "msgA") is False
    assert not any("pending_msg_id = ''" in e[0] for e in conn.executed)


def test_try_consume_batch_false_for_stale_batch(monkeypatch, fake_conn):
    fake_conn(monkeypatch, store, fetchone_results=[("msgCURRENT", "conv1")])
    assert store.try_consume_batch(7, "msgOLD") is False


def test_try_consume_batch_missing_row_clears_and_returns_false(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, store, fetchone_results=[("msgGONE", "conv1"), None])
    assert store.try_consume_batch(7, "msgGONE") is False
    assert any("pending_msg_id = ''" in e[0] for e in conn.executed)


def test_try_consume_batch_false_when_the_link_is_gone(monkeypatch, fake_conn):
    fake_conn(monkeypatch, store, fetchone_results=[None])
    assert store.try_consume_batch(7, "msgA") is False


def test_set_pending_msg_is_keyed_on_the_link(monkeypatch):
    captured = {}
    monkeypatch.setattr(store, "pg_execute", lambda sql, params=(): captured.update(sql=sql, params=params))
    store.set_pending_msg(7, "msgA")
    assert "UPDATE telegram_links" in captured["sql"]
    assert captured["params"] == ("msgA", 7)


def test_clear_pending_msg_only_clears_the_batch_the_caller_saw(monkeypatch):
    """Conditional, so a batch installed after the read keeps its live buttons."""
    captured = {}
    monkeypatch.setattr(store, "pg_execute", lambda sql, params=(): captured.update(sql=sql, params=params))
    store.clear_pending_msg(7, "msgA")
    assert "AND pending_msg_id = %s" in captured["sql"]
    assert captured["params"] == (7, "msgA")


def test_clear_pending_msg_no_ops_without_an_expected_batch(monkeypatch):
    calls = []
    monkeypatch.setattr(store, "pg_execute", lambda sql, params=(): calls.append(sql))
    store.clear_pending_msg(7, "")
    assert calls == []


# ── history helpers backing the batch gate (hermetic — CI runs these) ────────

def test_list_pending_tool_uses_filters(monkeypatch):
    from assistant import history

    row = {"tool_results": [
        {"tool_use_id": "t1", "content": '{"status": "pending_user_approval"}'},
        {"tool_use_id": "t2", "content": '{"ok": true}'},
        {"tool_use_id": "t3", "content": '{"status": "pending_user_approval"}'},
    ]}
    monkeypatch.setattr(history, "pg_fetchone", lambda *a, **k: row)
    assert history.list_pending_tool_uses("conv1", "m1") == ["t1", "t3"]


def test_is_unsettled_result():
    from assistant import history

    assert history.is_unsettled_result('{"status": "pending_user_approval"}') is True
    assert history.is_unsettled_result('{"status": "executing"}') is True  # stuck-mid-execution blocks
    assert history.is_unsettled_result('{"status": "denied_by_user"}') is False
    assert history.is_unsettled_result('{"ok": true}') is False


# ── The singleton's retired per-seat API is really gone ─────────────────────

@pytest.mark.parametrize("name", ["link_chat", "regenerate_link_code"])
def test_singleton_link_api_is_removed(name):
    """Leaving either behind would be a second, install-wide way to bind a chat."""
    assert not hasattr(store, name)
