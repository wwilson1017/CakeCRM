"""Login, session validation and in-app password change (issues #78, #60).

Hermetic: the users service's Postgres access is stubbed. bcrypt runs for real, so
the round trips below prove actual hashes verify.

#78 built these behaviors against a single shared credential; #60 re-keyed them per
user. What must still hold, and is pinned here:

* a wrong current password answers 400, not 401 (a typo must not read as a logout);
* the current-password check happens BEFORE the 2FA code, so a typo cannot spend a
  single-use backup code;
* a database outage on the login path is a 503, never a fallback to an env var;
* the session-invalidation epoch is stamped at mint time from the SAME row the
  password was verified against — never re-read afterwards.
"""

from types import SimpleNamespace

import pytest
from conftest import fake_admin
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from core import auth
from core.auth import get_current_user
from users import service as users_service

CURRENT_PW = "old-password"
NEW_PW = "brand-new-password"
ADMIN_ID = 1


def _user_row(**over):
    """A users-table row as get_user returns it (password hash included)."""
    row = {
        "id": ADMIN_ID,
        "email": "admin@cakecrm.test",
        "name": "Test Admin",
        "role": "admin",
        "is_active": True,
        "token_epoch": 3,
        "password_hash": users_service.hash_password(CURRENT_PW),
        "created_at": "2026-08-01T00:00:00+00:00",
        "updated_at": "2026-08-01T00:00:00+00:00",
    }
    row.update(over)
    return row


def _bearer(token: str):
    """Minimal stand-in for the Request object get_current_user reads headers from."""
    return SimpleNamespace(headers={"Authorization": f"Bearer {token}"})


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """The limiter is module-level; it would leak across tests."""
    auth._attempts.clear()
    yield
    auth._attempts.clear()


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(auth.router, prefix="/api")
    app.dependency_overrides[get_current_user] = fake_admin
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def stub_user(monkeypatch):
    """Point both user lookups at one row. Returns a setter so tests can mutate it."""
    state = {"row": _user_row()}

    monkeypatch.setattr(users_service, "get_user", lambda uid: state["row"])
    monkeypatch.setattr(users_service, "get_user_by_email", lambda email: state["row"])
    return state


@pytest.fixture
def no_2fa(monkeypatch):
    """Default the 2FA seam off, and record trusted-device revocations."""
    import core.auth_2fa as auth_2fa

    revoked = []
    monkeypatch.setattr(auth_2fa, "is_2fa_enabled", lambda user_id: False)
    monkeypatch.setattr(
        auth_2fa, "revoke_all_trusted_devices", lambda user_id: revoked.append(user_id)
    )
    return revoked


# ── Login ────────────────────────────────────────────────────────────────────

def test_login_success_returns_token_bound_to_the_user(client, stub_user, monkeypatch):
    monkeypatch.setattr("core.auth_2fa.is_2fa_enabled", lambda user_id: False)
    r = client.post("/api/login", json={"email": "admin@cakecrm.test", "password": CURRENT_PW})
    assert r.status_code == 200
    payload = auth.decode_access_token(r.json()["access_token"])
    assert payload["sub"] == str(ADMIN_ID)
    assert payload["pwd_epoch"] == 3
    # The role is deliberately NOT a claim — it would be stale the moment someone
    # is demoted, and the dependency reads it live instead.
    assert "role" not in payload


def test_login_wrong_password_401(client, stub_user):
    r = client.post("/api/login", json={"email": "admin@cakecrm.test", "password": "nope"})
    assert r.status_code == 401


def test_unknown_email_and_wrong_password_are_indistinguishable(client, stub_user, monkeypatch):
    """Different answers here would turn the login form into an account-enumeration oracle."""
    monkeypatch.setattr(users_service, "get_user_by_email", lambda email: None)
    unknown = client.post("/api/login", json={"email": "nobody@x.test", "password": "x"})

    stub_user["row"] = _user_row()
    monkeypatch.setattr(users_service, "get_user_by_email", lambda email: stub_user["row"])
    wrong = client.post("/api/login", json={"email": "admin@cakecrm.test", "password": "x"})

    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["detail"] == wrong.json()["detail"]


