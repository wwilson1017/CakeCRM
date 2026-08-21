"""User accounts service (issue #60 Phase A).

Hermetic: Postgres access is stubbed. bcrypt runs for real.

The two invariants worth guarding hardest are the ones whose failures are silent:
email normalization must agree byte-for-byte with the SQL unique index, and password
verification must fail CLOSED on anything that is not a bcrypt hash.
"""

import pytest

from users import service

# ── Email normalization: Python and SQL must agree ───────────────────────────

def test_normalization_lowercases_and_trims_ascii_whitespace():
    assert service.normalize_email("  Admin@Example.COM\t\n") == "admin@example.com"


def test_normalization_leaves_non_ascii_whitespace_alone():
    """The SQL side is btrim(email, E' \\t\\n\\r\\f\\x0b') — six ASCII bytes.

    Python's str.strip() with no argument would also strip NBSP and friends, so the
    two sides would disagree about what a duplicate is. cake_os shipped that exact
    bug: an NBSP pasted from a spreadsheet made owner filters silently match zero
    rows. Here the NBSP survives on BOTH sides and simply reads as part of the
    address, which is consistent even if it looks odd.
    """
    nbsp = " "
    assert service.normalize_email(f"{nbsp}admin@example.com") == f"{nbsp}admin@example.com"
    assert service._WS == " \t\n\r\f\v"
    # The SQL fragment must name the same six bytes.
    assert service.SQL_EMAIL == "LOWER(btrim(email, E' \\t\\n\\r\\f\\x0b'))"


def test_normalization_survives_none_and_empty():
    assert service.normalize_email("") == ""
    assert service.normalize_email(None) == ""


# ── Password verification fails closed ───────────────────────────────────────

def test_bcrypt_round_trip():
    h = service.hash_password("correct-horse")
    assert service.verify_user_password("correct-horse", h) is True
    assert service.verify_user_password("wrong", h) is False


@pytest.mark.parametrize("stored", [None, "", "not-a-hash", "!", "changeme", "$1$oldmd5$xx"])
def test_non_bcrypt_values_never_authenticate(stored):
    """A '!' sentinel is how #61's importer parks a rep who has no seat yet: the row
    preserves attribution and can be activated later, but must never log in. The same
    guard covers a corrupted or half-migrated hash — there is no plaintext fallback."""
    assert service.verify_user_password("anything", stored) is False
    assert service.verify_user_password(stored or "", stored) is False


def test_malformed_bcrypt_is_a_failed_check_not_a_crash():
    assert service.verify_user_password("x", "$2b$notreallyahash") is False


def test_dummy_verify_is_a_real_bcrypt_check():
    """It exists to equalize timing, so it has to actually cost a verification."""
    assert service._DUMMY_HASH.startswith("$2b$")
    service.spend_dummy_verify()  # must not raise


# ── public_view ──────────────────────────────────────────────────────────────

def test_public_view_strips_credential_columns():
    row = {
        "id": 1, "email": "a@b.c", "name": "A", "role": "admin", "is_active": True,
        "password_hash": "$2b$secret", "token_epoch": 7,
    }
    assert service.public_view(row) == {
        "id": 1, "email": "a@b.c", "name": "A", "role": "admin", "is_active": True,
    }


def test_list_users_never_selects_the_password_hash(monkeypatch):
    captured = {}

    def _fetchall(sql, params=()):
        captured["sql"] = sql
        return []

    monkeypatch.setattr(service, "pg_fetchall", _fetchall)
    service.list_users()
    assert "password_hash" not in captured["sql"]
    assert "token_epoch" not in captured["sql"]


def test_list_users_includes_inactive_by_default(monkeypatch):
    """Historical owner/actor labels must still resolve for a departed rep."""
    captured = {}
    monkeypatch.setattr(service, "pg_fetchall", lambda sql, params=(): captured.setdefault("sql", sql) or [])
    service.list_users()
    assert "WHERE is_active" not in captured["sql"]


# ── create_user ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("email", ["", "   ", "no-at-sign"])
def test_create_rejects_a_bad_email(email):
    with pytest.raises(service.UserError):
        service.create_user(email, "N", "password123")


def test_create_rejects_an_unknown_role():
    with pytest.raises(service.UserError) as exc:
        service.create_user("a@b.c", "N", "password123", role="superuser")
    assert "admin" in str(exc.value)


def test_duplicate_email_is_reported_from_the_index_not_a_pre_check(monkeypatch, fake_conn):
    """A check-then-insert races two concurrent creates onto the same address, so the
    unique index stays the authority and its violation is translated here."""
    class _Boom:
        def cursor(self):
            raise Exception('duplicate key value violates unique constraint "uq_users_email_ci"')

    from contextlib import contextmanager

    @contextmanager
    def _conn():
        yield _Boom()

    monkeypatch.setattr(service, "get_connection", _conn)
    with pytest.raises(service.UserError) as exc:
        service.create_user("a@b.c", "N", "password123")
    assert "already exists" in str(exc.value)


def test_an_unrelated_database_error_is_not_swallowed(monkeypatch):
    from contextlib import contextmanager

    class _Boom:
        def cursor(self):
            raise Exception("connection reset by peer")

    @contextmanager
    def _conn():
        yield _Boom()

    monkeypatch.setattr(service, "get_connection", _conn)
    with pytest.raises(Exception) as exc:
        service.create_user("a@b.c", "N", "password123")
    assert not isinstance(exc.value, service.UserError)


