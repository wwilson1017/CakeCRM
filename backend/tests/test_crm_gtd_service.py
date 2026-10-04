"""Todo-GTD service tests: the completion/recurrence transition and the queries (#70).

The transition is the riskiest code in the feature — it decides whether a repeating
todo spawns its next occurrence, and it keeps `completed` and `status` in step for a
CHECK constraint that will reject any disagreement.

These tests drive it through a cursor that mimics psycopg2's per-execute
``cursor.description`` so the REAL ``row_to_dict`` runs. That is deliberate: the spawn
step reads the post-UPDATE row and then issues another execute on the same cursor, and
a monkeypatched ``row_to_dict`` (or a cursor returning bare tuples) would hide a
description-reuse bug completely.
"""

import datetime
from contextlib import contextmanager

import pytest

from crm import gtd_service, service

# Columns of `todos` after the #70 migration, in the order RETURNING * yields them.
# `owner_id` sits between the original columns and the GTD ones because #60's
# migration is the earlier timestamp, so ALTER TABLE appends it first.
_TODO_COLS = [
    "id", "contact_id", "deal_id", "title", "description", "due_date", "completed",
    "priority", "created_at", "updated_at", "owner_id", "status", "star", "context",
    "tags", "repeat", "auto_star_on_due", "project_id", "completed_at", "source",
]


def _todo_row(**overrides) -> tuple:
    base = {
        "id": 1, "contact_id": None, "deal_id": None, "title": "Water the plants",
        "description": "", "due_date": "2026-08-14", "completed": 1,
        "priority": "medium", "created_at": None, "updated_at": None,
        "owner_id": None,
        "status": "done", "star": False, "context": "@home", "tags": "[]",
        "repeat": "weekly", "auto_star_on_due": False, "project_id": None,
        "completed_at": None, "source": "ui",
    }
    base.update(overrides)
    return tuple(base[c] for c in _TODO_COLS)


class _Cursor:
    """Rebinds `description` per execute, exactly like a real psycopg2 cursor."""

    def __init__(self, prior_status, returning_row):
        self._prior = prior_status
        self._returning = returning_row
        self.description = None
        self._rows: list = []
        self.executed: list = []
        self.rowcount = 1

    def execute(self, sql, params=()):
        norm = " ".join(sql.split())
        self.executed.append((norm, params))
        if "FOR UPDATE" in norm:
            self.description = [("status",)]
            self._rows = [(self._prior,)] if self._prior is not None else []
        elif "RETURNING *" in norm:
            self.description = [(c,) for c in _TODO_COLS]
            self._rows = [self._returning] if self._returning else []
        else:
            self.description = None
            self._rows = []

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def statements(self) -> list[str]:
        return [s for s, _ in self.executed]

    def inserted(self):
        """The spawn INSERT's (sql, params), or None if no occurrence was created."""
        return next(((s, p) for s, p in self.executed if "INSERT INTO todos" in s), None)


@pytest.fixture
def txn(monkeypatch):
    def _install(*, prior_status="next_action", **row_overrides):
        cur = _Cursor(prior_status, _todo_row(**row_overrides))

        class _Conn:
            def cursor(self):
                return cur

        @contextmanager
        def _get_connection():
            yield _Conn()

        monkeypatch.setattr(service, "get_connection", _get_connection)
        monkeypatch.setattr(service, "get_todo", lambda todo_id: {"id": todo_id})
        return cur

    return _install


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch):
    """Pin "today" so auto-star assertions can't drift with the wall clock."""
    monkeypatch.setattr(service, "today_local", lambda: datetime.date(2026, 8, 21))


# ── The completion transition ─────────────────────────────────────────────────

def test_completing_a_repeating_todo_spawns_the_next_occurrence(txn):
    cur = txn(prior_status="next_action", repeat="weekly", due_date="2026-08-14")
    service.update_todo(1, status="done")
    spawn = cur.inserted()
    assert spawn is not None
    # Completed exactly one week late, so the next occurrence lands on today.
    assert "2026-08-21" in spawn[1]


def test_the_spawned_occurrence_keeps_the_owner(txn):
    """A repeating todo someone owns (#60) must not come back unassigned — the spawn
    is the one todo-INSERT that copies its fields from an existing row."""
    cur = txn(prior_status="next_action", repeat="weekly", owner_id=7)
    service.update_todo(1, status="done")
    _, params = cur.inserted()
    assert params[6] == 7


