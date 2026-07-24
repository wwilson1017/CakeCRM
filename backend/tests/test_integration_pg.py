"""Real-Postgres integration — proves what mocks can't: the migration applies,
the enc:v1: CHECK rejects plaintext, credentials survive an encrypt -> store ->
new-process-reload -> decrypt round-trip (with no raw key in the DB or the API
summary), and JSONB tier config round-trips.

Marked ``integration`` and excluded from the default (no-DB) test run — the CI
lane that runs these provisions a throwaway PostgreSQL. It never uses skip/xfail:
where PostgreSQL is provided, it runs; where it isn't, that lane isn't invoked.

Admin DSN (a maintenance DB you can CREATE DATABASE from) via TEST_ADMIN_DSN;
defaults to the local dev container.
"""

import json
import os

import psycopg2
import pytest

pytestmark = pytest.mark.integration

ADMIN_DSN = os.getenv("TEST_ADMIN_DSN", "postgresql://cake:cake_dev@localhost:5432/cake")


@pytest.fixture(scope="module")
def pg_db():
    from core import postgres

    dbname = f"cakecrm_it_{os.getpid()}"
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
    postgres.run_migrations()  # applies EVERY migration, incl. ai_providers
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
def _clean_tables(pg_db):
    from core.postgres import pg_execute
    pg_execute("TRUNCATE ai_providers, ai_model_tiers")
    pg_execute("UPDATE ai_settings SET active_provider = '', active_model = '' WHERE id = 1")
    yield


def test_migration_created_the_tables(pg_db):
    from core.postgres import pg_fetchall
    names = {
        r["table_name"]
        for r in pg_fetchall(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
    }
    assert {"ai_providers", "ai_settings", "ai_model_tiers"} <= names


def test_enc_check_rejects_plaintext(pg_db):
    from core.postgres import get_connection
    with pytest.raises(psycopg2.errors.CheckViolation):
        with get_connection() as conn:
            conn.cursor().execute(
                "INSERT INTO ai_providers (provider, auth_type, api_key_enc) "
                "VALUES ('openai', 'api_key', 'plaintext-not-allowed')"
            )


def test_credential_roundtrip_through_postgres(pg_db):
    from core.postgres import pg_fetchone
    from providers.credentials import CredentialStore

    CredentialStore().set_api_key("anthropic", "test-real-secret-42")

    # A fresh store reloads purely from the database.
    reloaded = CredentialStore()
    assert reloaded.get_api_key("anthropic") == "test-real-secret-42"

    summary = reloaded.to_dict()
    assert summary["profiles"]["anthropic"]["configured"] is True
    assert "test-real-secret-42" not in json.dumps(summary)

    row = pg_fetchone("SELECT api_key_enc FROM ai_providers WHERE provider = 'anthropic'")
    assert row["api_key_enc"].startswith("enc:v1:")  # stored ciphertext, never plaintext


def test_unicode_secret_roundtrip(pg_db):
    from providers.credentials import CredentialStore
    secret = "test-üñïçödé-🔐-key"
    CredentialStore().set_api_key("together", secret)
    assert CredentialStore().get_api_key("together") == secret


def test_remove_provider_clears_active(pg_db):
    from providers.credentials import CredentialStore
    s = CredentialStore()
    s.set_api_key("openai", "test-openai-key")
    assert s.data["active_provider"] == "openai"
    s.remove_provider("openai")
    reloaded = CredentialStore()
    assert reloaded.data["active_provider"] == ""
    assert reloaded.get_api_key("openai") is None


def test_tier_jsonb_roundtrip(pg_db):
    from providers import model_tiers
    model_tiers.set_inferred("anthropic", {"top": "claude-opus-4-8", "mid": "claude-sonnet-4-6"})
    model_tiers.set_overrides("anthropic", {"light": "claude-haiku-4-5"})
    resolved = model_tiers.get_resolved("anthropic")
    assert resolved["top"] == "claude-opus-4-8"      # inferred
    assert resolved["mid"] == "claude-sonnet-4-6"    # inferred
    assert resolved["light"] == "claude-haiku-4-5"   # user override
