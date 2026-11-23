"""Restore both formats into fresh databases; compare every table and attachment bytes."""

import hashlib
import os
import subprocess
import uuid

import psycopg2
import pyrage
import pytest
from psycopg2 import sql
from psycopg2.extensions import make_dsn

from backups import dump, service

pytestmark = pytest.mark.integration
ADMIN_DSN = os.getenv("TEST_ADMIN_DSN", "postgresql://cake:cake_dev@localhost:5432/cake")


@pytest.fixture
def databases(monkeypatch):
    from core import postgres

    names = ["backup_test_" + uuid.uuid4().hex for _ in range(2)]
    admin = psycopg2.connect(ADMIN_DSN)
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            for name in names:
                cur.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name)))
        dsns = [make_dsn(ADMIN_DSN, dbname=name) for name in names]
        monkeypatch.setenv("DATABASE_URL", dsns[0])
        postgres.close_pool()
        postgres.init_pool()
        postgres.run_migrations()
        yield dsns
    finally:
        postgres.close_pool()
        with admin.cursor() as cur:
            for name in names:
                cur.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
        admin.close()


def _counts(dsn):
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        tables = [row[0] for row in cur.fetchall()]
        result = {}
        for table in tables:
            cur.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)))
            result[table] = cur.fetchone()[0]
        return result


@pytest.mark.parametrize("phrase", [None, "restore test passphrase"])
def test_backup_restores_every_table_and_attachment(databases, monkeypatch, tmp_path, phrase):
    from core.postgres import pg_execute, pg_fetchone
    from crm import chatter_service, service as crm

    pg_execute("INSERT INTO users (email, name, password_hash, role) "
               "VALUES ('admin@example.test', 'Test Admin', 'not-a-real-hash', 'admin')")
    contact = crm.create_contact("Sample Contact")
    note = chatter_service.add_note("contact", contact["id"], "Sample attachment")
    data = bytes(range(256)) * 1000
    pg_execute("INSERT INTO crm_chatter_attachments (note_id, filename, mime_type, byte_size, sha256, data) "
               "VALUES (%s, 'sample.bin', 'application/octet-stream', %s, %s, %s)",
               (note["id"], len(data), hashlib.sha256(data).hexdigest(), data))
    service.save_destination(endpoint="https://storage.example.test", bucket="backups", region="us-east-1",
                             prefix="install", access_key_id="test-id", secret_key="test-secret", retention=30)
    service.set_passphrase(phrase)
    assert pg_fetchone("SELECT config_version FROM backup_settings WHERE id = 1")["config_version"] == 2
    path, name = dump.make_backup(service.passphrase(service.get_settings()))
    try:
        assert name.endswith(".dump.age" if phrase else ".dump")
        plain = tmp_path / "restore.dump"
        plain.write_bytes(pyrage.passphrase.decrypt(path.read_bytes(), phrase) if phrase else path.read_bytes())
        expected = _counts(databases[0])
        assert expected["contacts"] == 1 and expected["crm_chatter_attachments"] == 1
        monkeypatch.setenv("DATABASE_URL", databases[1])
        # Same flags as the documented restore, plus fail-fast for test diagnostics.
        subprocess.run(["pg_restore", "--exit-on-error", "--no-owner", "--no-privileges",
                        "-d", databases[1], str(plain)], check=True, capture_output=True)
        assert _counts(databases[1]) == expected
        with psycopg2.connect(databases[1]) as conn, conn.cursor() as cur:
            cur.execute("SELECT data FROM crm_chatter_attachments")
            assert bytes(cur.fetchone()[0]) == data
        # Both CRM reset modes preserve destination, passphrase and history configuration.
        from core.postgres import get_connection
        for definitions in (False, True):
            with get_connection() as conn:
                crm._truncate_all(conn.cursor(), include_definitions=definitions)
            assert service.get_settings()["bucket"] == "backups"
            assert service.passphrase(service.get_settings()) == phrase
    finally:
        dump.cleanup(path)
