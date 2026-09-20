"""First-admin bootstrap (issue #60 Phase A).

The riskiest code in the whole issue: it runs once, against a live install that
already has data, and getting it wrong locks the owner out of their own CRM. These
tests pin the three password sources, the legacy 2FA carry, the ownership backfill,
and the deliberate decision NOT to clear the old credential row.
"""

import pytest

from users import bootstrap, service

ADMIN_EMAIL = "admin@cakecrm.local"

# The fetchone sequence ensure_bootstrap_admin consumes, in order:
#   1. SELECT COUNT(*) FROM users
#   2. SELECT to_regclass('public.auth_credential')
#   3. SELECT password_hash, token_epoch FROM auth_credential   (only if 2 found it)
#   4. INSERT INTO users ... RETURNING id, email, name, role, ...
INSERTED = (1, ADMIN_EMAIL, "Admin", "admin", True, "t", "t")


def _seq(*, users=0, table="auth_credential", credential=(None, 0)):
    seq = [(users,), (table,)]
    if table is not None:
        seq.append(credential)
    seq.append(INSERTED)
    return seq


@pytest.fixture(autouse=True)
def _bootstrap_env(monkeypatch):
    monkeypatch.setattr(bootstrap.settings.auth, "admin_email", ADMIN_EMAIL)
    monkeypatch.setattr(bootstrap.settings.auth, "admin_name", "Admin")
    monkeypatch.setattr(bootstrap.settings.auth, "password", "a-real-password")


def _sql(conn):
    return " | ".join(s for s, _ in conn.executed)


def _insert_params(conn):
    for sql, params in conn.executed:
        if sql.startswith("INSERT INTO users"):
            return params
    raise AssertionError("no user INSERT was issued")


# ── Idempotency ──────────────────────────────────────────────────────────────

def test_does_nothing_when_users_already_exist(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, bootstrap, fetchone_results=[(3,)])
    assert bootstrap.ensure_bootstrap_admin() is None
    assert not any(s.startswith("INSERT INTO users") for s, _ in conn.executed)


def test_takes_an_advisory_lock_before_counting(monkeypatch, fake_conn):
    """Two workers booting the same install must not both seed an admin."""
    conn = fake_conn(monkeypatch, bootstrap, fetchone_results=[(3,)])
    bootstrap.ensure_bootstrap_admin()
    assert "pg_advisory_xact_lock" in conn.executed[0][0]


# ── The three password sources ───────────────────────────────────────────────

def test_carries_an_existing_bcrypt_hash_verbatim(monkeypatch, fake_conn):
    """THE upgrade path. An owner who changed their password in-app does not know
    AUTH_PASSWORD any more — #78 made it inert — so the live credential is the hash
    in auth_credential. Re-deriving from the env var would lock them out."""
    existing = service.hash_password("the-password-they-actually-use")
    conn = fake_conn(monkeypatch, bootstrap, fetchone_results=_seq(credential=(existing, 7)))

    bootstrap.ensure_bootstrap_admin()

    params = _insert_params(conn)
    assert params[2] == existing
    assert service.verify_user_password("the-password-they-actually-use", params[2]) is True
    # The epoch carries too, so tokens minted before the upgrade stay comparable.
    assert params[3] == 7


def test_an_already_bcrypt_auth_password_is_not_hashed_again(monkeypatch, fake_conn):
    """AUTH_PASSWORD has always been documented as accepting a bcrypt hash. Hashing
    the hash would store a credential nobody can present."""
    hashed_env = service.hash_password("env-chosen-password")
    monkeypatch.setattr(bootstrap.settings.auth, "password", hashed_env)
    conn = fake_conn(monkeypatch, bootstrap, fetchone_results=_seq(credential=(None, 0)))

    bootstrap.ensure_bootstrap_admin()

    params = _insert_params(conn)
    assert params[2] == hashed_env
    assert service.verify_user_password("env-chosen-password", params[2]) is True


