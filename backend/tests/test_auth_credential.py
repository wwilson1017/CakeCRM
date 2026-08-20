"""DB-backed login credential + in-app password change (issue #78).

Hermetic: the auth module's Postgres access is stubbed (pg_fetchone for the read
path, the fake connection for the FOR UPDATE transaction). bcrypt runs for real,
so the round trips below prove actual hashes verify.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import bcrypt
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from jose import jwt

from core import auth
from core.auth import get_current_user


def _bearer(token: str):
    """Minimal stand-in for the Request object get_current_user reads headers from."""
    return SimpleNamespace(headers={"Authorization": f"Bearer {token}"})


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(auth.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: {"sub": "user", "role": "admin"}
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _reset_module_state():
    """The limiter and the epoch cache are module-level; they'd leak across tests."""
    auth._attempts.clear()
    auth._token_epoch = None
    yield
    auth._attempts.clear()
    auth._token_epoch = None


CURRENT_PW = "old-password"


@pytest.fixture
def no_2fa(monkeypatch):
    """Default the 2FA seam off, and record trusted-device revocations."""
    import core.auth_2fa as auth_2fa

    revoked = []
    monkeypatch.setattr(auth_2fa, "is_2fa_enabled", lambda: False)
    monkeypatch.setattr(auth_2fa, "revoke_all_trusted_devices", lambda: revoked.append(True))
    # The endpoint pre-checks the current password before spending a 2FA code.
    monkeypatch.setattr(auth, "verify_password", lambda plain: plain == CURRENT_PW)
    return revoked


def _stub_stored_hash(monkeypatch, value):
    """Point core.auth's credential read at `value` (None = no DB credential yet)."""
    row = None if value is None else {"password_hash": value}
    monkeypatch.setattr(auth, "pg_fetchone", lambda sql, params=(): row)


# ── verify_password: resolution order ────────────────────────────────────────

def test_db_hash_wins_over_env(monkeypatch):
    """Once a DB hash exists, AUTH_PASSWORD is inert — the whole point of #78."""
    monkeypatch.setattr(auth.settings.auth, "password", "env-password")
    _stub_stored_hash(monkeypatch, auth._hash_password("db-password"))

    assert auth.verify_password("db-password") is True
    assert auth.verify_password("env-password") is False


def test_falls_back_to_plaintext_env_when_no_db_hash(monkeypatch):
    monkeypatch.setattr(auth.settings.auth, "password", "env-password")
    _stub_stored_hash(monkeypatch, None)

    assert auth.verify_password("env-password") is True
    assert auth.verify_password("wrong") is False


def test_falls_back_to_bcrypt_env_when_no_db_hash(monkeypatch):
    hashed = bcrypt.hashpw(b"env-password", bcrypt.gensalt()).decode()
    monkeypatch.setattr(auth.settings.auth, "password", hashed)
    _stub_stored_hash(monkeypatch, None)

    assert auth.verify_password("env-password") is True
    assert auth.verify_password("wrong") is False


def test_absent_row_falls_back_to_env(monkeypatch):
    """A pre-migration/empty table must behave exactly like the old env-only build."""
    monkeypatch.setattr(auth.settings.auth, "password", "env-password")
    monkeypatch.setattr(auth, "pg_fetchone", lambda sql, params=(): None)

    assert auth.verify_password("env-password") is True


def test_login_fails_closed_when_credential_unreadable(monkeypatch, client):
    """A DB outage must not resurrect the superseded AUTH_PASSWORD."""
    monkeypatch.setattr(auth.settings.auth, "password", "env-password")

    def _boom(sql, params=()):
        raise RuntimeError("Postgres pool not initialized")

    monkeypatch.setattr(auth, "pg_fetchone", _boom)

    r = client.post("/api/login", json={"password": "env-password"})
    assert r.status_code == 503


# ── set_password: transactional check-then-write ─────────────────────────────

def test_set_password_locks_row_and_writes(monkeypatch, fake_conn):
    conn = fake_conn(
        monkeypatch, auth,
        fetchone_results=[(auth._hash_password("old-password"),), (1,)],
    )

    assert auth.set_password("old-password", "new-password") is True

    select_sql, _ = conn.executed[0]
    assert "FOR UPDATE" in select_sql
    insert_sql, params = conn.executed[1]
    assert "auth_credential" in insert_sql and "ON CONFLICT" in insert_sql
    # The stored value is a real hash of the new password, not the plaintext.
    assert params[0] != "new-password"
    assert bcrypt.checkpw(b"new-password", params[0].encode())


