"""Real-Postgres integration for the Gmail store (issue #8) — proves what mocks
can't: the migration seeds the singleton row, the enc:v1: CHECK rejects plaintext,
the OAuth state is single-use, and tokens round-trip through connect/disconnect.

Marked ``integration`` and excluded from the default no-DB run. Admin DSN via
TEST_ADMIN_DSN (a maintenance DB you can CREATE DATABASE from)."""

from __future__ import annotations

import os

import psycopg2
import pytest

pytestmark = pytest.mark.integration

ADMIN_DSN = os.getenv("TEST_ADMIN_DSN", "postgresql://cake:cake_dev@localhost:5432/cake")


@pytest.fixture(scope="module")
def pg_db():
    from core import postgres

    dbname = f"cakecrm_gmail_it_{os.getpid()}"
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
def _reset_row(pg_db):
    """Reset the singleton row between tests."""
    from core.postgres import pg_execute

    pg_execute(
        """
        UPDATE gmail_connection SET
            client_id='', client_secret_enc='', email='', access_token_enc='',
            refresh_token_enc='', token_expires_at=NULL, scopes='',
            connection_status='disconnected', oauth_state_hash='',
            oauth_state_created_at=NULL, connection_generation=0
        WHERE id=1
        """
    )
    yield


def _raw_row():
    from core.postgres import pg_fetchone

    return pg_fetchone("SELECT * FROM gmail_connection WHERE id = 1")


def _generation() -> int:
    return int(_raw_row()["connection_generation"])


def test_migration_seeds_singleton_row(pg_db):
    row = _raw_row()
    assert row is not None
    assert row["id"] == 1
    assert row["connection_status"] == "disconnected"


def test_app_credentials_encrypted_and_check_rejects_plaintext(pg_db):
    from core.postgres import get_connection
    from gmail import store

    store.save_app_credentials("cid.apps.googleusercontent.com", "top-secret")
    row = _raw_row()
    assert row["client_secret_enc"].startswith("enc:v1:")
    assert row["client_id"] == "cid.apps.googleusercontent.com"

    # The CHECK constraint must reject a plaintext secret.
    with pytest.raises(psycopg2.errors.CheckViolation):
        with get_connection() as conn:
            conn.cursor().execute(
                "UPDATE gmail_connection SET client_secret_enc = 'plaintext' WHERE id = 1"
            )


def test_oauth_state_single_use(pg_db):
    from gmail import store

    store.set_oauth_state_hash("state-abc")
    # Claiming returns the generation observed, not a bare bool — and claiming does
    # not itself advance it.
    assert store.claim_oauth_state("state-abc") == _generation()
    assert store.claim_oauth_state("state-abc") is None  # already consumed


def test_tokens_roundtrip_and_disconnect_keeps_app_creds(pg_db):
    from datetime import datetime, timedelta, timezone

    from gmail import store

    store.save_app_credentials("cid", "secret")
    assert store.save_tokens(
        access_token="at",
        refresh_token="rt",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        scopes="https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.compose",
        email="me@example.com",
        expected_generation=_generation(),
    ) is True
    assert store.is_connected() is True

    store.clear_connection()
    assert store.is_connected() is False
    row = _raw_row()
    assert row["client_id"] == "cid"  # app creds preserved for one-click reconnect
    assert row["refresh_token_enc"] == ""


def test_update_access_token_cas_against_real_pg(pg_db):
    from datetime import datetime, timedelta, timezone

    from core.encryption import decrypt_value
    from core.postgres import pg_fetchone
    from gmail import store

    store.save_app_credentials("cid", "secret")
    expiry = datetime.now(timezone.utc) + timedelta(hours=1)
    store.save_tokens("at", "rt", expiry, "scope", "me@example.com", _generation())
    current_enc = pg_fetchone("SELECT refresh_token_enc FROM gmail_connection WHERE id=1")["refresh_token_enc"]

    # Matching CAS key updates the access token.
    store.update_access_token("at-fresh", expiry, current_enc)
    row = pg_fetchone("SELECT access_token_enc FROM gmail_connection WHERE id=1")
    assert decrypt_value(row["access_token_enc"]) == "at-fresh"

    # A stale CAS key (connection replaced meanwhile) is a no-op — no clobber.
    store.update_access_token("at-stale-writer", expiry, "enc:v1:stale-ciphertext")
    row = pg_fetchone("SELECT access_token_enc FROM gmail_connection WHERE id=1")
    assert decrypt_value(row["access_token_enc"]) == "at-fresh"  # unchanged


