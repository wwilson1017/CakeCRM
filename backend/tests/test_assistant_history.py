"""Assistant history — hermetic SQL-shape + behavior tests.

The pg_*-based functions are covered with a Recorder (patching the imported
helpers, since FakeCursor can't feed row_to_dict). The get_connection-based
transactional functions (save_message / merge_tool_result) are checked here for
SQL shape (FOR UPDATE, seq math) via the shared fake_conn fixture; their full
concurrency correctness — and claim_pending_tool, which needs row_to_dict — lives
in tests/test_assistant_history_pg.py (real Postgres).
"""

import json

import pytest

from assistant import history


class Recorder:
    def __init__(self):
        self.calls: list[tuple[str, list]] = []
        self.fetchone_queue: list = []
        self.fetchall_queue: list = []
        self.rowcount = 1

    def fetchone(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), list(params)))
        return self.fetchone_queue.pop(0) if self.fetchone_queue else None

    def fetchall(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), list(params)))
        return self.fetchall_queue.pop(0) if self.fetchall_queue else []

    def execute(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), list(params)))
        return self.rowcount

    def sql_with(self, needle):
        for sql, _ in self.calls:
            if needle in sql:
                return sql
        raise AssertionError(f"no SQL contains {needle!r}: {[s for s, _ in self.calls]}")

    def params_with(self, needle):
        for sql, p in self.calls:
            if needle in sql:
                return p
        raise AssertionError(f"no SQL contains {needle!r}")


@pytest.fixture
def rec(monkeypatch):
    r = Recorder()
    monkeypatch.setattr(history, "pg_fetchone", r.fetchone)
    monkeypatch.setattr(history, "pg_fetchall", r.fetchall)
    monkeypatch.setattr(history, "pg_execute", r.execute)
    return r


# ── Pure predicates ───────────────────────────────────────────────────────────

def test_is_pending_result_exact_on_status():
    assert history.is_pending_result(history.PENDING_RESULT_JSON) is True
    # a real result that merely MENTIONS the phrase is not pending
    assert history.is_pending_result(json.dumps({"note": "pending_user_approval happened"})) is False
    assert history.is_pending_result(json.dumps({"status": "denied_by_user"})) is False
    assert history.is_pending_result(None) is False
    assert history.is_pending_result("not json") is False


# ── pg_*-based functions ──────────────────────────────────────────────────────

def test_create_conversation_inserts_uuid(rec):
    rec.fetchone_queue = [{"id": "abc", "title": "New conversation"}]
    out = history.create_conversation()
    assert out["id"] == "abc"
    assert "INSERT INTO assistant_conversations" in rec.sql_with("INSERT INTO assistant_conversations")


def test_auto_title_truncates_and_guards_user_edit(rec):
    long = "x" * 200
    title = history.auto_title("c1", long)
    assert title.endswith("...") and len(title) <= history._TITLE_MAX
    # never overrides a user-edited title
    assert "title_edited_by_user = FALSE" in rec.sql_with("UPDATE assistant_conversations")


def test_auto_title_blank_falls_back(rec):
    assert history.auto_title("c1", "   ") == "New conversation"


def test_list_conversations_clamps_limit(rec):
    rec.fetchall_queue = [[]]
    history.list_conversations(limit=99999, offset=-5)
    params = rec.params_with("FROM assistant_conversations")
    assert params[-2] == 200 and params[-1] == 0  # clamped


def test_get_conversation_none_when_missing(rec):
    rec.fetchone_queue = [None]
    assert history.get_conversation("nope") is None


def test_get_conversation_attaches_messages(rec):
    rec.fetchone_queue = [{"id": "c1", "title": "t"}]
    rec.fetchall_queue = [[{"id": "m1", "role": "user", "content": "hi", "seq": 0}]]
    conv = history.get_conversation("c1")
    assert conv["messages"][0]["id"] == "m1"


def test_rename_blank_returns_none(rec):
    assert history.rename_conversation("c1", "   ") is None


