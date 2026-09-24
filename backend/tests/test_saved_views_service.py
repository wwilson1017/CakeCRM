"""Saved views service — validation, the creator-or-admin rule, and transaction shape (#181).

Hermetic: `get_connection` is replaced by a fake context manager whose cursor records every
statement, so the tests assert on the SQL that would run without a database. The load-bearing
assertion throughout is not just "returns forbidden" but "and issued no write" — an
authorization check that refuses after writing is not a check.
"""

import json

import psycopg2
import pytest
from conftest import FAKE_ADMIN, FAKE_MEMBER
from psycopg2.extras import Json

from saved_views import service

CREATOR = {"id": 7, "email": "c@x.test", "name": "Creator", "role": "member", "sub": "7"}


class FakeCursor:
    """Records executed SQL; answers fetchone/fetchall from a scripted queue."""

    def __init__(self, answers, raise_unique_on=None):
        self.executed: list[tuple[str, tuple]] = []
        self._answers = list(answers)
        self._raise_unique_on = raise_unique_on
        self.description = [("id",), ("surface",), ("name",), ("version",), ("payload",),
                            ("created_by",), ("created_at",), ("updated_at",),
                            ("created_by_name",)]

    def execute(self, sql, params=()):
        self.executed.append((sql, tuple(params)))
        if self._raise_unique_on and self._raise_unique_on in sql:
            raise psycopg2.errors.UniqueViolation("duplicate key")

    def fetchone(self):
        return self._answers.pop(0) if self._answers else None

    def fetchall(self):
        return self._answers.pop(0) if self._answers else []

    @property
    def sql(self) -> str:
        return "\n".join(s for s, _ in self.executed)


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


@pytest.fixture
def conn(monkeypatch):
    """Install a fake connection; the test scripts its answers via `install(...)`."""
    holder: dict = {}

    def install(answers, raise_unique_on=None) -> FakeCursor:
        cur = FakeCursor(answers, raise_unique_on)
        holder["cursor"] = cur

        class _Ctx:
            def __enter__(self):
                return FakeConn(cur)

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr(service, "get_connection", lambda: _Ctx())
        return cur

    return install


def _row(created_by=7, name="Q3 pipeline"):
    """A tuple matching FakeCursor.description."""
    return (1, "crm_pipeline", name, 1, {"query": ""}, created_by,
            "2026-09-12T00:00:00+00:00", "2026-09-12T00:00:00+00:00", "Creator")


# ── validation ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["", "   ", "\t\n", "x" * 81])
def test_create_rejects_bad_name_without_touching_the_db(conn, name):
    cur = conn([])
    result = service.create_view("crm_pipeline", name, 1, {}, FAKE_ADMIN)
    assert result["code"] == "bad_request"
    assert cur.executed == []


@pytest.mark.parametrize("surface", ["", "   ", "Crm Pipeline", "crm-pipeline", "x" * 65])
def test_create_rejects_bad_surface(conn, surface):
    cur = conn([])
    result = service.create_view(surface, "n", 1, {}, FAKE_ADMIN)
    assert result["code"] == "bad_request"
    assert cur.executed == []


@pytest.mark.parametrize("payload", [None, "x", 5, [1, 2]])
def test_create_rejects_non_object_payload(conn, payload):
    cur = conn([])
    assert service.create_view("crm_pipeline", "n", 1, payload, FAKE_ADMIN)["code"] == "bad_request"
    assert cur.executed == []


def test_create_rejects_oversize_payload_measured_in_utf8_bytes(conn):
    cur = conn([])
    # 2 UTF-8 bytes per character, so this is comfortably over the ceiling.
    payload = {"q": "é" * service.MAX_PAYLOAD_BYTES}
    assert service.create_view("crm_pipeline", "n", 1, payload, FAKE_ADMIN)["code"] == "bad_request"
    assert cur.executed == []


def test_payload_size_is_measured_unescaped(conn):
    """An accented payload is measured as stored UTF-8, not as \\uXXXX escapes.

    json.dumps escapes non-ASCII by default, which would inflate every accented character
    sixfold and reject a payload at a third of the real ceiling.
    """
    text = "é" * 20_000                       # 40,000 UTF-8 bytes — storable
    assert len(json.dumps({"q": text})) > service.MAX_PAYLOAD_BYTES   # escaped: 120,000+
    cur = conn([(0,), (1,), _row()])
    assert service.create_view("crm_pipeline", "n", 1, {"q": text}, FAKE_ADMIN)["id"] == 1
    assert "INSERT INTO saved_views" in cur.sql