def test_a_second_complete_does_not_spawn_again(txn):
    """The row lock plus the prior-status read make the transition fire exactly once —
    two concurrent completes serialize, and the second sees status='done' already."""
    cur = txn(prior_status="done", repeat="weekly")
    service.update_todo(1, status="done")
    assert cur.inserted() is None


def test_completing_a_non_repeating_todo_spawns_nothing(txn):
    cur = txn(prior_status="next_action", repeat="")
    service.update_todo(1, status="done")
    assert cur.inserted() is None


def test_clearing_repeat_while_completing_does_not_spawn(txn):
    """The spawn decision reads the POST-update row, so clearing `repeat` in the same
    write means no next occurrence — the user asked it to stop."""
    cur = txn(prior_status="next_action", repeat="")  # RETURNING * shows repeat cleared
    service.update_todo(1, status="done", repeat="")
    assert cur.inserted() is None


def test_the_spawned_occurrence_clears_the_star(txn):
    cur = txn(prior_status="next_action", repeat="weekly", star=True, due_date="2026-08-14")
    service.update_todo(1, status="done")
    _, params = cur.inserted()
    # star is the 9th INSERT column (title, description, due_date, contact_id,
    # deal_id, priority, owner_id, status, star, ...) — today's priority does not
    # carry over.
    assert params[8] is False


def test_auto_star_restars_only_when_the_spawn_is_due_today(txn):
    cur = txn(prior_status="next_action", repeat="weekly",
              auto_star_on_due=True, due_date="2026-08-14")
    service.update_todo(1, status="done")
    _, params = cur.inserted()
    assert params[8] is True  # next occurrence is 2026-08-21 == today


def test_auto_star_does_not_restar_when_completed_two_intervals_late(txn):
    cur = txn(prior_status="next_action", repeat="weekly",
              auto_star_on_due=True, due_date="2026-08-01")
    service.update_todo(1, status="done")
    _, params = cur.inserted()
    assert params[8] is False  # re-anchored past today, so not starred


def test_a_dropped_repeating_todo_returns_as_a_next_action(txn):
    """dropped/done are finished states with no sensible status to resume, so the new
    occurrence starts actionable rather than re-dropped."""
    cur = txn(prior_status="dropped", repeat="weekly")
    service.update_todo(1, status="done")
    _, params = cur.inserted()
    assert "next_action" in params


# ── completed <-> status coherence ────────────────────────────────────────────

def test_status_done_also_writes_completed_1(txn):
    cur = txn(prior_status="next_action", repeat="")
    service.update_todo(1, status="done")
    sql, params = next((s, p) for s, p in cur.executed if "UPDATE todos SET" in s)
    assert "completed = %s" in sql and 1 in params


def test_a_non_done_status_writes_completed_0_and_clears_completed_at(txn):
    cur = txn(prior_status="done", repeat="")
    service.update_todo(1, status="someday_maybe")
    sql, params = next((s, p) for s, p in cur.executed if "UPDATE todos SET" in s)
    assert 0 in params and "completed_at = NULL" in sql


def test_an_explicit_status_wins_over_completed(txn):
    """A caller sending both means the status — `completed` is the coarser spelling."""
    cur = txn(prior_status="next_action", repeat="")
    service.update_todo(1, completed=True, status="waiting_for")
    _, params = next((s, p) for s, p in cur.executed if "UPDATE todos SET" in s)
    assert "waiting_for" in params and "done" not in params


def test_the_row_is_locked_before_anything_is_written(txn):
    cur = txn(prior_status="next_action", repeat="")
    service.update_todo(1, title="New title")
    stmts = cur.statements()
    assert "FOR UPDATE" in stmts[0]
    assert stmts.index(next(s for s in stmts if "UPDATE todos SET" in s)) > 0


def test_a_missing_todo_returns_none_and_writes_nothing(txn):
    cur = txn(prior_status=None)
    assert service.update_todo(999, status="done") is None
    assert not any("UPDATE todos SET" in s for s in cur.statements())


# ── Bulk update ───────────────────────────────────────────────────────────────

def test_bulk_update_loops_per_id_and_never_uses_any(monkeypatch):
    """One UPDATE ... WHERE id = ANY(...) would be faster and WRONG: each repeating
    todo has to spawn its own next occurrence."""
    seen = []
    monkeypatch.setattr(
        gtd_service.service, "_apply_todo_update_cur",
        lambda cur, tid, fields: seen.append(tid) or True,
    )

    @contextmanager
    def _get_connection():
        class _Conn:
            def cursor(self):
                return object()
        yield _Conn()

    monkeypatch.setattr(gtd_service, "get_connection", _get_connection)
    result = gtd_service.bulk_update([3, 1, 2], {"status": "done"})
    assert seen == [1, 2, 3]  # sorted, so overlapping bulk updates can't deadlock
    assert result == {"updated": [1, 2, 3], "not_found": []}