def test_a_plaintext_auth_password_is_hashed(monkeypatch, fake_conn):
    monkeypatch.setattr(bootstrap.settings.auth, "password", "plain-env-password")
    conn = fake_conn(monkeypatch, bootstrap, fetchone_results=_seq(credential=(None, 0)))

    bootstrap.ensure_bootstrap_admin()

    params = _insert_params(conn)
    assert params[2].startswith("$2b$")
    assert params[2] != "plain-env-password"
    assert service.verify_user_password("plain-env-password", params[2]) is True


def test_a_fresh_install_with_no_credential_table_still_boots(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, bootstrap, fetchone_results=_seq(table=None))
    bootstrap.ensure_bootstrap_admin()
    assert service.verify_user_password("a-real-password", _insert_params(conn)[2]) is True


def test_the_seeded_account_is_an_active_admin(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, bootstrap, fetchone_results=_seq())
    result = bootstrap.ensure_bootstrap_admin()
    assert "'admin', TRUE" in _sql(conn)
    assert result == {"id": 1, "email": ADMIN_EMAIL, "name": "Admin", "role": "admin"}


# ── Legacy 2FA carry ─────────────────────────────────────────────────────────

def test_claims_the_orphaned_2fa_rows(monkeypatch, fake_conn):
    """Until claimed those rows are invisible (every read filters user_id, and NULL
    matches nothing) — which would silently switch an existing user's 2FA off."""
    conn = fake_conn(monkeypatch, bootstrap, fetchone_results=_seq())
    bootstrap.ensure_bootstrap_admin()
    sql = _sql(conn)
    assert "UPDATE totp_config SET user_id = %s WHERE user_id IS NULL" in sql
    assert "UPDATE trusted_devices SET user_id = %s WHERE user_id IS NULL" in sql


def test_claims_the_legacy_conversations(monkeypatch, fake_conn):
    """The SKIPPED-VERSION upgrade (#191): an install coming straight from a
    pre-multi-user release runs the users migration and M1 in one startup, so M1's own
    MIN(id)-admin claim reads an empty users table and matches nothing. Without this
    claim that install's whole chat history stays unowned — and an unowned conversation
    is invisible to every seat, so it would vanish from the sidebar for good."""
    conn = fake_conn(monkeypatch, bootstrap, fetchone_results=_seq())
    bootstrap.ensure_bootstrap_admin()
    assert (
        "UPDATE assistant_conversations SET user_id = %s WHERE user_id IS NULL"
        in _sql(conn)
    )


# ── Ownership backfill ───────────────────────────────────────────────────────

def test_backfills_ownership_on_every_owned_table(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, bootstrap, fetchone_results=_seq())
    bootstrap.ensure_bootstrap_admin()
    sql = _sql(conn)
    for table in ("contacts", "companies", "deals", "tasks"):
        assert f"UPDATE {table} SET owner_id = %s WHERE owner_id IS NULL" in sql


def test_does_not_backfill_actor_or_author(monkeypatch, fake_conn):
    """Phase A's rule is that it may UNDERCOUNT assistant-delegated work but must
    never MISATTRIBUTE it. Historical rows cannot be told apart, so they stay NULL
    and roll up as "Unattributed"."""
    conn = fake_conn(monkeypatch, bootstrap, fetchone_results=_seq())
    bootstrap.ensure_bootstrap_admin()
    sql = _sql(conn)
    assert "actor_id" not in sql
    assert "author_id" not in sql


# ── The old credential row is deliberately left alone ────────────────────────

def test_never_clears_the_legacy_credential(monkeypatch, fake_conn):
    """Clearing it looks tidy and is a downgrade hazard: pre-#60 code reads a NULL
    hash as permission to fall back to AUTH_PASSWORD, so a rolled-back or
    mid-rolling-deploy process would accept the superseded env password."""
    conn = fake_conn(monkeypatch, bootstrap, fetchone_results=_seq())
    bootstrap.ensure_bootstrap_admin()
    assert "UPDATE auth_credential" not in _sql(conn)
    assert "DROP TABLE" not in _sql(conn)


