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
    # #56's verdict-health counters are module-level too — a fresh dict per test keeps
    # one test's ok/fallback/failed tallies out of the next one's assertions.
    monkeypatch.setattr(tcs, "_verdict_stats", {"ok": 0, "fallback": 0, "failed": 0})
    yield


class FakeCursor:
    """Raw-cursor stand-in. execute() records normalized SQL + params; fetchone/
    fetchall pop from queues seeded on the owning FakeConn. Mirrors the REAL
    adapter: raw psycopg2 cursors return TUPLES (positional), which is what
    credentials._load and model_tiers.set_overrides consume."""

    def __init__(self, conn, cursor_id=0):
        self._conn = conn
        # Which cursor ran a statement, so a test can prove two writes shared ONE
        # transaction. Without it, statements from separate `with get_connection()` blocks
        # are indistinguishable — every cursor appends to the same `executed` list — and a
        # test asserting atomicity would pass even if the code split the work in two.
        self.cursor_id = cursor_id

    @property
    def description(self):
        """Column metadata, for flows that convert rows with core.postgres.row_to_dict.

        Set ``conn.description = ["id", "email", ...]`` on the FakeConn. Kept real
        rather than monkeypatching row_to_dict away: that helper's cursor/description
        reuse across statements in a FOR UPDATE flow is itself a live bug class, and
        stubbing it would hide exactly the mistake worth catching.
        """
        cols = self._conn.description
        return None if cols is None else [(c,) for c in cols]

    @property
    def rowcount(self):
        """Rows affected by the LAST statement, for code that branches on it.

        Set ``conn.rowcounts = [0]`` on the FakeConn to queue per-statement answers;
        anything past the queue reports 1 (a write that matched), which is what every
        test written before conditional writes existed assumes. A real cursor reports
        -1 before any statement runs, so the queue is consumed by execute(), not here.
        """
        return self._conn.rowcount

    def execute(self, sql, params=()):
        self._conn.executed.append((" ".join(sql.split()), params))
        self._conn.executed_by.append(self.cursor_id)
        self._conn.rowcount = (
            self._conn.rowcounts.pop(0) if self._conn.rowcounts else 1
        )

    def executemany(self, sql, seq_of_params):
        self._conn.executed.append((" ".join(sql.split()), list(seq_of_params)))
        self._conn.executed_by.append(self.cursor_id)

    def fetchone(self):
        return self._conn.fetchone_results.pop(0) if self._conn.fetchone_results else None

    def fetchall(self):
        return self._conn.fetchall_results.pop(0) if self._conn.fetchall_results else []


class FakeConn:
    def __init__(self, fetchone_results=None, fetchall_results=None, description=None,
                 rowcounts=None):
        self.executed = []
        self.fetchone_results = list(fetchone_results or [])
        self.fetchall_results = list(fetchall_results or [])
        # cursor.rowcount answers, consumed one per execute(); the default of 1 keeps
        # every pre-existing test on the "the write matched a row" path. A conditional
        # UPDATE (see crm.service._write_deal_update, #96) branches on this, so queueing
        # a 0 is how a test drives the no-op branch.
        self.rowcounts = list(rowcounts or [])
        self.rowcount = 1
        # Transaction-shape bookkeeping. `entries` counts `with get_connection()` blocks and
        # `executed_by` records which cursor ran each statement, so a test can assert "these
        # two writes rode ONE transaction" — an atomicity claim that is otherwise unfalsifiable
        # here, since one FakeConn is reused for every block.
        self.entries = 0
        self.cursors = 0
        self.executed_by = []
        # Column names for cursor.description, when the code under test converts
        # rows with row_to_dict. None mirrors a cursor that returned no rows.
        self.description = list(description) if description else None

    def cursor(self):
        self.cursors += 1
        return FakeCursor(self, cursor_id=self.cursors)


def _make_get_connection(conn):
    @contextmanager
    def _get_connection():
        conn.entries += 1
        yield conn
    return _get_connection


@pytest.fixture
def fake_conn():
    """Install a FakeConn onto a consuming module's ``get_connection`` and return
    it for assertions. Usage: ``conn = fake_conn(monkeypatch, providers.credentials,
    fetchone_results=[...], fetchall_results=[...])``."""

    def _install(monkeypatch, module, *, fetchone_results=None, fetchall_results=None,
                 description=None, rowcounts=None):
        conn = FakeConn(
            fetchone_results=fetchone_results,
            fetchall_results=fetchall_results,
            description=description,
            rowcounts=rowcounts,
        )
        monkeypatch.setattr(module, "get_connection", _make_get_connection(conn))
        return conn

    return _install


# ── Test identity (issue #60) ────────────────────────────────────────────────
#
# Since multi-user landed, get_current_user returns a live DB-backed user row and
# `require_admin` reads `role` off it. Router tests override the dependency rather
# than hitting the database, so they need a dict of the same SHAPE — a bare
# {"sub": "u"} silently 403s every admin-gated route.
#
# One definition, imported everywhere, so the shape can only drift in one place.
FAKE_ADMIN: dict = {
    "id": 1,
    "email": "admin@cakecrm.test",
    "name": "Test Admin",
    "role": "admin",
    "is_active": True,
    "sub": "1",
}

FAKE_MEMBER: dict = {
    "id": 2,
    "email": "member@cakecrm.test",
    "name": "Test Member",
    "role": "member",
    "is_active": True,
    "sub": "2",
}


def fake_admin() -> dict:
    """Dependency override returning an admin. Use as `lambda: fake_admin()`-free:
    `app.dependency_overrides[get_current_user] = fake_admin`."""
    return dict(FAKE_ADMIN)


def fake_member() -> dict:
    """Dependency override returning a non-admin member."""
    return dict(FAKE_MEMBER)