def test_bulk_update_rejects_an_oversized_batch():
    with pytest.raises(gtd_service.ValidationError, match="too many ids"):
        gtd_service.bulk_update(list(range(501)), {"status": "done"})


def test_bulk_update_rejects_unknown_fields():
    with pytest.raises(gtd_service.ValidationError, match="Unknown fields"):
        gtd_service.bulk_update([1], {"lead_score": 99})


def test_bulk_update_requires_ids():
    with pytest.raises(gtd_service.ValidationError, match="ids is required"):
        gtd_service.bulk_update([], {"status": "done"})


# ── Query shapes ──────────────────────────────────────────────────────────────

class _Rec:
    def __init__(self):
        self.calls: list[tuple[str, list]] = []
        self.rows: list = []

    def fetchall(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), list(params)))
        return self.rows

    def fetchone(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), list(params)))
        return None

    def sql(self, needle: str) -> str:
        for s, _ in self.calls:
            if needle in s:
                return s
        raise AssertionError(f"no SQL contains {needle!r}: {[s for s, _ in self.calls]}")


@pytest.fixture
def rec(monkeypatch):
    r = _Rec()
    monkeypatch.setattr(gtd_service, "pg_fetchall", r.fetchall)
    monkeypatch.setattr(gtd_service, "pg_fetchone", r.fetchone)
    return r


def test_finished_lists_are_ordered_newest_first(rec):
    gtd_service.list_todos(status="done")
    sql = rec.sql("FROM todos")
    assert "ORDER BY COALESCE(t.completed_at, t.updated_at) DESC" in sql


def test_a_status_less_search_puts_open_todos_first(rec):
    """Repeating todos accumulate done copies with identical titles; the live one must
    win the LIMIT window."""
    gtd_service.list_todos(search="vanilla")
    assert "ORDER BY (t.status IN ('done','dropped')) ASC" in rec.sql("FROM todos")


def test_open_lists_are_ordered_oldest_first(rec):
    gtd_service.list_todos(status="next_action")
    assert "ORDER BY t.created_at ASC" in rec.sql("FROM todos")


def test_lists_exclude_todos_on_archived_deals(rec):
    """Work items follow the deal out of view in GTD mode too, matching list_todos."""
    gtd_service.list_todos(status="inbox")
    assert "archived_at IS NULL" in rec.sql("FROM todos")


def test_limit_is_clamped_and_an_explicit_zero_is_not_treated_as_unset(rec):
    gtd_service.list_todos(limit=0)
    assert rec.calls[-1][1][-1] == 1
    gtd_service.list_todos(limit=99999)
    assert rec.calls[-1][1][-1] == 500


def test_tag_filter_uses_jsonb_exists_not_a_like(rec):
    """`?` is the jsonb operator but psycopg2 would read it as a placeholder, so it
    must be spelled as a function."""
    gtd_service.list_todos(tag="errand")
    assert "jsonb_exists(t.tags, %s)" in rec.sql("FROM todos")


def test_search_escapes_like_wildcards(rec):
    gtd_service.list_todos(search="50%_off")
    params = rec.calls[-1][1]
    assert any(isinstance(p, str) and "50\\%\\_off" in p for p in params)


def test_an_unknown_project_name_returns_empty_without_querying_todos(rec):
    rec.rows = []
    assert gtd_service.list_todos(project="nope") == []
    assert not any("FROM todos t" in s for s, _ in rec.calls)


def test_today_view_shows_starred_due_and_overdue_only(rec):
    gtd_service.today_view()
    sql = rec.sql("FROM todos")
    assert "t.status NOT IN ('done','dropped')" in sql
    assert "t.star OR (t.due_date != '' AND t.due_date <= %s)" in sql


def test_filters_carry_the_install_timezone(rec, monkeypatch):
    # The GTD client buckets Overdue vs Due today in THIS zone (#259), so it must be the
    # zone today_view uses — including its UTC fallback for a bogus value.
    monkeypatch.setenv("TIMEZONE", "America/Chicago")
    assert gtd_service.get_filters()["tz"] == "America/Chicago"
    monkeypatch.setenv("TIMEZONE", "Not/AZone")
    assert gtd_service.get_filters()["tz"] == "UTC"
    monkeypatch.delenv("TIMEZONE")
    assert gtd_service.get_filters()["tz"] == "UTC"
