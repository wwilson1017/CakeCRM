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
            oauth_state_created_at=NULL
        WHERE id=1
        """
    )
    yield


def _raw_row():
    from core.postgres import pg_fetchone

    return pg_fetchone("SELECT * FROM gmail_connection WHERE id = 1")


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
    assert store.claim_oauth_state("state-abc") is True
    assert store.claim_oauth_state("state-abc") is False  # already consumed


def test_tokens_roundtrip_and_disconnect_keeps_app_creds(pg_db):
    from datetime import datetime, timedelta, timezone

    from gmail import store

    store.save_app_credentials("cid", "secret")
    store.save_tokens(
        access_token="at",
        refresh_token="rt",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        scopes="https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.compose",
        email="me@example.com",
    )
    assert store.is_connected() is True

    store.clear_connection()
    assert store.is_connected() is False
    row = _raw_row()
    assert row["client_id"] == "cid"  # app creds preserved for one-click reconnect
    assert row["refresh_token_enc"] == ""