def test_unknown_email_still_spends_a_bcrypt_verification(client, monkeypatch):
    """Answering an unknown address faster than a known one leaks which exist."""
    monkeypatch.setattr(users_service, "get_user_by_email", lambda email: None)
    spent = []
    monkeypatch.setattr(users_service, "spend_dummy_verify", lambda: spent.append(True))

    client.post("/api/login", json={"email": "nobody@x.test", "password": "x"})
    assert spent == [True]


def test_deactivated_user_cannot_log_in(client, stub_user):
    stub_user["row"] = _user_row(is_active=False)
    r = client.post("/api/login", json={"email": "admin@cakecrm.test", "password": CURRENT_PW})
    assert r.status_code == 401


def test_login_fails_closed_when_the_user_store_is_unreadable(client, monkeypatch):
    """A DB outage must never resurrect the AUTH_PASSWORD bootstrap value."""
    def _boom(email):
        raise RuntimeError("Postgres pool not initialized")

    monkeypatch.setattr(users_service, "get_user_by_email", _boom)
    r = client.post("/api/login", json={"email": "admin@cakecrm.test", "password": "x"})
    assert r.status_code == 503


def test_login_is_rate_limited(client, stub_user):
    for _ in range(10):
        client.post("/api/login", json={"email": "admin@cakecrm.test", "password": "wrong"})
    r = client.post("/api/login", json={"email": "admin@cakecrm.test", "password": CURRENT_PW})
    assert r.status_code == 429


def test_2fa_challenge_pending_token_names_the_user(client, stub_user, monkeypatch):
    monkeypatch.setattr("core.auth_2fa.is_2fa_enabled", lambda user_id: True)
    monkeypatch.setattr("core.auth_2fa.is_device_trusted", lambda token, user_id: False)

    r = client.post("/api/login", json={"email": "admin@cakecrm.test", "password": CURRENT_PW})
    body = r.json()
    assert body["requires_2fa"] is True
    payload = auth.decode_access_token(body["pending_token"])
    assert payload["purpose"] == "2fa_pending"
    assert payload["sub"] == str(ADMIN_ID)
    assert payload["pwd_epoch"] == 3


# ── get_current_user ─────────────────────────────────────────────────────────

def test_valid_token_resolves_to_the_live_row(stub_user):
    token = auth.create_user_token(stub_user["row"])
    user = auth.get_current_user(_bearer(token))
    assert user["id"] == ADMIN_ID
    assert user["role"] == "admin"
    # The credential columns must never cross the dependency boundary.
    assert "password_hash" not in user
    assert "token_epoch" not in user


def test_pre_multi_user_token_is_rejected_not_crashed(stub_user):
    """Old tokens carry sub="user". That must 401, never 500 on the int cast."""
    stale = auth.create_access_token({"sub": "user", "role": "admin", "pwd_epoch": 0})
    with pytest.raises(HTTPException) as exc:
        auth.get_current_user(_bearer(stale))
    assert exc.value.status_code == 401


def test_stale_epoch_ends_the_session(stub_user):
    token = auth.create_user_token(stub_user["row"])
    stub_user["row"] = _user_row(token_epoch=4)  # someone changed the password
    with pytest.raises(HTTPException) as exc:
        auth.get_current_user(_bearer(token))
    assert exc.value.status_code == 401
    assert "password change" in exc.value.detail


def test_deactivation_takes_effect_on_the_next_request(stub_user):
    """The whole point of the per-request lookup: no waiting for the JWT to expire."""
    token = auth.create_user_token(stub_user["row"])
    stub_user["row"] = _user_row(is_active=False)
    with pytest.raises(HTTPException) as exc:
        auth.get_current_user(_bearer(token))
    assert exc.value.status_code == 401
    assert "deactivated" in exc.value.detail


def test_deleted_user_401s(stub_user, monkeypatch):
    token = auth.create_user_token(stub_user["row"])
    monkeypatch.setattr(users_service, "get_user", lambda uid: None)
    with pytest.raises(HTTPException) as exc:
        auth.get_current_user(_bearer(token))
    assert exc.value.status_code == 401


def test_database_outage_is_503_not_401(stub_user, monkeypatch):
    """401 would eject every signed-in user to a login screen they also can't use."""
    token = auth.create_user_token(stub_user["row"])

    def _boom(uid):
        raise RuntimeError("pool exhausted")

    monkeypatch.setattr(users_service, "get_user", _boom)
    with pytest.raises(HTTPException) as exc:
        auth.get_current_user(_bearer(token))
    assert exc.value.status_code == 503


