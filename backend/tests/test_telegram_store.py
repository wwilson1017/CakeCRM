"""Telegram singleton store — the Python-level branches that must be correct.

Hermetic: ``get_connection`` is a FakeConn (fixture), ``pg_fetchone``/``pg_execute`` are
monkeypatched, encryption runs for real (per the autouse encryption_key fixture). We
assert the decisive logic — link-code compare-and-consume, the exactly-once batch gate,
token encryption round-trip, monotonic offset SQL — not Postgres semantics (those are
covered by the integration marker path against a real DB).
"""

from telegram import store


def test_get_bot_token_roundtrip(monkeypatch):
    from core.encryption import encrypt_value

    enc = encrypt_value("123456:ABC-DEF")
    assert enc.startswith("enc:v1:")
    monkeypatch.setattr(store, "pg_fetchone", lambda *a, **k: {"bot_token_enc": enc})
    assert store.get_bot_token() == "123456:ABC-DEF"


def test_get_bot_token_empty_when_unset(monkeypatch):
    monkeypatch.setattr(store, "pg_fetchone", lambda *a, **k: {"bot_token_enc": ""})
    assert store.get_bot_token() == ""


def test_connect_stores_encrypted_token_and_resets_state(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, store)
    code = store.connect("999:XYZ", "mybot")
    assert code  # a link code was generated
    # The UPDATE must carry an enc:v1: token and reset the offset.
    updates = [e for e in conn.executed if "UPDATE telegram_settings" in e[0]]
    assert updates, "connect must UPDATE telegram_settings"
    params = updates[-1][1]
    assert any(isinstance(p, str) and p.startswith("enc:v1:") for p in params)
    assert "poll_offset = 0" in updates[-1][0]


def test_link_chat_matches_and_consumes(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, store, fetchone_results=[("SECRET123",)])
    assert store.link_chat("SECRET123", "chat1", "user1", "Alex") is True
    # An UPDATE binding the user AND clearing the code was issued.
    updates = [e for e in conn.executed if "UPDATE telegram_settings" in e[0]]
    assert updates and "link_code = ''" in updates[-1][0]
    assert ("chat1", "user1", "Alex") == tuple(updates[-1][1])


def test_link_chat_rejects_mismatch(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, store, fetchone_results=[("SECRET123",)])
    assert store.link_chat("WRONG", "chat1", "user1", "Alex") is False
    assert not [e for e in conn.executed if "UPDATE telegram_settings" in e[0]]


def test_link_chat_rejects_when_no_code_set(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, store, fetchone_results=[("",)])
    assert store.link_chat("anything", "chat1", "user1", "Alex") is False
    assert not [e for e in conn.executed if "UPDATE telegram_settings" in e[0]]


def test_try_consume_batch_continues_when_all_resolved(monkeypatch, fake_conn):
    # pending_msg_id names this batch; its tool_results carry NO pending entries.
    resolved = [{"tool_use_id": "t1", "content": '{"status": "denied_by_user"}'}]
    conn = fake_conn(monkeypatch, store, fetchone_results=[("msgA", "conv1"), (resolved,)])
    assert store.try_consume_batch("msgA") is True
    assert any("pending_msg_id = ''" in e[0] for e in conn.executed)


def test_try_consume_batch_waits_when_sibling_pending(monkeypatch, fake_conn):
    mixed = [
        {"tool_use_id": "t1", "content": '{"status": "denied_by_user"}'},
        {"tool_use_id": "t2", "content": '{"status": "pending_user_approval"}'},
    ]
    conn = fake_conn(monkeypatch, store, fetchone_results=[("msgA", "conv1"), (mixed,)])
    assert store.try_consume_batch("msgA") is False
    assert not any("pending_msg_id = ''" in e[0] for e in conn.executed)


def test_try_consume_batch_false_for_stale_batch(monkeypatch, fake_conn):
    # The current pending batch is a DIFFERENT message → a stale button press no-ops.
    fake_conn(monkeypatch, store, fetchone_results=[("msgCURRENT", "conv1")])
    assert store.try_consume_batch("msgOLD") is False


def test_advance_offset_is_monotonic(monkeypatch):
    captured = {}
    monkeypatch.setattr(store, "pg_execute", lambda sql, params=(): captured.update(sql=sql, params=params))
    store.advance_offset(42)
    assert "GREATEST(poll_offset" in captured["sql"]
    assert captured["params"] == (42,)


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
