"""dreaming/processor.py — the one-transaction score/archive/audit cycle.

Uses a keyword-dispatching fake connection: fetch results are chosen by matching the
last-executed SQL, so the whole flow (advisory lock → due check → SELECT FOR UPDATE →
UPDATE RETURNING → audit INSERT) is assertable without a database. The real scorer is
used (crafted inputs) so classification stays honest.
"""

import contextlib

import pytest

from dreaming import processor

# Live-fact tuple layout the processor expects from _SELECT_LIVE:
# (id, subject, predicate, object, memory_type, retrieval_count, confidence,
#  days_since_retrieved, days_old)
DORMANT_OLD = (1, "Old Fact", "p", "o", None, 0, 1.0, None, 200.0)          # score 0.05 dormant, 200d
TIER1_DORMANT_OLD = (2, "A Decision", "p", "o", "decision", 0, 1.0, None, 200.0)  # dormant but protected
FRESH = (3, "Fresh", "p", "o", None, 3, 1.0, 0.0, 5.0)                       # active


class FakeCursor:
    def __init__(self, conn):
        self._conn = conn
        self._last = ""

    def execute(self, sql, params=()):
        self._last = " ".join(sql.split())
        self._conn.executed.append((self._last, params))

    def fetchone(self):
        if "pg_try_advisory_xact_lock" in self._last:
            return (self._conn.lock_ok,)
        if "max(finished_at)" in self._last:
            return (self._conn.last_ok,)
        return None

    def fetchall(self):
        if "FOR UPDATE" in self._last:
            return list(self._conn.live_rows)
        if "RETURNING id" in self._last:
            return [(i,) for i in self._conn.returning_ids]
        return []


class FakeConn:
    def __init__(self, live_rows=(), returning_ids=(), lock_ok=True, last_ok=None):
        self.executed = []
        self.live_rows = live_rows
        self.returning_ids = returning_ids
        self.lock_ok = lock_ok
        self.last_ok = last_ok

    def cursor(self):
        return FakeCursor(self)

    def sql_matching(self, needle):
        return [sql for sql, _ in self.executed if needle in sql]

    def params_for(self, needle):
        return [p for sql, p in self.executed if needle in sql]


@pytest.fixture
def install(monkeypatch):
    def _install(conn):
        @contextlib.contextmanager
        def _get_connection():
            yield conn
        monkeypatch.setattr(processor, "get_connection", _get_connection)
        return conn
    return _install


# ── _run_cycle / run_dreaming_cycle ─────────────────────────────────────────────

def test_cycle_archives_dormant_nontier1_old_fact(install):
    conn = install(FakeConn(live_rows=[DORMANT_OLD, FRESH], returning_ids=[1]))
    out = processor.run_dreaming_cycle()
    assert out["facts_scored"] == 2
    assert out["facts_archived"] == 1
    assert out["archived"] == [1]
    # UPDATE guards present, tier-1 param passed, candidate is the dormant old id.
    archive_sql = conn.sql_matching("RETURNING id")[0]
    assert "archived_at IS NULL" in archive_sql and "!= ALL(%s)" in archive_sql
    assert "make_interval(days => %s)" in archive_sql
    cand_params = conn.params_for("RETURNING id")[0]
    assert cand_params[0] == [1]                       # only the dormant non-tier-1 old fact
    assert cand_params[1] == ["decision", "preference"]  # sorted tier-1 protection list


def test_cycle_records_audit_row_always(install):
    conn = install(FakeConn(live_rows=[DORMANT_OLD], returning_ids=[1]))
    processor.run_dreaming_cycle()
    insert = conn.params_for("INSERT INTO dreaming_runs")[0]
    assert insert[0] == 1        # facts_scored
    assert insert[1] == 1        # facts_archived


def test_tier1_dormant_fact_is_not_a_candidate(install):
    conn = install(FakeConn(live_rows=[TIER1_DORMANT_OLD]))
    out = processor.run_dreaming_cycle()
    assert out["facts_archived"] == 0
    assert conn.sql_matching("RETURNING id") == []   # no UPDATE issued at all
    assert conn.sql_matching("INSERT INTO dreaming_runs")  # but a run is still recorded