def test_set_password_rejects_wrong_current_and_writes_nothing(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, auth, fetchone_results=[(auth._hash_password("old-password"),)])

    assert auth.set_password("not-the-password", "new-password") is False
    assert len(conn.executed) == 1  # the SELECT only


def test_set_password_accepts_env_password_on_first_change(monkeypatch, fake_conn):
    """Bootstrap: the seeded row holds a NULL hash, so the env var authorizes change #1."""
    monkeypatch.setattr(auth.settings.auth, "password", "env-password")
    conn = fake_conn(monkeypatch, auth, fetchone_results=[(None,), (1,)])

    assert auth.set_password("env-password", "new-password") is True
    assert len(conn.executed) == 2


def test_set_password_bumps_the_token_epoch_in_the_same_statement(monkeypatch, fake_conn):
    """The epoch must move with the hash, or a session could outlive its password."""
    conn = fake_conn(
        monkeypatch, auth,
        fetchone_results=[(auth._hash_password("old-password"),), (7,)],
    )

    assert auth.set_password("old-password", "new-password") is True

    write_sql, _ = conn.executed[1]
    assert "token_epoch" in write_sql and "RETURNING token_epoch" in write_sql
    assert auth.current_token_epoch() == 7  # cache updated without another read


def test_set_password_round_trip(monkeypatch, fake_conn):
    """The hash set_password writes is one verify_password later accepts."""
    conn = fake_conn(
        monkeypatch, auth,
        fetchone_results=[(auth._hash_password("old-password"),), (1,)],
    )
    auth.set_password("old-password", "new-password")
    stored = conn.executed[1][1][0]

    _stub_stored_hash(monkeypatch, stored)
    assert auth.verify_password("new-password") is True
    assert auth.verify_password("old-password") is False


# ── Password epoch: session invalidation ─────────────────────────────────────

async def test_token_carries_the_current_epoch(monkeypatch):
    monkeypatch.setattr(auth, "_token_epoch", 4)
    payload = auth.decode_access_token(auth.create_access_token({"sub": "user"}))
    assert payload["pwd_epoch"] == 4


async def test_session_from_a_previous_epoch_is_rejected(monkeypatch):
    """A device holding a token minted before the password changed must be signed out."""
    monkeypatch.setattr(auth, "_token_epoch", 1)
    stale = auth.create_access_token({"sub": "user", "role": "admin"})

    monkeypatch.setattr(auth, "_token_epoch", 2)
    monkeypatch.setattr(auth, "pg_fetchone", lambda sql, params=(): {"token_epoch": 2})

    with pytest.raises(HTTPException) as exc:
        await auth.get_current_user(_bearer(stale))
    assert exc.value.status_code == 401
    assert "password change" in exc.value.detail.lower()


async def test_current_session_survives(monkeypatch):
    monkeypatch.setattr(auth, "_token_epoch", 3)
    token = auth.create_access_token({"sub": "user", "role": "admin"})

    payload = await auth.get_current_user(_bearer(token))
    assert payload["sub"] == "user"


async def test_pre_upgrade_token_without_the_claim_still_works(monkeypatch):
    """Deploying this must not sign everyone out — only a password change should."""
    monkeypatch.setattr(auth, "_token_epoch", 0)
    legacy = jwt.encode(
        {"sub": "user", "role": "admin",
         "exp": datetime.now(timezone.utc) + timedelta(minutes=60)},
        auth.settings.jwt.secret_key, algorithm=auth.settings.jwt.algorithm,
    )
    assert "pwd_epoch" not in auth.decode_access_token(legacy)

    payload = await auth.get_current_user(_bearer(legacy))
    assert payload["sub"] == "user"


async def test_stale_cache_is_refreshed_instead_of_signing_a_valid_session_out(monkeypatch):
    """Guards a multi-worker deploy: a worker that missed the bump must re-read,
    not reject a session that is actually current."""
    monkeypatch.setattr(auth, "_token_epoch", 5)
    token = auth.create_access_token({"sub": "user", "role": "admin"})

    monkeypatch.setattr(auth, "_token_epoch", 2)  # this worker missed the change
    reads = []

    def _live(sql, params=()):
        reads.append(sql)
        return {"token_epoch": 5}

    monkeypatch.setattr(auth, "pg_fetchone", _live)

    payload = await auth.get_current_user(_bearer(token))
    assert payload["sub"] == "user"
    assert len(reads) == 1  # re-read only on mismatch
    assert auth.current_token_epoch() == 5  # and the cache self-healed


