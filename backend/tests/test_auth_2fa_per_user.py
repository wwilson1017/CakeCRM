"""Per-user two-factor authentication (issue #60 Phase A).

Before #60 `totp_config` was pinned to one row and `trusted_devices` had no user
column at all, so with real accounts every seat would have shared one authenticator
secret, one replay slot, one set of backup codes and one trust list.

These tests pin the scoping. The failure mode is not an error — it is one person's
second factor quietly accepting another person's codes, or a colleague's browser
skipping 2FA entirely — so each test asserts the user_id actually reaches the SQL.
"""

import json

import bcrypt
import pytest

from core import auth_2fa

USER_A = 11
USER_B = 22


def _sql(conn):
    return " | ".join(s for s, _ in conn.executed)


def _params(conn):
    return [p for _, p in conn.executed]


# ── Reads are scoped ─────────────────────────────────────────────────────────

def test_config_read_is_scoped_to_the_user(monkeypatch):
    captured = {}

    def _fetchone(sql, params=()):
        captured["sql"], captured["params"] = " ".join(sql.split()), params
        return None

    monkeypatch.setattr(auth_2fa, "pg_fetchone", _fetchone)
    auth_2fa.get_totp_config(USER_A)
    assert "WHERE user_id = %s" in captured["sql"]
    assert captured["params"] == (USER_A,)


def test_2fa_enabled_is_per_user(monkeypatch):
    enabled_for = {USER_A: {"enabled": True}, USER_B: None}
    monkeypatch.setattr(
        auth_2fa, "get_totp_config", lambda uid: enabled_for[uid]
    )
    assert auth_2fa.is_2fa_enabled(USER_A) is True
    assert auth_2fa.is_2fa_enabled(USER_B) is False


# ── Backup codes cannot cross accounts ───────────────────────────────────────

def test_backup_code_consumption_is_scoped_to_the_user(monkeypatch, fake_conn):
    """The row lock and the write must BOTH name the user, or one person's code
    could be spent against another's list."""
    code_hash = bcrypt.hashpw(b"AAAABBBB", bcrypt.gensalt()).decode()
    conn = fake_conn(
        monkeypatch, auth_2fa, fetchone_results=[(json.dumps([code_hash]),)]
    )

    assert auth_2fa.consume_backup_code(USER_A, "AAAA-BBBB") is True

    select_sql, select_params = conn.executed[0]
    assert "WHERE user_id = %s FOR UPDATE" in select_sql
    assert select_params == (USER_A,)
    update_sql, update_params = conn.executed[1]
    assert "WHERE user_id = %s" in update_sql
    assert update_params[1] == USER_A
    # The consumed code is gone from the stored list.
    assert json.loads(update_params[0]) == []


def test_a_user_with_no_config_consumes_nothing(monkeypatch, fake_conn):
    fake_conn(monkeypatch, auth_2fa, fetchone_results=[None])
    assert auth_2fa.consume_backup_code(USER_B, "AAAA-BBBB") is False


def test_a_wrong_backup_code_leaves_the_list_intact(monkeypatch, fake_conn):
    code_hash = bcrypt.hashpw(b"AAAABBBB", bcrypt.gensalt()).decode()
    conn = fake_conn(monkeypatch, auth_2fa, fetchone_results=[(json.dumps([code_hash]),)])
    assert auth_2fa.consume_backup_code(USER_A, "ZZZZ-YYYY") is False
    assert len(conn.executed) == 1  # only the SELECT ran


# ── TOTP verification is scoped ──────────────────────────────────────────────

def test_totp_verification_reads_and_writes_one_users_row(monkeypatch, fake_conn):
    import pyotp

    secret = pyotp.random_base32()
    code = pyotp.TOTP(secret).now()
    conn = fake_conn(
        monkeypatch, auth_2fa,
        fetchone_results=[("enc", "", USER_A)],
        description=["secret_enc", "last_used_at", "user_id"],
    )
    monkeypatch.setattr(auth_2fa, "decrypt_value", lambda v: secret)

    assert auth_2fa.verify_totp_code(USER_A, code) is True

    assert conn.executed[0][1] == (USER_A,)
    assert "WHERE user_id = %s FOR UPDATE" in conn.executed[0][0]
    # The replay watermark is stamped on THAT user's row.
    assert conn.executed[1][1][1] == USER_A