def test_empty_db_records_noop_run(install):
    conn = install(FakeConn(live_rows=[]))
    out = processor.run_dreaming_cycle()
    assert out == {"facts_scored": 0, "facts_archived": 0, "archived": [], "duration_ms": out["duration_ms"]}
    assert conn.sql_matching("RETURNING id") == []
    insert = conn.params_for("INSERT INTO dreaming_runs")[0]
    assert insert[0] == 0 and insert[1] == 0


def test_facts_archived_from_returning_not_candidate_count(install):
    # Two candidates, but the UPDATE only actually archived one (race/guard) →
    # facts_archived reflects RETURNING, not the candidate list.
    other_dormant = (4, "Other", "p", "o", None, 0, 1.0, None, 300.0)
    conn = install(FakeConn(live_rows=[DORMANT_OLD, other_dormant], returning_ids=[1]))
    out = processor.run_dreaming_cycle()
    assert sorted(conn.params_for("RETURNING id")[0][0]) == [1, 4]  # both candidates offered
    assert out["facts_archived"] == 1 and out["archived"] == [1]    # only one came back


def test_cycle_failure_records_error_and_raises(install, monkeypatch):
    conn = install(FakeConn(live_rows=[DORMANT_OLD]))
    monkeypatch.setattr(processor.scorer, "score_fact",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        processor.run_dreaming_cycle()
    # a status='error' audit row was recorded
    assert any("'error'" in sql for sql in conn.sql_matching("INSERT INTO dreaming_runs"))


# ── run_dreaming_if_due ─────────────────────────────────────────────────────────

def test_if_due_skips_when_lock_not_acquired(install):
    conn = install(FakeConn(live_rows=[DORMANT_OLD], lock_ok=False))
    assert processor.run_dreaming_if_due() is None
    assert conn.sql_matching("FOR UPDATE") == []   # never scored


def test_if_due_skips_when_not_due(install, monkeypatch):
    conn = install(FakeConn(live_rows=[DORMANT_OLD], lock_ok=True, last_ok="recent"))
    monkeypatch.setattr(processor, "is_due", lambda last_ok, now: False)
    assert processor.run_dreaming_if_due() is None
    assert conn.sql_matching("FOR UPDATE") == []


def test_if_due_runs_when_due_and_locked(install, monkeypatch):
    conn = install(FakeConn(live_rows=[DORMANT_OLD, FRESH], returning_ids=[1], lock_ok=True))
    monkeypatch.setattr(processor, "is_due", lambda last_ok, now: True)
    out = processor.run_dreaming_if_due()
    assert out["facts_archived"] == 1
    assert conn.sql_matching("FOR UPDATE")


def test_if_due_never_raises_on_failure(install, monkeypatch):
    conn = install(FakeConn(live_rows=[DORMANT_OLD], lock_ok=True))
    monkeypatch.setattr(processor, "is_due", lambda last_ok, now: True)
    monkeypatch.setattr(processor.scorer, "score_fact",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    out = processor.run_dreaming_if_due()
    assert out["status"] == "error"
    assert any("'error'" in sql for sql in conn.sql_matching("INSERT INTO dreaming_runs"))


def test_young_dormant_fact_is_not_a_candidate(install, monkeypatch):
    # A fact scored dormant but younger than MIN_AGE_DAYS_FOR_ARCHIVE must never be
    # archived (belt-and-suspenders guard against a future scorer-weight change).
    young = (5, "Young", "p", "o", None, 0, 1.0, None, 10.0)  # 10 days old
    conn = install(FakeConn(live_rows=[young]))
    monkeypatch.setattr(processor.scorer, "score_fact",
                        lambda *a, **k: {"score": 0.0, "classification": "dormant", "signals": {}})
    out = processor.run_dreaming_cycle()
    assert out["facts_archived"] == 0
    assert conn.sql_matching("RETURNING id") == []   # no archive UPDATE issued