def test_rename_sets_user_edited(rec):
    rec.rowcount = 1
    assert history.rename_conversation("c1", "My chat") == "My chat"
    assert "title_edited_by_user = TRUE" in rec.sql_with("UPDATE assistant_conversations")


def test_delete_conversation_reports_rowcount(rec):
    rec.rowcount = 0
    assert history.delete_conversation("gone") is False
    rec.rowcount = 1
    assert history.delete_conversation("c1") is True


def test_get_tool_result_by_msg_id_parses_content(rec):
    rec.fetchone_queue = [{"tool_results": [{"tool_use_id": "t1", "content": '{"ok": true}'}]}]
    assert history.get_tool_result("c1", "t1", msg_id="m1") == {"ok": True}


def test_get_tool_result_missing_returns_none(rec):
    rec.fetchone_queue = [{"tool_results": [{"tool_use_id": "other", "content": "{}"}]}]
    assert history.get_tool_result("c1", "t1", msg_id="m1") is None


# ── Transactional functions (SQL shape via fake_conn) ─────────────────────────

def test_save_message_locks_and_allocates_seq(fake_conn, monkeypatch):
    conn = fake_conn(monkeypatch, history, fetchone_results=[("c1",), (6,)])
    history.save_message("c1", "m1", "assistant", "text", tool_calls=[{"tool": "x"}], model="m")
    sqls = " || ".join(s for s, _ in conn.executed)
    assert "FOR UPDATE" in sqls
    assert "INSERT INTO assistant_messages" in sqls
    # seq allocated from MAX(seq)+1 = 6
    insert = next(p for s, p in conn.executed if "INSERT INTO assistant_messages" in s)
    assert 6 in insert


def test_save_message_missing_conversation_raises(fake_conn, monkeypatch):
    fake_conn(monkeypatch, history, fetchone_results=[])  # existence check → None
    with pytest.raises(KeyError):
        history.save_message("ghost", "m1", "user", "hi")


def test_merge_tool_result_preserves_siblings(fake_conn, monkeypatch):
    existing = [{"tool_use_id": "t1", "tool_name": "r", "content": "{}"}]
    conn = fake_conn(monkeypatch, history, fetchone_results=[(existing,)])
    history.merge_tool_result("m1", "t2", "crm_create_contact", '{"ok": true}')
    upd = next(p for s, p in conn.executed if "UPDATE assistant_messages SET tool_results" in s)
    written = json.loads(upd[0])
    ids = {r["tool_use_id"] for r in written}
    assert ids == {"t1", "t2"}  # sibling t1 preserved, t2 added
    assert "FOR UPDATE" in " || ".join(s for s, _ in conn.executed)


def test_merge_tool_result_replaces_same_id(fake_conn, monkeypatch):
    existing = [{"tool_use_id": "t1", "tool_name": "x", "content": history.PENDING_RESULT_JSON}]
    conn = fake_conn(monkeypatch, history, fetchone_results=[(existing,)])
    history.merge_tool_result("m1", "t1", "x", '{"done": true}')
    upd = next(p for s, p in conn.executed if "UPDATE assistant_messages SET tool_results" in s)
    written = json.loads(upd[0])
    assert len(written) == 1 and written[0]["content"] == '{"done": true}'


def test_merge_tool_result_missing_row_raises(fake_conn, monkeypatch):
    fake_conn(monkeypatch, history, fetchone_results=[])  # row lookup → None
    with pytest.raises(KeyError):
        history.merge_tool_result("gone", "t1", "x", "{}")