def test_pending_2fa_token_cannot_be_used_for_api_access(stub_user):
    pending = auth.create_user_token(
        stub_user["row"], expire_minutes=5, purpose="2fa_pending"
    )
    with pytest.raises(HTTPException) as exc:
        auth.get_current_user(_bearer(pending))
    assert exc.value.status_code == 401


def test_require_admin_rejects_members(stub_user):
    member = {"id": 2, "email": "m@x.test", "name": "M", "role": "member", "is_active": True}
    with pytest.raises(HTTPException) as exc:
        auth.require_admin(member)
    assert exc.value.status_code == 403


def test_require_admin_passes_the_user_through(stub_user):
    admin = fake_admin()
    assert auth.require_admin(admin) is admin


# ── Change password ──────────────────────────────────────────────────────────

def _patch_change(monkeypatch, result):
    """Stub the transactional write and record its arguments."""
    calls = []

    def _change(user_id, current_plain, new_plain):
        calls.append((user_id, current_plain, new_plain))
        return result

    monkeypatch.setattr(users_service, "change_own_password", _change)
    return calls


def test_change_password_happy_path(client, stub_user, no_2fa, monkeypatch):
    calls = _patch_change(monkeypatch, 9)
    r = client.post(
        "/api/auth/change-password",
        json={"current_password": CURRENT_PW, "new_password": NEW_PW},
    )
    assert r.status_code == 200
    assert calls == [(ADMIN_ID, CURRENT_PW, NEW_PW)]


def test_trusted_devices_are_revoked_inside_the_password_transaction(
    client, stub_user, no_2fa, monkeypatch
):
    """As a second transaction after the write, a failed revocation returns 500 to a
    caller whose old JWT is already dead — no replacement token, and the promised
    revocation never happened. So change_own_password does it, and the route must
    NOT also call the standalone revoke."""
    _patch_change(monkeypatch, 9)
    client.post(
        "/api/auth/change-password",
        json={"current_password": CURRENT_PW, "new_password": NEW_PW},
    )
    assert no_2fa == [], "the route revoked separately instead of in-transaction"


def test_change_own_password_revokes_devices_in_one_transaction(monkeypatch, fake_conn):
    stored = users_service.hash_password(CURRENT_PW)
    conn = fake_conn(monkeypatch, users_service, fetchone_results=[(stored,), (9,)])
    assert users_service.change_own_password(ADMIN_ID, CURRENT_PW, NEW_PW) == 9
    sql = " | ".join(s for s, _ in conn.executed)
    assert "FOR UPDATE" in sql
    assert "DELETE FROM trusted_devices WHERE user_id = %s" in sql


def test_replacement_token_carries_the_epoch_the_write_produced(
    client, stub_user, no_2fa, monkeypatch
):
    """Guards a real race: re-reading the epoch after the write could pick up a
    concurrent change's later value and mint a token that outlives the password it
    was issued for. The row still reports epoch 3; the write returned 9."""
    _patch_change(monkeypatch, 9)
    r = client.post(
        "/api/auth/change-password",
        json={"current_password": CURRENT_PW, "new_password": NEW_PW},
    )
    payload = auth.decode_access_token(r.json()["access_token"])
    assert payload["pwd_epoch"] == 9
    assert payload["sub"] == str(ADMIN_ID)


def test_change_password_wrong_current_is_400_not_401(client, stub_user, no_2fa, monkeypatch):
    """401 would make the frontend treat a typo as an expired session and log you out."""
    _patch_change(monkeypatch, None)
    r = client.post(
        "/api/auth/change-password",
        json={"current_password": "wrong-password", "new_password": NEW_PW},
    )
    assert r.status_code == 400


def test_losing_the_row_lock_race_is_reported(client, stub_user, no_2fa, monkeypatch):
    """The advisory pre-check passed, but the locked re-check is authoritative."""
    _patch_change(monkeypatch, None)
    r = client.post(
        "/api/auth/change-password",
        json={"current_password": CURRENT_PW, "new_password": NEW_PW},
    )
    assert r.status_code == 400


