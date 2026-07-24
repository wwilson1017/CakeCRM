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


# ── log_note ──────────────────────────────────────────────────────────────────

def test_log_note_validates_target_then_inserts_returning(rec):
    # existence check returns a row, then the INSERT returns the new note
    rec.fetchone_queue = [{"?column?": 1}, {"id": 5, "message": "hi"}]
    result = chatter_service.log_note("deal", 3, "  hi  ")
    # existence check hit the deals table
    exist_sql = rec.sql_containing("FROM deals WHERE id = %s")
    assert "SELECT 1" in exist_sql
    assert rec.params_for("FROM deals WHERE id = %s") == [3]
    # insert used %s, RETURNING *, and the trimmed message
    insert_sql = rec.sql_containing("INSERT INTO crm_chatter")
    assert "RETURNING *" in insert_sql
    assert "%s" in insert_sql and "?" not in insert_sql
    params = rec.params_for("INSERT INTO crm_chatter")
    assert params[0] == "deal" and params[1] == 3 and params[2] == "hi"
    assert result == {"id": 5, "message": "hi"}


def test_log_note_contact_checks_contacts_table(rec):
    rec.fetchone_queue = [{"?column?": 1}, {"id": 9}]
    chatter_service.log_note("contact", 7, "note")
    assert rec.params_for("FROM contacts WHERE id = %s") == [7]


def test_log_note_blank_message_raises(rec):
    with pytest.raises(ValueError, match="Message is required"):
        chatter_service.log_note("deal", 3, "   ")


def test_log_note_too_long_raises(rec):
    with pytest.raises(ValueError, match="too long"):
        chatter_service.log_note("deal", 3, "x" * (chatter_service.MAX_MESSAGE_LEN + 1))


def test_log_note_bad_entity_type_raises(rec):
    with pytest.raises(ValueError, match="Invalid entity_type"):
        chatter_service.log_note("company", 3, "hi")


def test_log_note_nonexistent_target_raises(rec):
    rec.fetchone_queue = [None]  # existence check misses
    with pytest.raises(ValueError, match="No deal with id 999"):
        chatter_service.log_note("deal", 999, "hi")
    # never reached the insert
    assert not any("INSERT INTO crm_chatter" in s for s, _ in rec.calls)


def test_log_note_nonpositive_id_raises(rec):
    with pytest.raises(ValueError, match="positive integer"):
        chatter_service.log_note("deal", 0, "hi")


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
