"""Real-Postgres integration for pipeline stage criteria (#289).

What the route tests cannot check: that the migration applies, that the upsert really
replaces (one row per stage), that JSONB round-trips the checklist in order, that a reset
deletes the row, and that BOTH `_truncate_all` variants name the new table. Fictional data.
"""

import os

import psycopg2
import pytest

pytestmark = pytest.mark.integration

ADMIN_DSN = os.getenv("TEST_ADMIN_DSN", "postgresql://cake:cake_dev@localhost:5432/cake")


@pytest.fixture(scope="module")
def pg_db():
    from core import postgres

    dbname = f"cakecrm_stage_criteria_{os.getpid()}"
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
def _clean(pg_db):
    from core.postgres import pg_execute

    pg_execute("DELETE FROM crm_stage_criteria")
    yield


def _rows() -> int:
    from core.postgres import pg_fetchone

    return pg_fetchone("SELECT COUNT(*) AS n FROM crm_stage_criteria")["n"]


def test_override_upserts_and_reset_restores_the_standard(pg_db):
    from crm import stage_criteria

    stage_criteria.set_criteria("proposal", "First take", ["A", "B"])
    out = stage_criteria.set_criteria("proposal", "Second take", ["C", "B", "A"])
    assert out == {"stage": "proposal", "summary": "Second take", "checklist": ["C", "B", "A"],
                   "source": "custom"}
    assert _rows() == 1

    merged = {e["stage"]: e for e in stage_criteria.list_criteria()}
    assert merged["proposal"]["checklist"] == ["C", "B", "A"]
    assert merged["proposal"]["source"] == "custom"
    assert merged["lead"]["source"] == "standard"

    out = stage_criteria.reset_criteria("proposal")
    assert out["source"] == "standard"
    assert out["summary"] == stage_criteria.STANDARD["proposal"]["summary"]
    assert _rows() == 0


@pytest.mark.parametrize("include_definitions", [False, True])
def test_both_truncate_variants_clear_the_overrides(pg_db, include_definitions):
    from core.postgres import get_connection
    from crm import service, stage_criteria

    stage_criteria.set_criteria("won", "Ours", ["One"])
    with get_connection() as conn:
        service._truncate_all(conn.cursor(), include_definitions=include_definitions)
    assert _rows() == 0