def test_oauth_callback_cas_loses_to_a_concurrent_disconnect(pg_db):
    """THE #43 race against real Postgres.

    The admin clicks Connect (state claimed, generation captured), then disconnects
    while Google is still authorizing. The token persist must lose: no resurrection
    of a connection the admin deliberately ended.
    """
    from datetime import datetime, timedelta, timezone

    from gmail import store

    store.save_app_credentials("cid", "secret")
    store.set_oauth_state_hash("state-xyz")
    captured = store.claim_oauth_state("state-xyz")
    assert captured is not None

    # ...the admin disconnects mid-handshake.
    store.clear_connection()
    assert _generation() > captured

    persisted = store.save_tokens(
        access_token="at",
        refresh_token="rt",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        scopes="scope",
        email="me@example.com",
        expected_generation=captured,
    )
    assert persisted is False
    assert store.is_connected() is False
    row = _raw_row()
    assert row["refresh_token_enc"] == ""  # nothing was written
    assert row["email"] == ""


def test_generation_bumps_only_on_identity_changes(pg_db):
    """Refresh and mark_broken must NOT advance the generation — doing so would
    invalidate in-flight reconnects and every pending draft on each hourly refresh."""
    from datetime import datetime, timedelta, timezone

    from core.postgres import pg_fetchone
    from gmail import store

    store.save_app_credentials("cid", "secret")
    after_app = _generation()

    expiry = datetime.now(timezone.utc) + timedelta(hours=1)
    assert store.save_tokens("at", "rt", expiry, "scope", "me@example.com", after_app) is True
    after_connect = _generation()
    assert after_connect == after_app + 1

    current_enc = pg_fetchone("SELECT refresh_token_enc FROM gmail_connection WHERE id=1")["refresh_token_enc"]
    store.update_access_token("at-fresh", expiry, current_enc)
    assert _generation() == after_connect  # same account, fresher token

    store.mark_broken(current_enc)
    assert _generation() == after_connect  # a status change, not an identity change
    assert _raw_row()["connection_status"] == "broken"

    store.set_oauth_state_hash("s")
    assert _generation() == after_connect  # starting a flow changes nothing

    store.clear_connection()
    assert _generation() == after_connect + 1  # disconnect IS an identity change


def test_mark_broken_cas_ignores_a_stale_credential(pg_db):
    """A concurrent call holding an old refresh token must not be able to mark a
    healthy, freshly reconnected account broken."""
    from datetime import datetime, timedelta, timezone

    from gmail import store

    store.save_app_credentials("cid", "secret")
    expiry = datetime.now(timezone.utc) + timedelta(hours=1)
    store.save_tokens("at", "rt", expiry, "scope", "me@example.com", _generation())

    store.mark_broken("enc:v1:some-older-ciphertext")
    assert _raw_row()["connection_status"] == "ok"  # CAS missed, connection intact
    assert store.is_connected() is True


def test_clear_and_replace_return_the_ciphertext_they_cleared(pg_db):
    """The router revokes what these return, so it must be the grant actually ended."""
    from datetime import datetime, timedelta, timezone

    from core.encryption import decrypt_value
    from gmail import store

    store.save_app_credentials("cid", "secret")
    expiry = datetime.now(timezone.utc) + timedelta(hours=1)
    store.save_tokens("at", "rt-one", expiry, "scope", "me@example.com", _generation())

    old = store.clear_connection()
    assert decrypt_value(old) == "rt-one"
    assert store.clear_connection() == ""  # nothing left to revoke

    store.save_tokens("at", "rt-two", expiry, "scope", "me@example.com", _generation())
    replaced = store.save_app_credentials("cid2", "secret2")
    assert decrypt_value(replaced) == "rt-two"
    assert _raw_row()["client_id"] == "cid2"


def test_two_racing_callbacks_only_the_first_persists(pg_db):
    """save_tokens' docstring claims a second racing callback must miss too. Two
    connects that both captured the same starting generation: the first wins, the
    second is refused — the CAS is on the generation, not on 'was it a disconnect'."""
    from datetime import datetime, timedelta, timezone

    from core.encryption import decrypt_value
    from gmail import store

    store.save_app_credentials("cid", "secret")
    both_captured = _generation()
    expiry = datetime.now(timezone.utc) + timedelta(hours=1)

    assert store.save_tokens("at1", "rt-first", expiry, "scope", "one@x.com", both_captured) is True
    assert store.save_tokens("at2", "rt-second", expiry, "scope", "two@x.com", both_captured) is False

    row = _raw_row()
    assert decrypt_value(row["refresh_token_enc"]) == "rt-first"
    assert row["email"] == "one@x.com"