def test_a_replayed_code_is_rejected(monkeypatch, fake_conn):
    import pyotp

    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    code = totp.now()
    slot = str(totp.timecode(__import__("datetime").datetime.now(__import__("datetime").timezone.utc)))
    fake_conn(
        monkeypatch, auth_2fa,
        fetchone_results=[("enc", slot, USER_A)],
        description=["secret_enc", "last_used_at", "user_id"],
    )
    monkeypatch.setattr(auth_2fa, "decrypt_value", lambda v: secret)
    assert auth_2fa.verify_totp_code(USER_A, code) is False


# ── Trusted devices are per-user ─────────────────────────────────────────────

def test_a_trust_cookie_is_only_valid_for_its_own_account(monkeypatch):
    """Two people sharing a browser profile must not inherit each other's 2FA trust,
    and an unclaimed pre-#60 row (user_id NULL) must match nobody."""
    captured = {}

    def _fetchone(sql, params=()):
        captured["sql"], captured["params"] = " ".join(sql.split()), params
        return None

    monkeypatch.setattr(auth_2fa, "pg_fetchone", _fetchone)
    auth_2fa.is_device_trusted("cookie-value", USER_A)
    assert "user_id = %s" in captured["sql"]
    assert captured["params"][1] == USER_A


def test_an_empty_cookie_is_never_trusted(monkeypatch):
    monkeypatch.setattr(auth_2fa, "pg_fetchone", lambda *a, **k: {"trusted": 1})
    assert auth_2fa.is_device_trusted("", USER_A) is False


def test_adding_a_trusted_device_records_its_owner(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        auth_2fa, "pg_execute",
        lambda sql, params=(): captured.update(sql=" ".join(sql.split()), params=params),
    )
    auth_2fa.add_trusted_device(USER_A, "tok", "Chrome", "2026-09-01T00:00:00Z")
    assert "user_id" in captured["sql"]
    assert captured["params"][1] == USER_A


def test_revoking_devices_never_touches_other_accounts(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        auth_2fa, "pg_execute",
        lambda sql, params=(): captured.update(sql=" ".join(sql.split()), params=params),
    )
    auth_2fa.revoke_all_trusted_devices(USER_A)
    assert captured["sql"] == "DELETE FROM trusted_devices WHERE user_id = %s"
    assert captured["params"] == (USER_A,)


# ── Enabling and disabling are atomic ────────────────────────────────────────

def test_enabling_2fa_wipes_trusted_devices_in_the_same_transaction(monkeypatch, fake_conn):
    """As two statements, a failure between them leaves 2FA on while old trust
    cookies still match — and those cookies are exactly what skips the second factor."""
    conn = fake_conn(monkeypatch, auth_2fa)
    auth_2fa.save_totp_config(USER_A, "enc", "[]")
    sql = _sql(conn)
    assert "INSERT INTO totp_config" in sql
    assert "DELETE FROM trusted_devices WHERE user_id = %s" in sql
    assert conn.executed[-1][1] == (USER_A,)


def test_enabling_2fa_resets_the_replay_watermark(monkeypatch, fake_conn):
    """A high watermark from the OLD secret would reject the new authenticator's
    first codes."""
    conn = fake_conn(monkeypatch, auth_2fa)
    auth_2fa.save_totp_config(USER_A, "enc", "[]")
    assert "last_used_at = ''" in conn.executed[0][0]


def test_disabling_2fa_is_atomic_with_the_device_wipe(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, auth_2fa)
    auth_2fa.disable_totp(USER_A)
    sql = _sql(conn)
    assert "UPDATE totp_config SET enabled = FALSE" in sql
    assert "DELETE FROM trusted_devices WHERE user_id = %s" in sql
    assert all(p == (USER_A,) or USER_A in p for p in _params(conn))


def test_regenerating_backup_codes_produces_distinct_hashed_codes():
    codes, hashes = auth_2fa._generate_backup_codes()
    assert len(codes) == len(hashes) == auth_2fa.BACKUP_CODE_COUNT
    assert len(set(codes)) == len(codes)
    # Stored hashed, never plaintext.
    assert all(h.startswith("$2b$") for h in hashes)
    raw = codes[0].replace("-", "").encode()
    assert bcrypt.checkpw(raw, hashes[0].encode())


# ── The authenticator entry names the account ────────────────────────────────

@pytest.mark.asyncio
async def test_setup_labels_the_entry_with_the_users_email():
    """With several seats on one install, "CakeCRM: user" is indistinguishable."""
    from urllib.parse import unquote

    result = await auth_2fa.setup_2fa(
        user={"id": USER_A, "email": "rep@team.test", "role": "member"}
    )
    uri = result["provisioning_uri"]
    assert uri.startswith("otpauth://totp/")
    # pyotp percent-encodes the label, so compare the decoded form.
    assert "CakeCRM:rep@team.test" in unquote(uri)
