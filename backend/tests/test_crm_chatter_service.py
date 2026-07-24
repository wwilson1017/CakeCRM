"""Hermetic chatter-service tests — SQL shape, validation, and behavior.

Mirrors test_crm_service.py: the module imports pg_fetchone/pg_fetchall by name,
so we monkeypatch them with a small recorder that queues return rows.
"""

import pytest

from crm import chatter_service


class Recorder:
    def __init__(self):
        self.calls: list[tuple[str, list]] = []
        self.fetchone_queue: list = []
        self.fetchall_queue: list = []

    def fetchone(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), list(params)))
        return self.fetchone_queue.pop(0) if self.fetchone_queue else None

    def fetchall(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), list(params)))
        return self.fetchall_queue.pop(0) if self.fetchall_queue else []

    def sql_containing(self, needle: str) -> str:
        for sql, _ in self.calls:
            if needle in sql:
                return sql
        raise AssertionError(f"no recorded SQL contains {needle!r}: {[s for s, _ in self.calls]}")

    def params_for(self, needle: str) -> list:
        for sql, params in self.calls:
            if needle in sql:
                return params
        raise AssertionError(f"no recorded SQL contains {needle!r}")


@pytest.fixture
def rec(monkeypatch):
    r = Recorder()
    monkeypatch.setattr(chatter_service, "pg_fetchone", r.fetchone)
    monkeypatch.setattr(chatter_service, "pg_fetchall", r.fetchall)
    return r


# ── add_note (transactional: existence-check + INSERT in one txn) ───────────────

def test_add_note_locks_target_then_inserts_in_one_txn(monkeypatch, fake_conn):
    # In-txn: existence check returns a row, then INSERT ... RETURNING * → row tuple,
    # hydrated via row_to_dict before the transaction commits (no post-commit re-select).
    row = (5, "deal", 3, "hi", "2026-01-01T00:00:00+00:00", None, 0)
    conn = fake_conn(monkeypatch, chatter_service, fetchone_results=[(1,), row])
    monkeypatch.setattr(chatter_service, "row_to_dict", lambda cur, r: {"id": r[0], "message": r[3]})
    result = chatter_service.add_note("deal", 3, "  hi  ")
    stmts = [s for s, _ in conn.executed]
    # existence check locks the target row in the same transaction as the insert
    assert any("SELECT 1 FROM deals WHERE id = %s FOR UPDATE" in s for s in stmts)
    insert_sql = next(s for s in stmts if "INSERT INTO crm_chatter" in s)
    assert "RETURNING *" in insert_sql
    assert "%s" in insert_sql and "?" not in insert_sql
    insert_params = next(p for s, p in conn.executed if "INSERT INTO crm_chatter" in s)
    assert insert_params[0] == "deal" and insert_params[1] == 3 and insert_params[2] == "hi"  # trimmed
    assert result == {"id": 5, "message": "hi"}


def test_add_note_contact_locks_contacts_table(monkeypatch, fake_conn):
    row = (9, "contact", 7, "note", "2026-01-01T00:00:00+00:00", None, 0)
    conn = fake_conn(monkeypatch, chatter_service, fetchone_results=[(1,), row])
    monkeypatch.setattr(chatter_service, "row_to_dict", lambda cur, r: {"id": r[0]})
    chatter_service.add_note("contact", 7, "note")
    assert any("FROM contacts WHERE id = %s FOR UPDATE" in s for s, _ in conn.executed)


def test_add_note_nonexistent_target_raises_no_insert(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, chatter_service, fetchone_results=[None])  # existence check misses
    with pytest.raises(ValueError, match="No deal with id 999"):
        chatter_service.add_note("deal", 999, "hi")
    assert not any("INSERT INTO crm_chatter" in s for s, _ in conn.executed)


def test_add_note_blank_message_raises():
    with pytest.raises(ValueError, match="Message is required"):
        chatter_service.add_note("deal", 3, "   ")


def test_add_note_too_long_raises():
    with pytest.raises(ValueError, match="too long"):
        chatter_service.add_note("deal", 3, "x" * (chatter_service.MAX_MESSAGE_LEN + 1))


def test_add_note_bad_entity_type_raises():
    with pytest.raises(ValueError, match="Invalid entity_type"):
        chatter_service.add_note("company", 3, "hi")


