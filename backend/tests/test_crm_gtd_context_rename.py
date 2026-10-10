"""Renaming a todo context across every todo (#280, port of cake_os #3569).

Hermetic: the route runs the REAL service over a scripted cursor, so these pin the
HTTP contract (409 without merge, the merge path, the response shape), that the write
never touches `updated_at`/`status`/`completed`, and that the no-login web mount has no
such route. `test_integration_todo_gtd_pg.py` proves the rewrite on real Postgres.
"""

from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.auth import get_current_user
from crm import gtd_router, gtd_service

URL = "/api/crm/gtd/contexts/rename"


class _Cursor:
    """`source_ids` are the todos carrying the old name; `dest_exists` says whether the
    new name is already a context."""

    def __init__(self, source_ids, dest_exists):
        self._source = source_ids
        self._dest = dest_exists
        self._rows: list = []
        self.executed: list = []

    def execute(self, sql, params=()):
        norm = " ".join(sql.split())
        self.executed.append((norm, params))
        if "FOR UPDATE" in norm:
            self._rows = [(i,) for i in self._source]
        elif "SELECT EXISTS" in norm:
            self._rows = [(self._dest,)]
        else:
            self._rows = []

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def updates(self):
        return [(s, p) for s, p in self.executed if s.startswith("UPDATE")]


@pytest.fixture
def db(monkeypatch):
    def _install(source_ids=(4, 9), dest_exists=False):
        cur = _Cursor(list(source_ids), dest_exists)

        class _Conn:
            def cursor(self):
                return cur

        @contextmanager
        def _get_connection():
            yield _Conn()

        monkeypatch.setattr(gtd_service, "get_connection", _get_connection)
        return cur

    return _install


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(gtd_router.router, prefix="/api/crm/gtd")
    app.dependency_overrides[get_current_user] = lambda: {"id": 1, "role": "member"}
    return TestClient(app)


def test_rename_rewrites_only_the_context_and_reports_what_moved(client, db):
    cur = db(source_ids=(4, 9), dest_exists=False)
    r = client.post(URL, json={"old": "@phone", "new": "@calls"})
    assert r.status_code == 200
    assert r.json() == {"count": 2, "todo_ids": [4, 9], "merged": False}
    [(sql, params)] = cur.updates()
    assert sql == "UPDATE todos SET context = %s WHERE id = ANY(%s)"
    assert params == ("@calls", [4, 9])
    # Review's stale list and Waiting's age read updated_at; the CHECK binds status
    # and completed. A rename touches none of them.
    assert "updated_at" not in sql and "status" not in sql and "completed" not in sql
    # Lock before reading, and the source rows locked in id order.
    stmts = [s for s, _ in cur.executed]
    assert stmts[0] == "SELECT pg_advisory_xact_lock(2801)"
    assert stmts[1] == "SELECT id FROM todos WHERE context = %s ORDER BY id FOR UPDATE"


def test_an_existing_name_is_409_and_nothing_is_written(client, db):
    cur = db(dest_exists=True)
    r = client.post(URL, json={"old": "@phone", "new": "@calls"})
    assert r.status_code == 409
    assert 'already have a context named "@calls"' in r.json()["detail"]
    assert cur.updates() == []


def test_merge_true_folds_into_the_existing_context(client, db):
    cur = db(source_ids=(4,), dest_exists=True)
    r = client.post(URL, json={"old": "@phone", "new": "@calls", "merge": True})
    assert r.status_code == 200
    assert r.json() == {"count": 1, "todo_ids": [4], "merged": True}
    assert len(cur.updates()) == 1


def test_an_unknown_context_is_404(client, db):
    cur = db(source_ids=())
    assert client.post(URL, json={"old": "@nope", "new": "@calls"}).status_code == 404
    assert cur.updates() == []


@pytest.mark.parametrize("body", [
    {"old": "@phone", "new": "  "},
    {"old": "", "new": "@calls"},
    {"old": "@calls", "new": " @calls "},
])
def test_empty_or_unchanged_names_are_400(client, db, body):
    cur = db()
    assert client.post(URL, json=body).status_code == 400
    assert cur.executed == []


def test_the_route_is_authenticated_not_on_the_shared_crud_factory():
    """build_router's routes are ALSO the public no-login web app; this one must not be."""
    shared = {r.path for r in gtd_router.build_router(lambda: None).routes}
    assert "/contexts/rename" not in shared
    authed = {r.path: r for r in gtd_router.router.routes}
    route = authed["/contexts/rename"]
    assert "get_current_user" in {d.call.__name__ for d in route.dependant.dependencies}


def test_the_public_web_mount_has_no_rename_route():
    """The real app: the authed mount has the route, neither no-login mount does."""
    from main import app

    paths = {getattr(r, "path", "") for r in app.routes}
    assert URL in paths
    assert "/api/todo-web/todos" in paths and "/api/todo-web/{token}/todos" in paths
    assert not [p for p in paths if p.startswith("/api/todo-web") and "contexts" in p]