@pytest.mark.parametrize(
    "body,fragment",
    [
        ({"current_password": CURRENT_PW, "new_password": "short"}, "at least"),
        ({"current_password": CURRENT_PW, "new_password": "x" * 73}, "at most"),
        ({"current_password": CURRENT_PW, "new_password": CURRENT_PW}, "must differ"),
    ],
)
def test_change_password_validation(client, stub_user, no_2fa, body, fragment):
    r = client.post("/api/auth/change-password", json=body)
    assert r.status_code == 400
    assert fragment in r.json()["detail"]


def test_change_password_is_rate_limited(client, stub_user, no_2fa, monkeypatch):
    _patch_change(monkeypatch, None)
    for _ in range(5):
        client.post(
            "/api/auth/change-password",
            json={"current_password": "wrong", "new_password": NEW_PW},
        )
    r = client.post(
        "/api/auth/change-password",
        json={"current_password": CURRENT_PW, "new_password": NEW_PW},
    )
    assert r.status_code == 429


# ── Change password with 2FA on ──────────────────────────────────────────────

@pytest.fixture
def with_2fa(monkeypatch):
    import core.auth_2fa as auth_2fa

    spent = []
    monkeypatch.setattr(auth_2fa, "is_2fa_enabled", lambda user_id: True)
    monkeypatch.setattr(auth_2fa, "revoke_all_trusted_devices", lambda user_id: None)
    monkeypatch.setattr(
        auth_2fa, "verify_totp_code",
        lambda user_id, code: (spent.append(("totp", code)), code == "123456")[1],
    )
    monkeypatch.setattr(
        auth_2fa, "consume_backup_code",
        lambda user_id, code: (spent.append(("backup", code)), code == "AAAA-BBBB")[1],
    )
    return spent


def test_change_password_requires_a_code_when_2fa_is_on(client, stub_user, with_2fa, monkeypatch):
    _patch_change(monkeypatch, 4)
    r = client.post(
        "/api/auth/change-password",
        json={"current_password": CURRENT_PW, "new_password": NEW_PW},
    )
    assert r.status_code == 400
    assert "Two-factor code required" in r.json()["detail"]


def test_change_password_accepts_totp_then_backup(client, stub_user, with_2fa, monkeypatch):
    _patch_change(monkeypatch, 4)
    assert client.post(
        "/api/auth/change-password",
        json={"current_password": CURRENT_PW, "new_password": NEW_PW, "code": "123456"},
    ).status_code == 200
    auth._attempts.clear()
    assert client.post(
        "/api/auth/change-password",
        json={"current_password": CURRENT_PW, "new_password": NEW_PW, "code": "AAAA-BBBB"},
    ).status_code == 200


def test_change_password_rejects_a_bad_code(client, stub_user, with_2fa, monkeypatch):
    _patch_change(monkeypatch, 4)
    r = client.post(
        "/api/auth/change-password",
        json={"current_password": CURRENT_PW, "new_password": NEW_PW, "code": "000000"},
    )
    assert r.status_code == 400
    assert "Invalid two-factor code" in r.json()["detail"]


def test_a_wrong_current_password_never_spends_the_2fa_code(
    client, stub_user, with_2fa, monkeypatch
):
    """Verifying a code consumes it. A typo in the password field must cost nothing."""
    _patch_change(monkeypatch, None)
    r = client.post(
        "/api/auth/change-password",
        json={"current_password": "wrong", "new_password": NEW_PW, "code": "AAAA-BBBB"},
    )
    assert r.status_code == 400
    assert with_2fa == [], "the backup code was consumed by a failed password check"


# ── AUTH_PASSWORD_RESET recovery lever ───────────────────────────────────────

def test_reset_lever_is_a_no_op_when_unset(monkeypatch, fake_conn):
    monkeypatch.setattr(auth.settings.auth, "password_reset", "")
    conn = fake_conn(monkeypatch, auth)
    auth.apply_password_reset_env()
    assert conn.executed == []


