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
