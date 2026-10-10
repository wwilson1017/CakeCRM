"""Postgres connection pool, query helpers, and migration runner.

Provides a ThreadedConnectionPool wrapped with a Semaphore so callers
block (instead of raising PoolError) when all connections are in use.

Postgres is mandatory — main.py fails startup when DATABASE_URL is unset.
"""

import logging
import os
import threading
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

import psycopg2
import psycopg2.pool

from core.localtime import tz

logger = logging.getLogger(__name__)

_pool: psycopg2.pool.ThreadedConnectionPool | None = None
_semaphore: threading.Semaphore | None = None


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def is_configured() -> bool:
    """True if DATABASE_URL is set (pool may or may not be active)."""
    return bool(os.getenv("DATABASE_URL"))


# ---------------------------------------------------------------------------
# Pool lifecycle
# ---------------------------------------------------------------------------

def init_pool(
    minconn: int | None = None,
    maxconn: int | None = None,
) -> None:
    """Initialize the connection pool.  Call once at startup."""
    global _pool, _semaphore

    if not is_configured():
        logger.info("DATABASE_URL not set — skipping Postgres pool init")
        return

    _minconn = minconn or int(os.getenv("PG_POOL_MIN", "2"))
    _maxconn = maxconn or int(os.getenv("PG_POOL_MAX", "10"))

    _pool = psycopg2.pool.ThreadedConnectionPool(
        _minconn, _maxconn, os.getenv("DATABASE_URL"),
    )
    _semaphore = threading.Semaphore(_maxconn)
    logger.info("Postgres pool initialized (min=%d, max=%d)", _minconn, _maxconn)


def close_pool() -> None:
    """Drain and close the connection pool.  Call on shutdown."""
    global _pool, _semaphore
    if _pool is not None:
        _pool.closeall()
        logger.info("Postgres pool closed")
    _pool = None
    _semaphore = None


# ---------------------------------------------------------------------------
# Connection context manager
# ---------------------------------------------------------------------------

@contextmanager
def get_connection():
    """Acquire a pooled connection (blocks if pool exhausted).

    Auto-commits on success, rolls back on exception, always returns
    the connection to the pool.
    """
    if _pool is None:
        raise RuntimeError("Postgres pool not initialized — call init_pool() first")

    _semaphore.acquire()
    conn = None
    try:
        conn = _pool.getconn()
        yield conn
        conn.commit()
    except Exception:
        if conn is not None:
            conn.rollback()
        raise
    finally:
        if conn is not None:
            _pool.putconn(conn)
        _semaphore.release()


# ---------------------------------------------------------------------------
# Row post-processing
# ---------------------------------------------------------------------------

def _postprocess_value(val):
    """Convert datetime/date objects to ISO strings for downstream compat."""
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, date):
        return val.isoformat()
    return val


def row_to_dict(cursor, row) -> dict:
    """Convert a raw psycopg2 row tuple + cursor.description into a dict.

    Public for callers that manage their own cursor inside a transaction
    (e.g. SELECT ... FOR UPDATE flows) and need the same dict/ISO-string
    conversion as pg_fetchall/pg_fetchone.
    """
    cols = [desc[0] for desc in cursor.description]
    return {col: _postprocess_value(val) for col, val in zip(cols, row)}


_row_to_dict = row_to_dict


# ---------------------------------------------------------------------------
# Query helpers (dict-returning)
# ---------------------------------------------------------------------------

def pg_fetchall(sql: str, params: tuple | list = ()) -> list[dict]:
    """Execute a query and return all rows as dicts."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
        if not rows:
            return []
        return [_row_to_dict(cur, r) for r in rows]


def pg_fetchone(sql: str, params: tuple | list = ()) -> dict | None:
    """Execute a query and return the first row as a dict, or None."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        row = cur.fetchone()
        if row is None:
            return None
        return _row_to_dict(cur, row)


def pg_execute(sql: str, params: tuple | list | dict = ()) -> int:
    """Execute a statement and return rowcount.

    ``params`` may be a sequence (for ``%s`` placeholders) or a dict (for
    ``%(name)s`` named placeholders) — psycopg2 supports both.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.rowcount


# ---------------------------------------------------------------------------
# Migration runner
# ---------------------------------------------------------------------------

# Advisory lock registry (keep in sync when adding locks):
#   pg_advisory_lock(1)               — migration runner (below)
#   pg_try_advisory_lock(720770)      — telegram poller leader election (telegram/poller.py)
#   pg_try_advisory_xact_lock(20260705) — dreaming cycle (dreaming/processor.run_dreaming_if_due)
#   pg_advisory_xact_lock(1801, id)   — per-deal lead-score recompute (crm/scoring_service.py)
#   pg_advisory_xact_lock(1802, id)   — per-contact lead-score recompute (crm/scoring_service.py)
#   pg_advisory_xact_lock(1901)       — first-admin bootstrap (users/bootstrap.py)
#   pg_advisory_xact_lock(2801)       — todo context rename (crm/gtd_service.rename_context)


def run_migrations() -> None:
    """Apply SQL migration files from backend/migrations/ in filename order.

    Migrations are named YYYYMMDDHHMMSS_<name>.sql so lexicographic order is
    chronological order. Uses pg_advisory_lock(1) to prevent concurrent DDL
    when multiple instances start simultaneously.
    """
    if _pool is None:
        return

    migrations_dir = Path(__file__).resolve().parent.parent / "migrations"
    if not migrations_dir.exists():
        logger.warning("No migrations directory at %s", migrations_dir)
        return

    with get_connection() as conn:
        cur = conn.cursor()

        # Bootstrap the tracking table outside the lock (idempotent)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS _migrations_applied (
                filename TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        conn.commit()

        # Session-level advisory lock — holds across commits within this
        # connection, released when connection returns to pool.
        cur.execute("SELECT pg_advisory_lock(1)")

        try:
            cur.execute("SELECT filename FROM _migrations_applied")
            applied = {row[0] for row in cur.fetchall()}

            sql_files = sorted(migrations_dir.glob("*.sql"))
            # The install zone, for a migration that turns an instant into a calendar day
            # (#279's closed_on backfill). A .sql file cannot read TIMEZONE, so zoneinfo
            # picks the zone and each file's transaction gets it as the namespaced setting
            # `cakecrm.timezone` — transaction-local and read by nothing else, so no other
            # migration's semantics change and nothing leaks to the pooled session.
            zone = tz().key
            for sql_file in sql_files:
                if sql_file.name in applied:
                    continue

                logger.info("Applying migration: %s", sql_file.name)
                sql = sql_file.read_text(encoding="utf-8")
                cur.execute("SELECT set_config('cakecrm.timezone', %s, true)", (zone,))
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO _migrations_applied (filename) VALUES (%s)",
                    (sql_file.name,),
                )
                conn.commit()
                logger.info("Migration applied: %s", sql_file.name)

        except Exception:
            conn.rollback()
            raise
        finally:
            try:
                cur.execute("SELECT pg_advisory_unlock(1)")
                conn.commit()
            except Exception as unlock_err:
                logger.error("Failed to release advisory lock: %s", unlock_err)

    logger.info("All migrations up to date")


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

def health_check() -> str:
    try:
        with get_connection() as conn:
            conn.cursor().execute("SELECT 1")
        return "ok"
    except Exception as e:
        logger.error("Postgres health check failed: %s", e)
        return "error"