# ── Weak bootstrap password ──────────────────────────────────────────────────

@pytest.mark.parametrize("weak", ["", "changeme"])
def test_a_default_password_raises_an_alert(monkeypatch, fake_conn, weak):
    monkeypatch.setattr(bootstrap.settings.auth, "password", weak)
    fake_conn(monkeypatch, bootstrap, fetchone_results=_seq(credential=(None, 0)))

    raised = []
    from alerts import service as alerts_service

    monkeypatch.setattr(
        alerts_service, "create_alert",
        lambda **kw: raised.append(kw) or {"ok": True},
    )
    bootstrap.ensure_bootstrap_admin()
    assert raised and raised[0]["source_id"] == "weak-admin-password"


def test_a_real_password_raises_no_alert(monkeypatch, fake_conn):
    fake_conn(monkeypatch, bootstrap, fetchone_results=_seq(credential=(None, 0)))
    raised = []
    from alerts import service as alerts_service

    monkeypatch.setattr(alerts_service, "create_alert", lambda **kw: raised.append(kw))
    bootstrap.ensure_bootstrap_admin()
    assert raised == []


def test_a_failing_alert_never_stops_the_app_booting(monkeypatch, fake_conn, caplog):
    monkeypatch.setattr(bootstrap.settings.auth, "password", "changeme")
    fake_conn(monkeypatch, bootstrap, fetchone_results=_seq(credential=(None, 0)))
    from alerts import service as alerts_service

    def _boom(**kw):
        raise RuntimeError("alerts table missing")

    monkeypatch.setattr(alerts_service, "create_alert", _boom)
    with caplog.at_level("ERROR"):
        assert bootstrap.ensure_bootstrap_admin() is not None
    assert "weak-password alert" in caplog.text


def test_logs_the_email_the_owner_must_now_sign_in_with(monkeypatch, fake_conn, caplog):
    """An upgrading owner has only ever typed a password. They need to be told the
    address, and the log is the one place a self-hosted operator will look."""
    existing = service.hash_password("their-password")
    fake_conn(monkeypatch, bootstrap, fetchone_results=_seq(credential=(existing, 1)))
    with caplog.at_level("WARNING"):
        bootstrap.ensure_bootstrap_admin()
    assert ADMIN_EMAIL in caplog.text
    assert "existing password" in caplog.text


# ── The seeded address must be one the login form will submit ────────────────

@pytest.mark.parametrize("bad", ["admin", "@example.com", "admin@", "ad min@x.test", "  "])
def test_an_unusable_admin_email_falls_back_to_the_default(monkeypatch, fake_conn, caplog, bad):
    """Seeding a malformed address creates an account nobody can sign in to —
    LoginPage's type="email" input refuses to submit it — and the seeding never runs
    again, so correcting the env var afterwards does nothing. Recovery would mean
    manual database surgery on a fresh install."""
    monkeypatch.setattr(bootstrap.settings.auth, "admin_email", bad)
    conn = fake_conn(monkeypatch, bootstrap, fetchone_results=_seq(credential=(None, 0)))
    with caplog.at_level("ERROR"):
        bootstrap.ensure_bootstrap_admin()
    assert _insert_params(conn)[0] == bootstrap.DEFAULT_ADMIN_EMAIL


@pytest.mark.parametrize("good", ["owner@team.test", "first.last+tag@sub.example.co.uk"])
def test_a_usable_admin_email_is_kept(monkeypatch, fake_conn, good):
    monkeypatch.setattr(bootstrap.settings.auth, "admin_email", good)
    conn = fake_conn(monkeypatch, bootstrap, fetchone_results=_seq(credential=(None, 0)))
    bootstrap.ensure_bootstrap_admin()
    assert _insert_params(conn)[0] == good