def test_add_note_nonpositive_id_raises():
    with pytest.raises(ValueError, match="positive integer"):
        chatter_service.add_note("deal", 0, "hi")


# ── get_chatter ───────────────────────────────────────────────────────────────

def test_get_chatter_filters_archived_and_orders_deterministically(rec):
    rec.fetchall_queue = [[{"id": 1}]]
    chatter_service.get_chatter("deal", 3)
    sql = rec.sql_containing("FROM crm_chatter")
    assert "archived = 0" in sql
    assert "ORDER BY created_at DESC, id DESC" in sql
    assert rec.params_for("FROM crm_chatter")[:2] == ["deal", 3]


def test_get_chatter_include_archived_drops_filter(rec):
    rec.fetchall_queue = [[]]
    chatter_service.get_chatter("deal", 3, include_archived=True)
    sql = rec.sql_containing("FROM crm_chatter")
    assert "archived = 0" not in sql


def test_get_chatter_bounds_limit(rec):
    rec.fetchall_queue = [[]]
    chatter_service.get_chatter("deal", 3, limit=9999)
    assert 200 in rec.params_for("FROM crm_chatter")  # clamped to the max


def test_get_chatter_bad_type_raises(rec):
    with pytest.raises(ValueError, match="Invalid entity_type"):
        chatter_service.get_chatter("company", 3)


def test_get_chatter_passes_bounded_offset(rec):
    rec.fetchall_queue = [[]]
    chatter_service.get_chatter("deal", 3, limit=10, offset=25)
    params = rec.params_for("FROM crm_chatter")
    assert params[-2:] == [10, 25]  # LIMIT then OFFSET


def test_get_chatter_returns_empty_for_nonexistent_entity(rec):
    # get_chatter is type-only (no existence check) — a well-typed but unknown id
    # yields [] rather than raising, so the notes panel degrades gracefully if its
    # entity was deleted underneath it.
    rec.fetchall_queue = [[]]
    assert chatter_service.get_chatter("deal", 999999) == []


def test_bounded_limit_and_offset_edges():
    assert chatter_service._bounded_limit(0) == 1
    assert chatter_service._bounded_limit(-5) == 1
    assert chatter_service._bounded_limit(9999) == 200
    assert chatter_service._bounded_limit("abc") == 50
    assert chatter_service._bounded_offset(-10) == 0
    assert chatter_service._bounded_offset("xyz") == 0
    assert chatter_service._bounded_offset(7) == 7


# ── update / archive / unarchive ────────────────────────────────────────────────

def test_update_note_sets_updated_at_returning(rec):
    rec.fetchone_queue = [{"id": 5, "message": "new", "updated_at": "t"}]
    result = chatter_service.update_note(5, " new ")
    sql = rec.sql_containing("UPDATE crm_chatter SET message")
    assert "updated_at = %s" in sql and "RETURNING *" in sql
    params = rec.params_for("UPDATE crm_chatter SET message")
    assert params[0] == "new" and params[-1] == 5
    assert result["message"] == "new"


def test_update_note_missing_returns_none(rec):
    rec.fetchone_queue = [None]
    assert chatter_service.update_note(999, "x") is None


def test_update_note_blank_raises(rec):
    with pytest.raises(ValueError, match="Message is required"):
        chatter_service.update_note(5, "")


def test_update_note_too_long_raises(rec):
    with pytest.raises(ValueError, match="too long"):
        chatter_service.update_note(5, "x" * (chatter_service.MAX_MESSAGE_LEN + 1))


def test_archive_note_returns_true_when_row(rec):
    rec.fetchone_queue = [{"id": 5}]
    assert chatter_service.archive_note(5) is True
    sql = rec.sql_containing("UPDATE crm_chatter SET archived = 1")
    assert "RETURNING id" in sql


def test_archive_note_missing_returns_none(rec):
    rec.fetchone_queue = [None]
    assert chatter_service.archive_note(999) is None


def test_unarchive_note_returns_true_when_row(rec):
    rec.fetchone_queue = [{"id": 5}]
    assert chatter_service.unarchive_note(5) is True
    assert "archived = 0" in rec.sql_containing("UPDATE crm_chatter SET archived = 0")
