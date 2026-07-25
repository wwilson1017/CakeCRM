"""Shared fixtures for the provider-layer test suite.

Hermetic by default: the PostgreSQL helpers (core.postgres) and the provider
SDKs are mocked, and encryption runs for real against an ephemeral test key —
so the unit suite needs neither a database nor network. The real-Postgres path
is exercised only by tests/test_integration_pg.py (marker: integration).
"""

import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

# Make the backend dir importable as the package root (providers, core, main).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def pytest_collection_modifyitems(config, items):
    """Enforce the repo's no-suppression rule: fail collection if any test carries
    a skip/skipif/xfail marker. (The `integration` marker is a selection marker,
    not a suppression, and is fine.)"""
    banned = {"skip", "skipif", "xfail"}
    offenders = [
        f"{item.nodeid}: @pytest.mark.{marker.name}"
        for item in items
        for marker in item.iter_markers()
        if marker.name in banned
    ]
    if offenders:
        raise pytest.UsageError(
            "Suppression markers are banned (fix the root cause instead):\n"
            + "\n".join(offenders)
        )


@pytest.fixture(autouse=True)
def encryption_key(monkeypatch):
    """Give every test a real, ephemeral Fernet key via env so encryption round-
    trips for real without ever touching the OS keychain or writing a key file."""
    from core.encryption import EncryptionKeyManager

    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())
    EncryptionKeyManager.reset_cache()
    yield
    EncryptionKeyManager.reset_cache()


@pytest.fixture(autouse=True)
def reset_model_listing_cache():
    """Isolate the module-level model-list cache + single-flight locks per test."""
    from providers import model_listing

    model_listing._cache.clear()
    model_listing._locks.clear()
    yield
    model_listing._cache.clear()
    model_listing._locks.clear()


@pytest.fixture(autouse=True)
def _no_touch_count_daemon(monkeypatch):
    """Keep the hermetic suite from spawning the real touch-count daemon thread (issue #16):
    any test that adds a deal note/activity hits schedule_recompute, which would lazily start
    a background thread. No-op the lazy worker start and give each test a fresh queue/pending/
    worker so module-level state never leaks between tests. Tests that exercise the worker
    directly call _process_one or re-patch _ensure_worker themselves (last patch wins)."""
    import queue as _q

    from crm import touch_count_service as tcs

    monkeypatch.setattr(tcs, "_ensure_worker", lambda: None)
    monkeypatch.setattr(tcs, "_queue", _q.Queue(maxsize=tcs.QUEUE_MAX))
    monkeypatch.setattr(tcs, "_pending", {})
    monkeypatch.setattr(tcs, "_worker", None)
    monkeypatch.setattr(tcs, "_last_backfill_at", None)
    yield


class FakeCursor:
    """Raw-cursor stand-in. execute() records normalized SQL + params; fetchone/
    fetchall pop from queues seeded on the owning FakeConn. Mirrors the REAL
    adapter: raw psycopg2 cursors return TUPLES (positional), which is what
    credentials._load and model_tiers.set_overrides consume."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        self._conn.executed.append((" ".join(sql.split()), params))

    def executemany(self, sql, seq_of_params):
        self._conn.executed.append((" ".join(sql.split()), list(seq_of_params)))

    def fetchone(self):
        return self._conn.fetchone_results.pop(0) if self._conn.fetchone_results else None

    def fetchall(self):
        return self._conn.fetchall_results.pop(0) if self._conn.fetchall_results else []


class FakeConn:
    def __init__(self, fetchone_results=None, fetchall_results=None):
        self.executed = []
        self.fetchone_results = list(fetchone_results or [])
        self.fetchall_results = list(fetchall_results or [])

    def cursor(self):
        return FakeCursor(self)


def _make_get_connection(conn):
    @contextmanager
    def _get_connection():
        yield conn
    return _get_connection


@pytest.fixture
def fake_conn():
    """Install a FakeConn onto a consuming module's ``get_connection`` and return
    it for assertions. Usage: ``conn = fake_conn(monkeypatch, providers.credentials,
    fetchone_results=[...], fetchall_results=[...])``."""

    def _install(monkeypatch, module, *, fetchone_results=None, fetchall_results=None):
        conn = FakeConn(fetchone_results=fetchone_results, fetchall_results=fetchall_results)
        monkeypatch.setattr(module, "get_connection", _make_get_connection(conn))
        return conn

    return _install