def test_claim_pending_tool_by_msg_id_marks_executing(fake_conn, monkeypatch):
    """The msg_id path (used by /confirm) locks the exact row, verifies it's pending,
    and returns the DB-canonical tool/args. Patches row_to_dict since FakeCursor
    has no .description."""
    calls = [{"tool": "crm_create_contact", "tool_use_id": "t1", "args": {"n": 1}}]
    results = [{"tool_use_id": "t1", "tool_name": "crm_create_contact", "content": history.PENDING_RESULT_JSON}]
    conn = fake_conn(monkeypatch, history, fetchone_results=[(calls, results)])
    monkeypatch.setattr(history, "row_to_dict", lambda cur, row: {"tool_calls": row[0], "tool_results": row[1]})
    out = history.claim_pending_tool("c1", "t1", msg_id="m1")
    assert out == {
        "msg_id": "m1",
        "tool": "crm_create_contact",
        "args": {"n": 1},
        # The pre-claim placeholder, so the resolver can read anything the gate
        # bound to it (the Gmail connection binding, #43).
        "content": history.PENDING_RESULT_JSON,
    }
    # scoped the lock to the row AND conversation, and marked the result executing
    sqls = " || ".join(s for s, _ in conn.executed)
    assert "WHERE id = %s AND conversation_id = %s FOR UPDATE" in sqls
    upd = next(p for s, p in conn.executed if "UPDATE assistant_messages SET tool_results" in s)
    assert history.EXECUTING_STATUS in upd[0]


def test_claim_pending_tool_returns_none_when_not_pending(fake_conn, monkeypatch):
    calls = [{"tool": "crm_create_contact", "tool_use_id": "t1", "args": {}}]
    results = [{"tool_use_id": "t1", "tool_name": "crm_create_contact", "content": '{"ok": true}'}]  # already resolved
    fake_conn(monkeypatch, history, fetchone_results=[(calls, results)])
    monkeypatch.setattr(history, "row_to_dict", lambda cur, row: {"tool_calls": row[0], "tool_results": row[1]})
    assert history.claim_pending_tool("c1", "t1", msg_id="m1") is None


# ── Compaction state (issue #72 Phase 3) ──────────────────────────────────────

def test_set_compaction_only_ever_moves_the_boundary_forward(rec):
    """A compare-and-set, not a plain write: two turns racing on one conversation must
    not be able to rewind the boundary or pair a newer summary with an older one."""
    history.set_compaction("c1", "gist", 42, False)
    sql = rec.sql_with("compaction_summary =")
    assert "compaction_first_kept_seq IS NULL OR compaction_first_kept_seq < %s" in sql
    assert rec.params_with("compaction_summary =") == ["gist", 42, False, "c1", 42]


def test_set_compaction_ors_the_taint_rather_than_assigning_it(rec):
    """The flag records that untrusted content ONCE entered the thread; a later
    compaction of a clean span must not clear it."""
    history.set_compaction("c1", "gist", 42, False)
    assert "untrusted_content_seen = untrusted_content_seen OR %s" in rec.sql_with("untrusted_content_seen")


def test_set_compaction_reports_whether_it_won(rec):
    rec.rowcount = 0
    assert history.set_compaction("c1", "gist", 42, False) is False
    rec.rowcount = 1
    assert history.set_compaction("c1", "gist", 43, False) is True


def test_set_compaction_leaves_updated_at_alone(rec):
    """It orders the conversation list as a record of USER activity, and compaction is
    internal housekeeping inside a turn whose own message write already bumps it."""
    history.set_compaction("c1", "gist", 42, False)
    assert "updated_at" not in rec.sql_with("compaction_summary =")


def test_mark_untrusted_seen_sets_the_flag_unconditionally(rec):
    history.mark_untrusted_seen("c1")
    assert "untrusted_content_seen = TRUE" in rec.sql_with("untrusted_content_seen")


def test_is_conversation_tainted_fails_closed_on_a_read_error(monkeypatch):
    """An unreadable flag costs a confirmation prompt; answering False on a database
    blip costs the power->normal mitigation itself."""
    def _boom(sql, params=()):
        raise RuntimeError("connection reset")
    monkeypatch.setattr(history, "pg_fetchone", _boom)
    assert history.is_conversation_tainted("c1") is True


def test_is_conversation_tainted_fails_closed_on_a_missing_conversation(rec):
    assert history.is_conversation_tainted("ghost") is True