# ── update_user: the last-admin guard ────────────────────────────────────────

def _rows_for(admin_ids):
    return [[(i,) for i in admin_ids]]


# The column list update_user's RETURNING clause produces, for row_to_dict.
USER_COLS = ["id", "email", "name", "role", "is_active", "created_at", "updated_at"]


def test_demoting_the_last_admin_is_refused(monkeypatch, fake_conn):
    fake_conn(monkeypatch, service, fetchall_results=_rows_for([1]))
    with pytest.raises(service.UserError) as exc:
        service.update_user(1, role="member")
    assert "only active admin" in str(exc.value)


def test_deactivating_the_last_admin_is_refused(monkeypatch, fake_conn):
    fake_conn(monkeypatch, service, fetchall_results=_rows_for([1]))
    with pytest.raises(service.UserError):
        service.update_user(1, is_active=False)


def test_demoting_one_of_two_admins_is_allowed(monkeypatch, fake_conn):
    conn = fake_conn(
        monkeypatch, service,
        fetchall_results=_rows_for([1, 2]),
        fetchone_results=[(1, "a@b.c", "A", "member", True, "t", "t")],
        description=USER_COLS,
    )
    service.update_user(1, role="member")
    assert any("UPDATE users SET role" in s for s, _ in conn.executed)


def test_demoting_a_member_never_consults_the_admin_count_guard(monkeypatch, fake_conn):
    """A member losing nothing must not be blocked because they aren't an admin."""
    conn = fake_conn(
        monkeypatch, service,
        fetchall_results=_rows_for([1]),
        fetchone_results=[(5, "m@b.c", "M", "member", True, "t", "t")],
        description=USER_COLS,
    )
    service.update_user(5, name="Renamed")
    assert conn.executed  # got past the guard


def test_the_admin_lock_is_ordered_to_avoid_deadlock(monkeypatch, fake_conn):
    """Two concurrent demotions grabbing the same rows in different orders deadlock,
    and Postgres gives no ordering guarantee for FOR UPDATE without ORDER BY."""
    conn = fake_conn(
        monkeypatch, service,
        fetchall_results=_rows_for([1, 2]),
        fetchone_results=[(2, "b@b.c", "B", "member", True, "t", "t")],
        description=USER_COLS,
    )
    service.update_user(2, role="member")
    lock_sql = conn.executed[0][0]
    assert "FOR UPDATE" in lock_sql
    assert "ORDER BY id" in lock_sql


def test_deactivation_bumps_the_token_epoch(monkeypatch, fake_conn):
    """Otherwise a deactivated user keeps working until their JWT expires."""
    conn = fake_conn(
        monkeypatch, service,
        fetchall_results=_rows_for([1, 2]),
        fetchone_results=[(2, "b@b.c", "B", "member", False, "t", "t")],
        description=USER_COLS,
    )
    service.update_user(2, is_active=False)
    assert any("token_epoch = token_epoch + 1" in s for s, _ in conn.executed)


def test_update_with_no_fields_is_a_read(monkeypatch):
    monkeypatch.setattr(service, "get_user", lambda uid: {"id": 3, "password_hash": "$2b$x"})
    assert service.update_user(3) == {"id": 3}


# ── Passwords ────────────────────────────────────────────────────────────────

def test_change_own_password_returns_the_new_epoch(monkeypatch, fake_conn):
    """The caller mints a replacement token from THIS value. Re-reading it afterwards
    could pick up a concurrent change's later epoch and mint a token that outlives the
    password it was issued against."""
    stored = service.hash_password("old-pw")
    conn = fake_conn(monkeypatch, service, fetchone_results=[(stored,), (12,)])
    assert service.change_own_password(1, "old-pw", "new-pw") == 12
    assert any("FOR UPDATE" in s for s, _ in conn.executed)
    assert any("RETURNING token_epoch" in s for s, _ in conn.executed)


def test_change_own_password_rejects_a_wrong_current(monkeypatch, fake_conn):
    stored = service.hash_password("old-pw")
    fake_conn(monkeypatch, service, fetchone_results=[(stored,)])
    assert service.change_own_password(1, "not-it", "new-pw") is None


def test_change_own_password_on_a_missing_row(monkeypatch, fake_conn):
    fake_conn(monkeypatch, service, fetchone_results=[None])
    assert service.change_own_password(99, "x", "y") is None


def test_admin_reset_bumps_the_epoch_and_needs_no_current_password(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, service, fetchone_results=[(1,)])
    assert service.set_password_as_admin(1, "fresh-password") is True
    sql = " | ".join(s for s, _ in conn.executed)
    assert "token_epoch = token_epoch + 1" in sql


def test_admin_reset_of_a_missing_user_reports_false(monkeypatch, fake_conn):
    fake_conn(monkeypatch, service, fetchone_results=[None])
    assert service.set_password_as_admin(404, "fresh-password") is False


def test_stored_password_is_a_hash_not_the_plaintext(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, service, fetchone_results=[(1,)])
    service.set_password_as_admin(1, "plaintext-secret")
    params = conn.executed[0][1]
    assert "plaintext-secret" not in params
    assert params[0].startswith("$2b$")


# ── Display helpers ──────────────────────────────────────────────────────────

def test_display_name_prefers_name_then_email():
    assert service.display_name({"name": "Jo", "email": "jo@x.c"}) == "Jo"
    assert service.display_name({"name": "  ", "email": "jo@x.c"}) == "jo@x.c"
    assert service.display_name(None) == "Unassigned"