def test_create_accepts_a_payload_just_under_the_ceiling(conn):
    cur = conn([(0,), (1,), _row()])
    payload = {"q": "a" * (service.MAX_PAYLOAD_BYTES - 100)}
    assert service.create_view("crm_pipeline", "n", 1, payload, FAKE_ADMIN)["id"] == 1
    assert "INSERT INTO saved_views" in cur.sql


@pytest.mark.parametrize("version", [True, False, -1, "1", 1.0, None])
def test_create_rejects_non_integer_or_negative_version(conn, version):
    # bool is a subclass of int: True must NOT persist as version 1.
    cur = conn([])
    assert service.create_view("crm_pipeline", "n", version, {}, FAKE_ADMIN)["code"] == "bad_request"
    assert cur.executed == []


# ── create ─────────────────────────────────────────────────────────────────

def test_create_normalizes_the_name_and_stores_json_and_the_actor(conn):
    cur = conn([(0,), (1,), _row()])
    service.create_view(" crm_pipeline ", "  Q3 pipeline \n", 1, {"query": "acme"}, CREATOR)
    insert_sql, params = next(c for c in cur.executed if c[0].strip().startswith("INSERT"))
    assert params[0] == "crm_pipeline"
    assert params[1] == "Q3 pipeline"
    assert params[2] == 1
    assert isinstance(params[3], Json)
    assert params[4] == 7


def test_create_reads_the_row_back_in_the_same_transaction(conn):
    # One connection, three statements in order: the ceiling COUNT, the INSERT, and the
    # decorated re-read. A post-commit re-read through a second connection could observe
    # someone else's concurrent write instead of this caller's own row.
    cur = conn([(0,), (1,), _row()])
    service.create_view("crm_pipeline", "n", 1, {}, CREATOR)
    assert len(cur.executed) == 3
    assert "SELECT COUNT(*) FROM saved_views" in cur.executed[0][0]
    assert "INSERT INTO saved_views" in cur.executed[1][0]
    assert "FROM saved_views v" in cur.executed[2][0]


def test_create_refuses_once_the_surface_is_full(conn):
    # Nothing else ever deletes a saved view, so an unbounded table has no reclaim path
    # short of manual SQL. The COUNT rides the insert transaction.
    cur = conn([(service.MAX_VIEWS_PER_SURFACE,)])
    result = service.create_view("crm_pipeline", "one more", 1, {}, CREATOR)
    assert result["code"] == "conflict"
    assert str(service.MAX_VIEWS_PER_SURFACE) in result["error"]
    assert "INSERT INTO saved_views" not in cur.sql


def test_create_allows_the_last_slot_on_a_surface(conn):
    cur = conn([(service.MAX_VIEWS_PER_SURFACE - 1,), (1,), _row()])
    assert service.create_view("crm_pipeline", "one more", 1, {}, CREATOR)["id"] == 1
    assert "INSERT INTO saved_views" in cur.sql


def test_the_ceiling_counts_only_the_target_surface(conn):
    cur = conn([(0,), (1,), _row()])
    service.create_view("crm_todos", "n", 1, {}, CREATOR)
    count_sql, params = cur.executed[0]
    assert "SELECT COUNT(*) FROM saved_views WHERE surface = %s" in count_sql
    assert params == ("crm_todos",)


def test_create_maps_a_duplicate_name_to_conflict(conn):
    conn([(0,), (1,), _row()], raise_unique_on="INSERT INTO saved_views")
    result = service.create_view("crm_pipeline", "Q3 pipeline", 1, {}, CREATOR)
    assert result["code"] == "conflict"
    assert "Q3 pipeline" in result["error"]


# ── list ───────────────────────────────────────────────────────────────────

def test_list_rejects_a_bad_surface(conn):
    cur = conn([])
    assert service.list_views("Not A Key", FAKE_ADMIN)["code"] == "bad_request"
    assert cur.executed == []


def test_list_orders_by_lowercased_name_then_id(conn):
    cur = conn([[_row()]])
    service.list_views("crm_pipeline", FAKE_ADMIN)
    assert "ORDER BY LOWER(v.name), v.id" in cur.sql


