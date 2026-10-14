"""crm/todo_sessions.py without a database (#293): transaction shape, hashing, and the
decisions the module makes in Python. The lifecycle against real SQL (single use under a
race, caps, idle expiry, deactivation) is test_todo_web_sessions_integration.py."""

from datetime import datetime, timedelta, timezone

from crm import todo_sessions as ts

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


def test_redeem_is_one_transaction_seat_lock_before_the_single_use_delete(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, ts, fetchone_results=[(7,), (7, NOW + timedelta(minutes=30))])
    out = ts.redeem_signin_token("code", replaces="old-cookie", now=NOW)
    assert out["user_id"] == 7 and out["session_token"]
    assert conn.entries == 1
    assert len(set(conn.executed_by)) == 1  # every statement on one cursor
    sql = [s for s, _ in conn.executed]
    lock = next(i for i, s in enumerate(sql) if "pg_advisory_xact_lock" in s)
    spend = next(i for i, s in enumerate(sql) if s.startswith("DELETE FROM todo_web_signin_tokens WHERE token_hash = %s RETURNING"))
    insert = next(i for i, s in enumerate(sql) if s.startswith("INSERT INTO todo_web_sessions"))
    assert lock < spend < insert
    # The replaced cookie's row is ended only within the same seat.
    assert any("DELETE FROM todo_web_sessions WHERE token_hash = %s AND user_id = %s" in s for s in sql)


def test_a_code_nobody_holds_takes_no_lock(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, ts, fetchone_results=[None])
    assert ts.redeem_signin_token("guess", now=NOW) is None
    assert not any("pg_advisory" in s for s, _ in conn.executed)


def test_an_expired_code_is_spent_and_refused(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, ts, fetchone_results=[(7,), (7, NOW)])
    assert ts.redeem_signin_token("code", now=NOW) is None
    assert not any(s.startswith("INSERT") for s, _ in conn.executed)


def test_a_lost_race_is_refused(monkeypatch, fake_conn):
    """The SELECT saw the code, but another redemption's DELETE got it first."""
    conn = fake_conn(monkeypatch, ts, fetchone_results=[(7,), None])
    assert ts.redeem_signin_token("code", now=NOW) is None
    assert not any(s.startswith("INSERT") for s, _ in conn.executed)


def test_only_hashes_reach_sql(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, ts, fetchone_results=[(7,), (7, NOW + timedelta(minutes=5))])
    out = ts.redeem_signin_token("plain-code", replaces="plain-cookie", now=NOW)
    token, _ = ts.issue_signin_token(7, now=NOW)
    params = [p for _, ps in conn.executed for p in ps]
    for plaintext in ("plain-code", "plain-cookie", out["session_token"], token):
        assert plaintext not in params
    assert ts.hash_token("plain-code") in params


def test_lookups_join_the_live_seat(monkeypatch, fake_conn):
    """Deactivating a seat ends its sessions and codes on the next request."""
    conn = fake_conn(monkeypatch, ts, fetchone_results=[None, None])
    assert ts.resolve_session("cookie", now=NOW) is None
    assert ts.redeem_signin_token("code", now=NOW) is None
    assert all("u.is_active" in s for s, _ in conn.executed)


def test_no_cookie_resolves_to_nothing_without_a_query(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, ts)
    assert ts.resolve_session(None) is None and ts.resolve_session("") is None
    assert conn.entries == 0


def test_touch_skips_the_write_inside_the_hour(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, ts)
    assert ts.touch_session("h", NOW - timedelta(minutes=59), now=NOW) is False
    assert conn.entries == 0
    assert ts.touch_session("h", NOW - timedelta(minutes=61), now=NOW) is True
    sql, params = conn.executed[0]
    assert "last_used_at < %s" in sql and params == (NOW, "h", NOW - ts.TOUCH_INTERVAL)


def test_the_cookie_is_secure_and_host_prefixed_only_over_https(monkeypatch):
    monkeypatch.setattr(ts.settings, "frontend_url", "http://192.168.1.5:8000")
    assert (ts.cookie_name(), ts.cookie_secure()) == ("cakecrm_todo_session", False)
    monkeypatch.setattr(ts.settings, "frontend_url", "https://crm.example.test")
    assert (ts.cookie_name(), ts.cookie_secure()) == ("__Host-cakecrm_todo_session", True)


# ── The member routes that drive the Todo app devices card ────────────────────

def _member_client(monkeypatch, *, enabled=True, user_id=2):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from core.auth import get_current_user
    from crm import gtd_router

    calls = []
    monkeypatch.setattr(gtd_router.service, "get_todo_public_settings",
                        lambda: {"todo_capture_token": "", "todo_web_enabled": enabled})
    monkeypatch.setattr(gtd_router.todo_sessions, "issue_signin_token",
                        lambda uid: calls.append(("issue", uid)) or ("tok", NOW + ts.SIGNIN_TTL))
    monkeypatch.setattr(gtd_router.todo_sessions, "count_sessions",
                        lambda uid: calls.append(("count", uid)) or 3)
    monkeypatch.setattr(gtd_router.todo_sessions, "revoke_all",
                        lambda uid: calls.append(("revoke_all", uid)))
    monkeypatch.setattr(gtd_router.settings, "frontend_url", "https://crm.example.test")
    monkeypatch.setattr(gtd_router.settings, "frontend_url_is_default", False)
    app = FastAPI()
    app.include_router(gtd_router.router, prefix="/api/crm/gtd")
    app.dependency_overrides[get_current_user] = lambda: {"id": user_id, "role": "member"}
    return TestClient(app), calls


def test_a_member_mints_a_code_for_their_own_seat(monkeypatch):
    client, calls = _member_client(monkeypatch)
    body = client.post("/api/crm/gtd/web-signin-link").json()
    assert body["url"] == "https://crm.example.test/todo/signin/tok"
    assert body["qr"].startswith("data:image/png;base64,")
    assert body["expires_at"] == (NOW + ts.SIGNIN_TTL).isoformat()
    assert calls == [("issue", 2)]


def test_no_code_while_the_app_is_switched_off(monkeypatch):
    client, calls = _member_client(monkeypatch, enabled=False)
    assert client.post("/api/crm/gtd/web-signin-link").status_code == 409
    assert calls == []


def test_the_count_and_sign_out_everywhere_act_on_the_callers_seat_only(monkeypatch):
    client, calls = _member_client(monkeypatch, user_id=5)
    assert client.get("/api/crm/gtd/web-sessions").json() == {"enabled": True, "count": 3}
    assert client.post("/api/crm/gtd/web-signout-all").json() == {"ok": True}
    assert calls == [("count", 5), ("revoke_all", 5)]


def test_an_unconfigured_install_links_to_the_address_it_was_reached_at(monkeypatch):
    client, _ = _member_client(monkeypatch)
    from crm import gtd_router
    monkeypatch.setattr(gtd_router.settings, "frontend_url_is_default", True)
    assert client.post("/api/crm/gtd/web-signin-link").json()["url"] == "http://testserver/todo/signin/tok"