def test_reset_lever_restores_password_activation_and_clears_2fa(monkeypatch, fake_conn, caplog):
    """A password-only rescue is useless to an operator who also lost their authenticator."""
    monkeypatch.setattr(auth.settings.auth, "password_reset", "rescue-password")
    conn = fake_conn(
        monkeypatch, auth,
        fetchone_results=[(ADMIN_ID, "admin@cakecrm.test"), (ADMIN_ID,)],
    )
    with caplog.at_level("WARNING"):
        auth.apply_password_reset_env()

    sql = " | ".join(s for s, _ in conn.executed)
    assert "FOR UPDATE" in sql
    assert "is_active = TRUE" in sql
    assert "token_epoch = token_epoch + 1" in sql
    assert "UPDATE totp_config" in sql
    assert "DELETE FROM trusted_devices" in sql
    assert "REMOVE this variable" in caplog.text
    assert "Two-factor authentication was DISABLED" in caplog.text


def test_reset_lever_stays_quiet_about_2fa_when_none_was_enabled(monkeypatch, fake_conn, caplog):
    monkeypatch.setattr(auth.settings.auth, "password_reset", "rescue-password")
    # Second fetchone is the totp UPDATE ... RETURNING: no row means 2FA was already off.
    fake_conn(monkeypatch, auth, fetchone_results=[(ADMIN_ID, "admin@cakecrm.test"), None])
    with caplog.at_level("WARNING"):
        auth.apply_password_reset_env()
    assert "Two-factor authentication was DISABLED" not in caplog.text


def test_reset_lever_warns_about_a_short_password(monkeypatch, fake_conn, caplog):
    monkeypatch.setattr(auth.settings.auth, "password_reset", "short")
    fake_conn(monkeypatch, auth, fetchone_results=[(ADMIN_ID, "admin@cakecrm.test"), None])
    with caplog.at_level("WARNING"):
        auth.apply_password_reset_env()
    # Applied anyway — refusing would leave a locked-out operator with no lever.
    assert "shorter than" in caplog.text


def test_reset_lever_reports_an_install_with_no_admin(monkeypatch, fake_conn, caplog):
    monkeypatch.setattr(auth.settings.auth, "password_reset", "rescue-password")
    fake_conn(monkeypatch, auth, fetchone_results=[None])
    with caplog.at_level("ERROR"):
        auth.apply_password_reset_env()
    assert "no admin account" in caplog.text


def test_reset_lever_failure_does_not_stop_the_app_booting(monkeypatch, caplog):
    """Raising here would turn a failed rescue into a total outage."""
    monkeypatch.setattr(auth.settings.auth, "password_reset", "rescue-password")

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(auth, "get_connection", _boom)
    with caplog.at_level("ERROR"):
        auth.apply_password_reset_env()
    assert "could not be reset" in caplog.text


# ── Rate limiting must not become a self-inflicted DoS ───────────────────────

def test_one_account_being_hammered_does_not_lock_out_the_team(client, stub_user, monkeypatch):
    """Everyone in an office shares a NAT address. With a single per-IP bucket, one
    colleague fat-fingering their password ten times would lock out the company."""
    users = {
        "a@x.test": _user_row(id=1, email="a@x.test"),
        "b@x.test": _user_row(id=2, email="b@x.test"),
    }
    monkeypatch.setattr(users_service, "get_user_by_email", lambda e: users.get(e.strip().lower()))
    monkeypatch.setattr("core.auth_2fa.is_2fa_enabled", lambda user_id: False)

    for _ in range(10):
        client.post("/api/login", json={"email": "a@x.test", "password": "wrong"})

    # That account is now limited...
    assert client.post(
        "/api/login", json={"email": "a@x.test", "password": CURRENT_PW}
    ).status_code == 429
    # ...but their colleague on the same address is not.
    assert client.post(
        "/api/login", json={"email": "b@x.test", "password": CURRENT_PW}
    ).status_code == 200


def test_the_account_key_is_normalized(client, stub_user, monkeypatch):
    """Otherwise 'Rep@X.test' and 'rep@x.test' are separate buckets and the limit is
    trivially bypassed by changing the casing."""
    monkeypatch.setattr("core.auth_2fa.is_2fa_enabled", lambda user_id: False)
    for _ in range(10):
        client.post("/api/login", json={"email": "admin@cakecrm.test", "password": "wrong"})
    assert client.post(
        "/api/login", json={"email": "  ADMIN@CakeCRM.TEST  ", "password": CURRENT_PW}
    ).status_code == 429
