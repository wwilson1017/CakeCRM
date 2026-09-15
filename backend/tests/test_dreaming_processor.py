"""dreaming/processor.py — the one-transaction score/archive/audit cycle.

Uses a keyword-dispatching fake connection: fetch results are chosen by matching the
last-executed SQL, so the whole flow (advisory lock → due check → SELECT FOR UPDATE →
UPDATE RETURNING → audit INSERT) is assertable without a database. The real scorer is
used (crafted inputs) so classification stays honest.

Since #72 Phase 4 the cycle has TWO units in the same transaction, and the dispatch keys
on the TABLE NAME first. That is not cosmetic: both units issue a `... FOR UPDATE` and a
`... RETURNING id`, so the original substring-only dispatch would have handed the file
SELECT a list of fact tuples and every assertion in this file would have kept passing
while testing nothing.
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

# Live-FILE tuple layout _SELECT_LIVE_FILES returns:
# (id, filename, read_count, days_since_read, days_since_written, days_old)
FILE_DORMANT_OLD = (11, "topics/old.md", 0, None, 200.0, 200.0)     # ~0.0 dormant, 200d
FILE_DORMANT_YOUNG = (12, "topics/new-but-quiet.md", 0, None, 5.0, 5.0)  # fresh -> active
FILE_READ_RECENTLY = (13, "topics/hot.md", 3, 5.0, 200.0, 200.0)    # ~0.36 stale, kept

_FACTS_TABLE = "memory_facts"
_FILES_TABLE = "assistant_context_files"


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
        # Table name FIRST — both units issue a FOR UPDATE and a RETURNING id, so
        # dispatching on those substrings alone would cross the wires (see the module
        # docstring).
        files = _FILES_TABLE in self._last
        if "FOR UPDATE" in self._last:
            return list(self._conn.live_files if files else self._conn.live_rows)
        if "RETURNING" in self._last:
            if files:
                # _ARCHIVE_FILES returns (id, filename); the processor reads [1].
                return [(i, name) for i, name in self._conn.returning_files]
            return [(i,) for i in self._conn.returning_ids]
        return []


class FakeConn:
    def __init__(self, live_rows=(), returning_ids=(), lock_ok=True, last_ok=None,
                 live_files=(), returning_files=()):
        self.executed = []
        self.live_rows = live_rows
        self.returning_ids = returning_ids
        self.lock_ok = lock_ok
        self.last_ok = last_ok
        self.live_files = live_files
        self.returning_files = returning_files

    def cursor(self):
        return FakeCursor(self)

    def sql_matching(self, needle):
        return [sql for sql, _ in self.executed if needle in sql]

    def params_for(self, needle):
        return [p for sql, p in self.executed if needle in sql]

    def _table_sql(self, table, needle):
        return [sql for sql, _ in self.executed if table in sql and needle in sql]

    def _table_params(self, table, needle):
        return [p for sql, p in self.executed if table in sql and needle in sql]

    def fact_archive_sql(self):
        return self._table_sql(_FACTS_TABLE, "RETURNING id")

    def fact_archive_params(self):
        return self._table_params(_FACTS_TABLE, "RETURNING id")

    def file_select_sql(self):
        return self._table_sql(_FILES_TABLE, "FOR UPDATE")

    def file_archive_sql(self):
        return self._table_sql(_FILES_TABLE, "RETURNING")

    def file_archive_params(self):
        return self._table_params(_FILES_TABLE, "RETURNING")


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
    archive_sql = conn.fact_archive_sql()[0]
    assert "archived_at IS NULL" in archive_sql and "!= ALL(%s)" in archive_sql
    assert "make_interval(days => %s)" in archive_sql
    cand_params = conn.fact_archive_params()[0]
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
    assert conn.fact_archive_sql() == []   # no UPDATE issued at all
    assert conn.sql_matching("INSERT INTO dreaming_runs")  # but a run is still recorded


def test_empty_db_records_noop_run(install):
    conn = install(FakeConn(live_rows=[]))
    out = processor.run_dreaming_cycle()
    assert out == {
        "facts_scored": 0, "facts_archived": 0, "archived": [],
        "files_scored": 0, "files_archived": 0, "archived_files": [],
        "duration_ms": out["duration_ms"],
    }
    assert conn.fact_archive_sql() == [] and conn.file_archive_sql() == []
    insert = conn.params_for("INSERT INTO dreaming_runs")[0]
    assert insert[0] == 0 and insert[1] == 0 and insert[2] == 0 and insert[3] == 0


def test_facts_archived_from_returning_not_candidate_count(install):
    # Two candidates, but the UPDATE only actually archived one (race/guard) →
    # facts_archived reflects RETURNING, not the candidate list.
    other_dormant = (4, "Other", "p", "o", None, 0, 1.0, None, 300.0)
    conn = install(FakeConn(live_rows=[DORMANT_OLD, other_dormant], returning_ids=[1]))
    out = processor.run_dreaming_cycle()
    assert sorted(conn.fact_archive_params()[0][0]) == [1, 4]  # both candidates offered
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
    assert conn.fact_archive_sql() == []   # no archive UPDATE issued


# ── the topic-file pass (#72 Phase 4) ───────────────────────────────────────────

def test_file_select_restricts_to_live_unprotected_topic_files(install):
    conn = install(FakeConn(live_files=[FILE_DORMANT_OLD], returning_files=[(11, "topics/old.md")]))
    processor.run_dreaming_cycle()
    select_sql = conn.file_select_sql()[0]
    assert "archived_at IS NULL" in select_sql
    assert "kind = 'topic'" in select_sql
    assert "is_protected = FALSE" in select_sql
    # NULL last_read_at must survive as NULL (never-read != read-today).
    assert "WHEN last_read_at IS NULL THEN NULL" in select_sql
    assert "ORDER BY id FOR UPDATE" in select_sql


def test_file_archive_reasserts_exemptions_and_min_age_and_never_touches_updated_at(install):
    conn = install(FakeConn(live_files=[FILE_DORMANT_OLD], returning_files=[(11, "topics/old.md")]))
    processor.run_dreaming_cycle()
    sql = conn.file_archive_sql()[0]
    assert "kind = 'topic'" in sql and "is_protected = FALSE" in sql
    assert "archived_at IS NULL" in sql
    assert "make_interval(days => %s)" in sql
    # updated_at is the write-recency signal AND the editor's concurrency token.
    assert "updated_at" not in sql
    params = conn.file_archive_params()[0]
    assert params[0] == [11]
    assert params[1] == processor.MIN_AGE_DAYS_FOR_ARCHIVE


def test_dormant_old_topic_file_is_archived(install):
    conn = install(FakeConn(live_files=[FILE_DORMANT_OLD, FILE_READ_RECENTLY],
                            returning_files=[(11, "topics/old.md")]))
    out = processor.run_dreaming_cycle()
    assert out["files_scored"] == 2
    assert out["files_archived"] == 1
    assert out["archived_files"] == ["topics/old.md"]
    # The recently-read one scores stale, so it is never offered as a candidate.
    assert conn.file_archive_params()[0][0] == [11]


def test_young_dormant_file_is_never_a_candidate(install, monkeypatch):
    conn = install(FakeConn(live_files=[FILE_DORMANT_YOUNG]))
    monkeypatch.setattr(processor.scorer, "score_file",
                        lambda *a, **k: {"score": 0.0, "classification": "dormant", "signals": {}})
    out = processor.run_dreaming_cycle()
    assert out["files_archived"] == 0
    assert conn.file_archive_sql() == []   # no UPDATE issued at all


def test_files_archived_comes_from_returning_not_the_candidate_list(install):
    other = (14, "topics/other.md", 0, None, 300.0, 300.0)
    conn = install(FakeConn(live_files=[FILE_DORMANT_OLD, other],
                            returning_files=[(11, "topics/old.md")]))
    out = processor.run_dreaming_cycle()
    assert sorted(conn.file_archive_params()[0][0]) == [11, 14]   # both offered
    assert out["files_archived"] == 1 and out["archived_files"] == ["topics/old.md"]


def test_audit_row_carries_both_units(install):
    conn = install(FakeConn(live_rows=[DORMANT_OLD], returning_ids=[1],
                            live_files=[FILE_DORMANT_OLD], returning_files=[(11, "topics/old.md")]))
    processor.run_dreaming_cycle()
    insert_sql = conn.sql_matching("INSERT INTO dreaming_runs")[0]
    assert "files_scored" in insert_sql and "files_archived" in insert_sql
    insert = conn.params_for("INSERT INTO dreaming_runs")[0]
    assert insert[0] == 1 and insert[1] == 1      # facts scored / archived
    assert insert[2] == 1 and insert[3] == 1      # files scored / archived
    details = insert[4]
    assert '"file_scores"' in details and '"archived_files"' in details


def test_file_pass_error_rolls_back_the_whole_cycle(install, monkeypatch):
    # One transaction means one outcome: a file-pass failure must not leave a
    # half-recorded 'ok' run claiming the facts were processed.
    conn = install(FakeConn(live_rows=[DORMANT_OLD], returning_ids=[1],
                            live_files=[FILE_DORMANT_OLD]))
    monkeypatch.setattr(processor.scorer, "score_file",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        processor.run_dreaming_cycle()
    assert conn.sql_matching("INSERT INTO dreaming_runs (finished_at, status, facts_scored, facts_archived, files_scored") == []
    assert any("'error'" in sql for sql in conn.sql_matching("INSERT INTO dreaming_runs"))


def test_file_pass_runs_on_the_same_cursor_after_the_fact_pass(install):
    # Ordering matters: the file SELECT must sit between the fact archive and the audit
    # INSERT, inside the SET LOCAL timeouts, so it inherits the transaction's bounds.
    conn = install(FakeConn(live_rows=[DORMANT_OLD], returning_ids=[1],
                            live_files=[FILE_DORMANT_OLD], returning_files=[(11, "topics/old.md")]))
    processor.run_dreaming_cycle()
    order = [sql for sql, _ in conn.executed]
    i_timeout = next(i for i, s in enumerate(order) if "statement_timeout" in s)
    i_facts = next(i for i, s in enumerate(order) if "memory_facts" in s and "FOR UPDATE" in s)
    i_files = next(i for i, s in enumerate(order) if "assistant_context_files" in s and "FOR UPDATE" in s)
    i_audit = next(i for i, s in enumerate(order) if "INSERT INTO dreaming_runs" in s)
    assert i_timeout < i_facts < i_files < i_audit