async def test_matching_epoch_costs_no_database_read(monkeypatch):
    """The whole point of caching: the happy path must not touch Postgres."""
    monkeypatch.setattr(auth, "_token_epoch", 9)
    token = auth.create_access_token({"sub": "user", "role": "admin"})

    def _explode(sql, params=()):
        raise AssertionError("get_current_user read the database on the happy path")

    monkeypatch.setattr(auth, "pg_fetchone", _explode)
    assert (await auth.get_current_user(_bearer(token)))["sub"] == "user"


# ── POST /api/auth/change-password ───────────────────────────────────────────

def test_change_password_happy_path(monkeypatch, client, no_2fa):
    monkeypatch.setattr(auth, "set_password", lambda cur, new: True)

    r = client.post(
        "/api/auth/change-password",
        json={"current_password": CURRENT_PW, "new_password": "new-password"},
    )
    assert r.status_code == 200
    # A fresh token keeps the acting session alive across the change.
    assert auth.decode_access_token(r.json()["access_token"])["role"] == "admin"
    assert no_2fa == [True]  # other devices must re-do 2FA


def test_change_password_wrong_current_is_400_not_401(monkeypatch, client, no_2fa):
    """401 would make the frontend api() wrapper eject the user to /login on a typo."""
    monkeypatch.setattr(auth, "set_password", lambda cur, new: False)

    r = client.post(
        "/api/auth/change-password",
        json={"current_password": "wrong", "new_password": "new-password"},
    )
    assert r.status_code == 400
    assert "current password" in r.json()["detail"].lower()


def test_concurrent_change_losing_the_row_lock_is_reported(monkeypatch, client, no_2fa):
    """The advisory pre-check can pass and set_password still lose the race; the
    locked re-check is authoritative, so the caller must still get an error."""
    monkeypatch.setattr(auth, "set_password", lambda cur, new: False)

    r = client.post(
        "/api/auth/change-password",
        json={"current_password": CURRENT_PW, "new_password": "new-password"},
    )
    assert r.status_code == 400


def test_change_password_rejects_short_password(client, no_2fa):
    r = client.post(
        "/api/auth/change-password",
        json={"current_password": CURRENT_PW, "new_password": "short"},
    )
    assert r.status_code == 400
    assert str(auth.MIN_PASSWORD_LENGTH) in r.json()["detail"]


def test_change_password_rejects_over_bcrypt_limit(client, no_2fa):
    """bcrypt ignores bytes past 72, so accepting them would overstate the strength."""
    r = client.post(
        "/api/auth/change-password",
        json={"current_password": CURRENT_PW, "new_password": "a" * 73},
    )
    assert r.status_code == 400


def test_change_password_rejects_unchanged_password(client, no_2fa):
    r = client.post(
        "/api/auth/change-password",
        json={"current_password": "same-password", "new_password": "same-password"},
    )
    assert r.status_code == 400


def test_change_password_rate_limited(monkeypatch, client, no_2fa):
    monkeypatch.setattr(auth, "set_password", lambda cur, new: False)
    body = {"current_password": "wrong", "new_password": "new-password"}

    for _ in range(5):
        assert client.post("/api/auth/change-password", json=body).status_code == 400
    assert client.post("/api/auth/change-password", json=body).status_code == 429


def test_change_password_route_requires_auth():
    route = next(r for r in auth.router.routes if getattr(r, "path", "") == "/auth/change-password")
    assert "get_current_user" in [d.call.__name__ for d in route.dependant.dependencies]


# ── 2FA interaction ──────────────────────────────────────────────────────────

@pytest.fixture
def with_2fa(monkeypatch):
    import core.auth_2fa as auth_2fa

    monkeypatch.setattr(auth_2fa, "is_2fa_enabled", lambda: True)
    monkeypatch.setattr(auth_2fa, "revoke_all_trusted_devices", lambda: None)
    # Both stubs mirror the real semantics — they only accept the current password —
    # so a test can't pass merely because a stub was unconditionally permissive.
    monkeypatch.setattr(auth, "set_password", lambda cur, new: cur == CURRENT_PW)
    monkeypatch.setattr(auth, "verify_password", lambda plain: plain == CURRENT_PW)
    return auth_2fa


def test_change_password_requires_code_when_2fa_enabled(client, with_2fa):
    r = client.post(
        "/api/auth/change-password",
        json={"current_password": CURRENT_PW, "new_password": "new-password"},
    )
    assert r.status_code == 400
    assert "two-factor" in r.json()["detail"].lower()