def test_is_conversation_tainted_reads_the_stored_flag(rec):
    rec.fetchone_queue = [{"untrusted_content_seen": False}]
    assert history.is_conversation_tainted("c1") is False
    rec.fetchone_queue = [{"untrusted_content_seen": True}]
    assert history.is_conversation_tainted("c1") is True


def test_get_compaction_state_answers_every_question_in_one_read(rec):
    """The fast path is only affordable because a thread nowhere near the threshold
    costs one indexed lookup and no message scan."""
    rec.fetchone_queue = [{"summary": None, "first_kept_seq": None,
                           "tainted": False, "last_context_tokens": 1234}]
    state = history.get_compaction_state("c1")
    assert state["last_context_tokens"] == 1234
    assert len([c for c in rec.calls if "assistant_conversations" in c[0]]) == 1


def test_save_message_records_the_context_reading_on_the_row_it_already_writes(fake_conn, monkeypatch):
    """Zero extra round trips on the streaming hot path: it rides the UPDATE the write
    was making anyway. COALESCE, so a provider that reports no usage never blanks a
    good reading from an earlier turn."""
    conn = fake_conn(monkeypatch, history, fetchone_results=[("c1",), (0,)])
    history.save_message("c1", "m1", "assistant", "text", context_tokens=4096)
    update = next(
        (s, p) for s, p in conn.executed if "UPDATE assistant_conversations" in s
    )
    assert "GREATEST(%s, COALESCE(last_context_tokens, 0))" in " ".join(update[0].split())
    assert update[1][0] == 4096
    # ONE transaction: the message insert and the reading ride the same connection block.
    assert conn.entries == 1


def test_save_message_without_a_reading_leaves_the_stored_one_alone(fake_conn, monkeypatch):
    conn = fake_conn(monkeypatch, history, fetchone_results=[("c1",), (0,)])
    history.save_message("c1", "m1", "user", "hi")
    update = next((s, p) for s, p in conn.executed if "UPDATE assistant_conversations" in s)
    sql = " ".join(update[0].split())
    assert "CASE WHEN %s IS NULL THEN last_context_tokens" in sql
    assert update[1][0] is None


def test_save_message_never_lowers_the_meter_between_compactions(fake_conn, monkeypatch):
    """Two turns racing on one conversation can finish out of order, and the slow one's
    context was assembled before the other's rows existed. Landing last, its stale low
    reading would send the NEXT turn down the fast path and skip compaction — and if the
    real context is already past the provider's limit every turn fails, none records a
    corrective reading, and the thread stays stuck."""
    conn = fake_conn(monkeypatch, history, fetchone_results=[("c1",), (0,)])
    history.save_message("c1", "m1", "assistant", "text", context_tokens=100)
    sql = " ".join(next(s for s, _ in conn.executed if "UPDATE assistant_conversations" in s).split())
    assert "GREATEST" in sql, "a stale low reading must not replace a high one"


def test_save_message_drops_a_reading_produced_under_an_older_boundary(fake_conn, monkeypatch):
    """Clearing the meter settles the stored value but cannot reach a turn already in
    flight. Turn A assembles a 150k context, turn B compacts and NULLs the meter, then A
    lands and GREATEST restores 150k — a number describing rows that are no longer
    assembled. Each reading therefore carries the boundary it was produced under, and
    `compaction_first_kept_seq` is that version: set_compaction only ever moves it
    forward, so it needs no column of its own.

    Shape only — that this really rejects the stale write is proved against real
    Postgres in test_integration_compaction_pg.py."""
    conn = fake_conn(monkeypatch, history, fetchone_results=[("c1",), (0,)])
    history.save_message("c1", "m1", "assistant", "t",
                         context_tokens=150_000, context_boundary_seq=4)
    sql, params = next(
        (s, p) for s, p in conn.executed if "UPDATE assistant_conversations" in s
    )
    flat = " ".join(sql.split())
    assert "WHEN compaction_first_kept_seq IS DISTINCT FROM %s THEN last_context_tokens" in flat
    # IS DISTINCT FROM, not `=`: a caller still reporting None once a boundary exists is
    # by definition working from the older view, and `= NULL` is never true.
    assert tuple(params[:2]) == (150_000, 4)


