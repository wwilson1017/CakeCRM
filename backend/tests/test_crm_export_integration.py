"""Real-Postgres integration for the all-data CSV export (#302): every exported CSV
parses, row counts match the tables, the live schema is fully placed, and the stream
really pages through a server-side cursor.

Marked ``integration`` and excluded from the default (no-DB) run.
"""

import csv
import io
import os
import zipfile

import psycopg2
import pytest

pytestmark = pytest.mark.integration

ADMIN_DSN = os.getenv("TEST_ADMIN_DSN", "postgresql://cake:cake_dev@localhost:5432/cake")


@pytest.fixture(scope="module")
def pg_db():
    from core import postgres

    dbname = f"cakecrm_export_{os.getpid()}"
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


def _export() -> zipfile.ZipFile:
    from crm import export

    return zipfile.ZipFile(io.BytesIO(b"".join(export.stream_zip())))


def _rows(z: zipfile.ZipFile, name: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(z.read(name).decode("utf-8"))))


def test_export_zips_one_parseable_csv_per_table_with_every_row(pg_db, monkeypatch):
    from core.postgres import pg_execute, pg_fetchall, pg_fetchone
    from crm import export, service

    pg_execute(
        "INSERT INTO users (email, name, password_hash, role) "
        "VALUES ('admin@example.com', 'Ada Admin', 'not-a-real-hash', 'admin')"
    )
    assert service.load_sample_data()["ok"] is True
    service.create_contact('=HYPERLINK("x")', phone="+1 615 555 0100")
    # Page smaller than the contacts table, so the stream really crosses page boundaries.
    contacts_n = pg_fetchone("SELECT COUNT(*) AS n FROM contacts")["n"]
    assert contacts_n > 3
    monkeypatch.setattr(export, "PAGE_ROWS", 3)

    z = _export()
    assert z.testzip() is None
    assert z.namelist() == [f"{t}.csv" for t, _, _ in export.TABLES]
    for table, columns, key in export.TABLES:
        rows = _rows(z, f"{table}.csv")
        live = pg_fetchone(f"SELECT COUNT(*) AS n FROM {table}")["n"]
        assert len(rows) - 1 == live, table
        header = rows[0]
        if columns == "*":
            cols = [r["column_name"] for r in pg_fetchall(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position",
                (table,))]
            assert header == cols, table
        if key == "id" and len(rows) > 2:
            ids = [int(r[header.index("id")]) for r in rows[1:]]
            assert ids == sorted(ids), table

    assert _rows(z, "deals.csv")[1:], "the sample data reached the deals file"
    users = _rows(z, "users.csv")
    assert users[0] == ["id", "email", "name", "role", "is_active", "created_at"]
    assert "not-a-real-hash" not in z.read("users.csv").decode()

    contacts = _rows(z, "contacts.csv")
    name_i, phone_i = contacts[0].index("name"), contacts[0].index("phone")
    hit = [r for r in contacts[1:] if r[name_i] == "'=HYPERLINK(\"x\")"]
    assert hit and hit[0][phone_i] == "'+1 615 555 0100"

    # The live schema, not just the migrations' text: every base table is placed.
    live = {r["table_name"] for r in pg_fetchall(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'")}
    live -= {"_migrations_applied"}  # core.postgres's ledger, not a migration's table
    assert live - {t for t, _, _ in export.TABLES} - set(export.NOT_EXPORTED) == set()


def test_an_abandoned_download_returns_its_connection_clean(pg_db):
    from core.postgres import pg_fetchone
    from crm import export

    stream = export.stream_zip()
    next(stream)
    stream.close()  # what Starlette does when the client goes away
    # The pool got the connection back with its read-only snapshot rolled back.
    stuck = pg_fetchone(
        "SELECT COUNT(*) AS n FROM pg_stat_activity "
        "WHERE datname = current_database() AND state LIKE 'idle in transaction%%'")
    assert stuck["n"] == 0