def test_change_password_accepts_valid_totp(monkeypatch, client, with_2fa):
    monkeypatch.setattr(with_2fa, "verify_totp_code", lambda code: code == "123456")

    r = client.post(
        "/api/auth/change-password",
        json={"current_password": CURRENT_PW, "new_password": "new-password", "code": "123456"},
    )
    assert r.status_code == 200


def test_change_password_accepts_backup_code(monkeypatch, client, with_2fa):
    """Someone who lost their authenticator must still be able to rotate a leaked password."""
    monkeypatch.setattr(with_2fa, "verify_totp_code", lambda code: False)
    monkeypatch.setattr(with_2fa, "consume_backup_code", lambda code: code == "ABCD-1234")

    r = client.post(
        "/api/auth/change-password",
        json={"current_password": CURRENT_PW, "new_password": "new-password", "code": "ABCD-1234"},
    )
    assert r.status_code == 200


def test_change_password_rejects_bad_code(monkeypatch, client, with_2fa):
    monkeypatch.setattr(with_2fa, "verify_totp_code", lambda code: False)
    monkeypatch.setattr(with_2fa, "consume_backup_code", lambda code: False)

    r = client.post(
        "/api/auth/change-password",
        json={"current_password": CURRENT_PW, "new_password": "new-password", "code": "000000"},
    )
    assert r.status_code == 400


def test_wrong_current_password_does_not_spend_the_2fa_code(monkeypatch, client, with_2fa):
    """A typo in the current-password field must not burn a single-use backup code.

    verify_totp_code marks the timeslot used and consume_backup_code destroys the
    code, so both must stay unreached until the current password has been checked.
    """
    spent = []
    monkeypatch.setattr(with_2fa, "verify_totp_code", lambda code: spent.append("totp") or True)
    monkeypatch.setattr(with_2fa, "consume_backup_code", lambda code: spent.append("backup") or True)

    r = client.post(
        "/api/auth/change-password",
        json={"current_password": "wrong", "new_password": "new-password", "code": "ABCD-1234"},
    )
    assert r.status_code == 400
    assert "current password" in r.json()["detail"].lower()
    assert spent == [], f"a 2FA code was consumed on a wrong-password request: {spent}"


# ── Operator recovery lever ──────────────────────────────────────────────────

def test_password_reset_env_noop_when_unset(monkeypatch, fake_conn):
    monkeypatch.setattr(auth.settings.auth, "password_reset", "")
    conn = fake_conn(monkeypatch, auth)

    auth.apply_password_reset_env()
    assert conn.executed == []


def test_password_reset_env_overwrites_hash_and_warns(monkeypatch, fake_conn, caplog):
    monkeypatch.setattr(auth.settings.auth, "password_reset", "rescue-password")
    conn = fake_conn(monkeypatch, auth, fetchone_results=[(3,)])

    with caplog.at_level("WARNING"):
        auth.apply_password_reset_env()

    sql, params = conn.executed[0]
    assert "auth_credential" in sql and "ON CONFLICT" in sql
    assert bcrypt.checkpw(b"rescue-password", params[0].encode())
    # A rescue must also end outstanding sessions, not just change the password.
    assert "token_epoch" in sql
    assert auth.current_token_epoch() == 3
    # The operator must be told to remove it, or every restart re-resets the password.
    assert "REMOVE this variable" in caplog.text


def test_password_reset_env_keeps_booting_when_the_write_fails(monkeypatch, caplog):
    """A failed rescue must not take the app down — the old password still works."""
    monkeypatch.setattr(auth.settings.auth, "password_reset", "rescue-password")

    def _boom():
        raise RuntimeError("Postgres pool not initialized")

    monkeypatch.setattr(auth, "get_connection", _boom)

    with caplog.at_level("ERROR"):
        auth.apply_password_reset_env()  # must not raise

    assert "could not be reset" in caplog.text
    # The misleading success warning must NOT have been emitted.
    assert "has been reset to its value" not in caplog.text


def test_password_reset_env_applies_short_password_with_extra_warning(monkeypatch, fake_conn, caplog):
    """Refusing a short value would leave a locked-out operator with no lever at all."""
    monkeypatch.setattr(auth.settings.auth, "password_reset", "short")
    conn = fake_conn(monkeypatch, auth, fetchone_results=[(1,)])

    with caplog.at_level("WARNING"):
        auth.apply_password_reset_env()

    assert len(conn.executed) == 1
    assert "shorter than" in caplog.text