def test_set_compaction_clears_the_meter(fake_conn, rec):
    """The one moment a decrease is real — which is what lets save_message otherwise
    keep the greater of the two. The next turn pays one row scan for a fresh reading."""
    history.set_compaction("c1", "gist", 42, False)
    assert "last_context_tokens = NULL" in rec.sql_with("compaction_summary =")


# ── Observer watermark (#72 Phase 4) ──────────────────────────────────────────
#
# These pin the SQL SHAPE only, which is all a hermetic test can honestly claim.
# Whether the candidate predicate actually selects the right conversations is proved
# against a real server in tests/test_integration_observer_pg.py — a substring assertion
# cannot establish selection behaviour.

def test_candidate_query_counts_only_new_user_rows(rec):
    rec.fetchall_queue.append([])
    history.list_observer_candidates(10, 2, 5)
    sql = rec.sql_with("new_user_rows")
    assert "count(*) FILTER ( WHERE role = 'user' AND seq > COALESCE(c.observed_through_seq, -1) )" in sql


def test_candidate_query_measures_quietness_over_every_message(rec):
    """max(created_at) is NOT filtered to user rows. A newer ASSISTANT row means the turn
    is still in flight, and observing mid-turn is the one moment a commitment has not
    settled."""
    rec.fetchall_queue.append([])
    history.list_observer_candidates(10, 2, 5)
    sql = rec.sql_with("newest_at")
    filtered, _, rest = sql.partition("max(created_at) AS newest_at")
    assert "FILTER" not in rest.split("FROM assistant_messages")[0]
    assert "m.newest_at <= now() - make_interval(mins => %s)" in sql


def test_candidate_query_orders_oldest_first_with_an_id_tiebreak(rec):
    rec.fetchall_queue.append([])
    history.list_observer_candidates(10, 2, 5)
    assert "ORDER BY m.newest_at ASC, c.id ASC" in rec.sql_with("new_user_rows")


def test_candidate_query_coalesces_a_null_watermark_for_the_caller(rec):
    rec.fetchall_queue.append([])
    history.list_observer_candidates(10, 2, 5)
    assert "COALESCE(c.observed_through_seq, -1) AS observed_through_seq" in rec.sql_with("new_user_rows")


def test_candidate_query_clamps_its_arguments(rec):
    rec.fetchall_queue.append([])
    history.list_observer_candidates(-5, 0, 0)
    assert rec.params_with("new_user_rows") == [1, 0, 1]


def test_user_rows_since_is_user_only_and_seq_bounded(rec):
    rec.fetchall_queue.append([])
    history.user_rows_since("c1", 4, 200)
    sql = rec.sql_with("FROM assistant_messages")
    assert "role = 'user'" in sql and "seq > %s" in sql and "ORDER BY seq" in sql
    assert rec.params_with("FROM assistant_messages") == ["c1", 4, 200]


def test_advance_observed_seq_only_moves_forward(rec):
    assert history.advance_observed_seq("c1", 9) is True
    sql = rec.sql_with("observed_through_seq = %s")
    assert "COALESCE(observed_through_seq, -1) < %s" in sql
    assert rec.params_with("observed_through_seq = %s") == [9, "c1", 9]


def test_advance_observed_seq_reports_a_no_op(rec):
    rec.rowcount = 0
    assert history.advance_observed_seq("c1", 9) is False


def test_advance_observed_seq_does_not_bump_updated_at(rec):
    """updated_at orders the user's conversation list as a record of THEIR activity;
    housekeeping must not make every observed thread look freshly active."""
    history.advance_observed_seq("c1", 9)
    assert "updated_at" not in rec.sql_with("observed_through_seq = %s")