def test_list_decorates_can_edit_per_caller(conn):
    rows = [_row(created_by=7), _row(created_by=2), _row(created_by=None)]
    conn([rows])
    as_creator = service.list_views("crm_pipeline", CREATOR)
    assert [v["can_edit"] for v in as_creator] == [True, False, False]

    conn([list(rows)])
    as_member = service.list_views("crm_pipeline", FAKE_MEMBER)
    assert [v["can_edit"] for v in as_member] == [False, True, False]

    conn([list(rows)])
    as_admin = service.list_views("crm_pipeline", FAKE_ADMIN)
    assert [v["can_edit"] for v in as_admin] == [True, True, True]


# ── the creator-or-admin rule ──────────────────────────────────────────────

def test_may_edit_truth_table():
    assert service._may_edit(None, FAKE_MEMBER) is False
    assert service._may_edit(None, FAKE_ADMIN) is True
    assert service._may_edit(2, FAKE_MEMBER) is True
    assert service._may_edit(7, FAKE_MEMBER) is False
    assert service._may_edit(7, CREATOR) is True
    assert service._may_edit(7, FAKE_ADMIN) is True


def test_update_refuses_a_non_creator_member_and_writes_nothing(conn):
    cur = conn([(7,)])
    result = service.update_view(1, FAKE_MEMBER, name="hijacked")
    assert result["code"] == "forbidden"
    assert "FOR UPDATE" in cur.sql
    assert "UPDATE saved_views SET" not in cur.sql


def test_update_allows_the_creator(conn):
    cur = conn([(7,), _row(name="renamed")])
    assert service.update_view(1, CREATOR, name="renamed")["name"] == "renamed"
    assert "UPDATE saved_views SET name = %s" in cur.sql


def test_update_allows_an_admin_over_someone_elses_view(conn):
    cur = conn([(7,), _row()])
    assert service.update_view(1, FAKE_ADMIN, name="renamed")["can_edit"] is True
    assert "UPDATE saved_views SET" in cur.sql


def test_update_missing_row_is_not_found_and_writes_nothing(conn):
    cur = conn([None])
    assert service.update_view(1, FAKE_ADMIN, name="x")["code"] == "not_found"
    assert "UPDATE saved_views SET" not in cur.sql


def test_update_takes_the_row_lock_before_deciding(conn):
    cur = conn([(7,), _row()])
    service.update_view(1, CREATOR, name="renamed")
    assert "SELECT created_by FROM saved_views WHERE id = %s FOR UPDATE" in cur.executed[0][0]


def test_delete_refuses_a_non_creator_member_and_writes_nothing(conn):
    cur = conn([(7,)])
    assert service.delete_view(1, FAKE_MEMBER)["code"] == "forbidden"
    assert "DELETE FROM saved_views" not in cur.sql


def test_delete_allows_the_creator_and_an_admin(conn):
    cur = conn([(7,)])
    assert service.delete_view(1, CREATOR) == {"ok": True, "id": 1}
    assert "DELETE FROM saved_views WHERE id = %s" in cur.sql

    cur = conn([(7,)])
    assert service.delete_view(1, FAKE_ADMIN)["ok"] is True
    assert "DELETE FROM saved_views WHERE id = %s" in cur.sql


def test_delete_missing_row_is_not_found(conn):
    cur = conn([None])
    assert service.delete_view(1, FAKE_ADMIN)["code"] == "not_found"
    assert "DELETE FROM saved_views" not in cur.sql


# ── update field rules ─────────────────────────────────────────────────────

def test_update_refuses_a_payload_without_its_version(conn):
    cur = conn([])
    assert service.update_view(1, CREATOR, payload={})["code"] == "bad_request"
    assert cur.executed == []


def test_update_refuses_a_version_without_a_payload(conn):
    cur = conn([])
    assert service.update_view(1, CREATOR, version=2)["code"] == "bad_request"
    assert cur.executed == []


def test_update_with_no_fields_is_a_bad_request(conn):
    cur = conn([])
    assert service.update_view(1, CREATOR)["code"] == "bad_request"
    assert cur.executed == []


def test_update_writes_payload_and_version_together(conn):
    cur = conn([(7,), _row()])
    service.update_view(1, CREATOR, payload={"query": "x"}, version=2)
    update_sql = [s for s, _ in cur.executed if s.startswith("UPDATE")][0]
    assert "payload = %s" in update_sql and "version = %s" in update_sql


def test_update_maps_a_rename_collision_to_conflict(conn):
    conn([(7,), _row()], raise_unique_on="UPDATE saved_views")
    assert service.update_view(1, CREATOR, name="taken")["code"] == "conflict"
