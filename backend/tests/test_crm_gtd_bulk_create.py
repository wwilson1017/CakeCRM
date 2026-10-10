"""`todo_bulk_create` (#284): one transaction, all or nothing, the bad item named.

Driven through a fake connection with the REAL `get_connection` contract — commit only
when the block exits cleanly, rollback on any exception — so "nothing was created" is
asserted as "the transaction was rolled back", not inferred from a return value.
"""

from contextlib import contextmanager

import pytest

from crm import gtd_service, gtd_tools
from crm.gtd_common import MAX_BULK_IDS, ValidationError


class _Cursor:
    def __init__(self, conn):
        self.conn = conn
        self._last = ""

    def execute(self, sql, params=()):
        self._last = " ".join(sql.split())
        self.conn.executed.append((self._last, params))

    def fetchone(self):
        if "INSERT INTO todos" in self._last:
            self.conn.next_id += 1
            return (self.conn.next_id,)
        if "FROM todo_projects" in self._last:
            return (7,)
        return None


class _Conn:
    def __init__(self):
        self.executed: list = []
        self.next_id = 100
        self.outcome = None

    def cursor(self):
        return _Cursor(self)

    def inserts(self):
        return [p for s, p in self.executed if s.startswith("INSERT INTO todos")]


@pytest.fixture
def conn(monkeypatch):
    c = _Conn()

    @contextmanager
    def _get_connection():
        try:
            yield c
            c.outcome = "commit"
        except Exception:
            c.outcome = "rollback"
            raise

    monkeypatch.setattr(gtd_service, "get_connection", _get_connection)
    return c


def test_creates_every_item_in_one_committed_transaction(conn):
    out = gtd_service.bulk_create(
        [{"title": "Call the vet", "project": "Pets", "context": "@calls"},
         {"title": "Buy seed", "status": "next_action", "notes": None}],
        owner_id=3,
    )
    assert out == {"created": 2, "ids": [101, 102]}
    assert conn.outcome == "commit"
    first, second = conn.inserts()
    assert first[0] == "Call the vet" and first[9] == "@calls" and first[13] == 7
    assert second[0] == "Buy seed" and second[7] == "next_action"
    # Server-stamped on every row; a null field means the default.
    assert {first[6], second[6]} == {3} and {first[14], second[14]} == {"agent"}
    assert first[7] == "inbox"


def test_a_done_item_is_inserted_completed(conn):
    gtd_service.bulk_create([{"title": "Old thing", "status": "done", "repeat": "weekly"}])
    sql = next(s for s, _ in conn.executed if s.startswith("INSERT INTO todos"))
    # completed/completed_at are derived from status in the ONE insert, and an insert is
    # not a done-transition, so nothing spawns a next occurrence.
    assert "CASE WHEN %s = 'done' THEN 1 ELSE 0 END" in sql
    assert len(conn.inserts()) == 1


@pytest.mark.parametrize("bad, needle", [
    ({"title": ""}, "title is required"),
    ({"title": "x", "status": "someday"}, "status"),
    ({"title": "x", "owner_id": 9}, "Unknown fields: owner_id"),
    ({"title": "x", "source": "capture_web"}, "Unknown fields: source"),
    ({"title": "x", "bring_back_on": "2026-11-01"}, "Unknown fields: bring_back_on"),
    ("just a string", "must be an object"),
])
def test_one_bad_item_rolls_back_the_batch_and_is_named(conn, bad, needle):
    with pytest.raises(ValidationError) as e:
        gtd_service.bulk_create([{"title": "a"}, {"title": "b"}, bad, {"title": "d"}])
    msg = str(e.value)
    assert msg.startswith("todos[2]: ") and needle in msg
    assert "nothing in this batch was created" in msg
    assert conn.outcome == "rollback"


def test_the_cap_is_the_shared_bulk_ceiling(conn):
    with pytest.raises(ValidationError, match=f"max {MAX_BULK_IDS}"):
        gtd_service.bulk_create([{"title": "x"}] * (MAX_BULK_IDS + 1))
    assert conn.executed == []
    with pytest.raises(ValidationError, match="todos is required"):
        gtd_service.bulk_create([])


def test_single_create_shares_the_same_insert(conn, monkeypatch):
    monkeypatch.setattr(gtd_service, "get_todo", lambda i: {"id": i})
    assert gtd_service.create_todo("One", project="Pets") == {"id": 101}
    assert conn.outcome == "commit" and len(conn.inserts()) == 1


def test_the_tool_binds_owner_and_source_and_drops_the_models(todo_mode, monkeypatch):
    todo_mode("gtd")
    calls = []

    def _fake(todos, **kw):
        calls.append(kw)
        return {"created": len(todos), "ids": [1]}

    monkeypatch.setattr(gtd_tools.gtd_service, "bulk_create", _fake)
    monkeypatch.setattr(gtd_tools, "_with_targets", lambda r, *_: r)
    _, executors = gtd_tools.get_gtd_tools({"id": 5})
    out = executors["todo_bulk_create"](todos=[{"title": "a"}], owner_id=99, source="capture_web")
    assert out["created"] == 1
    assert calls == [{"owner_id": 5, "source": "agent"}]


def test_a_refused_batch_is_an_error_dict_not_a_raise(conn):
    out = gtd_tools.GTD_TOOL_EXECUTORS["todo_bulk_create"](todos=[{"title": ""}])
    assert out["error"].startswith("todos[0]: title is required")
