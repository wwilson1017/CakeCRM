"""Hermetic CRM service tests — SQL shape + behavior.

The service imports the pg helpers by name, so we monkeypatch
``crm.service.pg_fetchone/pg_fetchall/pg_execute`` with a recorder (FakeCursor
can't feed ``row_to_dict``, which needs ``cursor.description``). Multi-statement
writes go through ``get_connection`` and use the shared ``fake_conn`` fixture.
"""

import pytest

from crm import scoring_service, service


class Recorder:
    """Records (normalized_sql, params) for every helper call; returns queued rows."""

    def __init__(self):
        self.calls: list[tuple[str, list]] = []
        self.fetchone_queue: list = []
        self.fetchall_queue: list = []
        self.execute_rowcount = 1

    def fetchone(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), list(params)))
        return self.fetchone_queue.pop(0) if self.fetchone_queue else None

    def fetchall(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), list(params)))
        return self.fetchall_queue.pop(0) if self.fetchall_queue else []

    def execute(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), list(params)))
        return self.execute_rowcount

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


class _RecCursor:
    """A cursor that records into the same Recorder, so a write that moved inside a
    `with get_connection()` block (#239: the audited contact/company/deal archive writes)
    is still visible to `sql_containing`/`params_for`. `fetchone` pops
    `Recorder.cursor_fetchone_queue`, else answers the locked status pre-read with a live
    record — the shape every pre-#239 update test assumed."""

    def __init__(self, r: Recorder):
        self.r = r
        self._last = ""

    def execute(self, sql, params=()):
        self._last = sql
        self.r.calls.append((" ".join(sql.split()), list(params)))

    def fetchone(self):
        if self.r.cursor_fetchone_queue:
            return self.r.cursor_fetchone_queue.pop(0)
        if "SELECT status FROM" in self._last:
            return ("active",)
        return None


@pytest.fixture
def rec(monkeypatch):
    from contextlib import contextmanager

    r = Recorder()
    r.cursor_fetchone_queue = []

    class _Conn:
        def cursor(self):
            return _RecCursor(r)

    @contextmanager
    def _get_connection():
        yield _Conn()

    monkeypatch.setattr(service, "pg_fetchone", r.fetchone)
    monkeypatch.setattr(service, "pg_fetchall", r.fetchall)
    monkeypatch.setattr(service, "pg_execute", r.execute)
    monkeypatch.setattr(service, "get_connection", _get_connection)
    return r


# Columns the todo UPDATE ... RETURNING * produces, in migration order. The spawn step
# reads them through the REAL row_to_dict, so the described cursor below must supply a
# matching `description` — a cursor returning bare tuples (or a monkeypatched
# row_to_dict) would hide a description-reuse bug entirely.
_TODO_COLS = [
    "id", "contact_id", "deal_id", "title", "description", "due_date", "completed",
    "priority", "created_at", "updated_at", "status", "star", "context", "tags",
    "repeat", "auto_star_on_due", "project_id", "completed_at", "source",
]


class _TodoCursor:
    """Cursor that mimics psycopg2's per-execute ``cursor.description``.

    steps are driven by the statement: the first SELECT ... FOR UPDATE yields the prior
    status (or nothing, for a missing row); the UPDATE ... RETURNING * yields a full
    todo row so the recurrence spawn can read it.
    """

    def __init__(self, prior_status, returning_row=None):
        self._prior = prior_status
        self._returning = returning_row
        self.description = None
        self._rows: list = []
        self.executed: list = []

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


@pytest.fixture
def todo_txn():
    """Install a described cursor on service.get_connection for the todo write path."""
    from contextlib import contextmanager

    def _install(monkeypatch, *, prior_status, returning_row=None):
        if returning_row is None:
            returning_row = tuple(
                {"id": 1, "title": "T", "repeat": "", "tags": "[]"}.get(c) for c in _TODO_COLS
            )
        cur = _TodoCursor(prior_status, returning_row)

        class _Conn:
            def cursor(self):
                return cur

        @contextmanager
        def _get_connection():
            yield _Conn()

        monkeypatch.setattr(service, "get_connection", _get_connection)
        return cur

    return _install


# ── Contacts ──────────────────────────────────────────────────────────────────

def test_create_contact_insert_returning_and_tag_normalization(rec):
    rec.fetchone_queue = [{"id": 7}, {"id": 7, "name": "Ana"}]
    result = service.create_contact("Ana", tags=" PT, ET ,MT ")
    insert_sql = rec.sql_containing("INSERT INTO contacts")
    assert "RETURNING id" in insert_sql
    assert "%s" in insert_sql and "?" not in insert_sql
    # tags normalized (whitespace stripped, empties dropped) in the insert params
    assert "PT,ET,MT" in rec.params_for("INSERT INTO contacts")
    # hydrated via a follow-up SELECT by the returned id (joined for company_name)
    assert rec.params_for("FROM contacts ct LEFT JOIN companies") == [7]
    assert result == {"id": 7, "name": "Ana"}


def test_search_contacts_ilike_and_tag_boundary(rec):
    service.search_contacts("acme", status="active", tags="vip, lead", limit=15)
    sql = rec.sql_containing("FROM contacts ct")
    # name/email/company/co.name/notes + tag clauses (issue #35 added the join term)
    assert sql.count("ILIKE") >= 5
    # No case-sensitive LIKE among the SEARCH terms. The #77 last-contact join excludes
    # housekeeping notes with a rendered `starts_with` predicate (#239), not a LIKE.
    assert "LIKE %s" not in sql.replace("ILIKE %s", "")
    assert scoring_service.not_housekeeping_sql("ch.message") in sql
    assert "ct.status = %s" in sql
    assert "LEFT JOIN companies co ON ct.company_id = co.id" in sql
    assert "co.name ILIKE %s" in sql  # linked contacts findable by company name
    params = rec.params_for("FROM contacts ct")
    # The last-contact LATERAL carries no placeholder since #239, so the WHERE's
    # parameters lead.
    assert params[0:5] == ["%acme%"] * 5
    assert "active" in params
    assert "%,vip,%" in params and "%,lead,%" in params
    assert params[-2:] == [15, 0]  # LIMIT %s OFFSET %s (default offset 0)


def test_list_contacts_count_alias_and_sort_whitelist(rec):
    rec.fetchone_queue = [{"cnt": 3}]
    rec.fetchall_queue = [[{"id": 1}]]
    out = service.list_contacts(offset=10, limit=5, sort="bogus")
    count_sql = rec.sql_containing("COUNT(*)")
    assert "COUNT(*) AS cnt" in count_sql
    # The count's WHERE never references companies, so it carries no join.
    assert "LEFT JOIN" not in count_sql
    # unknown sort falls back to updated_at
    assert "ORDER BY ct.updated_at DESC" in rec.sql_containing("ORDER BY")
    # the rows query joins and exposes the authoritative company name (issue #35)
    rows_sql = rec.sql_containing("LIMIT %s OFFSET %s")
    assert "SELECT ct.*, co.name AS company_name" in rows_sql
    assert "LEFT JOIN companies co ON ct.company_id = co.id" in rows_sql
    assert rec.params_for("LIMIT %s OFFSET %s")[-2:] == [5, 10]
    assert out == {"contacts": [{"id": 1}], "total": 3, "limit": 5, "offset": 10}


def test_update_contact_appends_updated_at_and_filters_unknown(rec):
    rec.fetchone_queue = [{"id": 4, "name": "New"}]
    service.update_contact(4, name="New", bogus="x")
    sql = rec.sql_containing("UPDATE contacts SET")
    assert "name = %s" in sql and "bogus" not in sql
    assert "updated_at = %s WHERE id = %s" in sql
    params = rec.params_for("UPDATE contacts SET")
    assert params[0] == "New"
    assert params[-1] == 4  # id last, after the _now() timestamp


def test_update_contact_empty_returns_getter_without_update(rec):
    rec.fetchone_queue = [{"id": 4}]
    service.update_contact(4)  # no fields
    assert not any("UPDATE contacts" in sql for sql, _ in rec.calls)


def test_update_deal_invalid_stage_returns_none(rec):
    assert service.update_deal(1, stage="not-a-stage") is None
    assert not any("UPDATE deals" in sql for sql, _ in rec.calls)


def test_update_deal_stage_invalid_returns_none(rec):
    assert service.update_deal_stage(1, "bogus") is None


# ── Todos ─────────────────────────────────────────────────────────────────────

def test_list_todos_completed_as_int_and_due_before_guard(rec):
    service.list_todos(completed=False, due_before="2026-01-01")
    sql = rec.sql_containing("FROM todos")
    assert "t.completed = %s" in sql
    assert "t.due_date != '' AND t.due_date <= %s" in sql
    params = rec.params_for("FROM todos")
    assert 0 in params  # completed=False → int 0
    assert "2026-01-01" in params


def test_update_todo_writes_completed_and_status_together(monkeypatch, rec, todo_txn):
    """`completed` and `status` are two views of one fact, bound by a CHECK constraint
    since #70 — a stray truthy value must still normalize to exactly 1, and the paired
    status must be written in the SAME statement."""
    cur = todo_txn(monkeypatch, prior_status="next_action")
    rec.fetchone_queue = [{"id": 1, "completed": 1}]
    service.update_todo(1, completed=2)  # stray truthy value must normalize to 1
    sql, params = next((s, p) for s, p in cur.executed if "UPDATE todos SET" in s)
    assert "status = %s" in sql and "completed = %s" in sql
    assert "done" in params and 1 in params and 2 not in params


def test_update_todo_uncompleting_a_done_todo_reopens_it(monkeypatch, rec, todo_txn):
    cur = todo_txn(monkeypatch, prior_status="done")
    rec.fetchone_queue = [{"id": 1}]
    service.update_todo(1, completed=False)
    sql, params = next((s, p) for s, p in cur.executed if "UPDATE todos SET" in s)
    assert "next_action" in params and 0 in params
    assert "completed_at = NULL" in sql


def test_complete_todo_locks_the_row_and_none_when_missing(monkeypatch, rec, todo_txn):
    cur = todo_txn(monkeypatch, prior_status=None)  # row absent
    assert service.complete_todo(999) is None
    assert any("SELECT status FROM todos WHERE id = %s FOR UPDATE" in s
               for s, _ in cur.executed)
    assert not any("UPDATE todos SET" in s for s, _ in cur.executed)


def test_delete_todo_rowcount(rec):
    rec.execute_rowcount = 0
    assert service.delete_todo(5) is False
    rec.execute_rowcount = 1
    assert service.delete_todo(5) is True


def test_delete_activity_returns_true_and_rescores_links(rec):
    # #18: delete_activity now DELETE ... RETURNING the links so the affected deal/contact
    # can be rescored (their interaction count/recency changed).
    rec.fetchone_queue = [{"contact_id": 5, "deal_id": 7}]
    assert service.delete_activity(3) is True
    assert "RETURNING contact_id, deal_id" in rec.sql_containing("DELETE FROM activity_log")


def test_delete_activity_missing_returns_false(rec):
    rec.fetchone_queue = [None]  # RETURNING found nothing
    assert service.delete_activity(999) is False


# ── Dashboard overdue: today-date TEXT compare, no ::date cast ─────────────────

def test_dashboard_overdue_is_text_date_compare(rec):
    service.get_dashboard_stats()
    overdue_sql = rec.sql_containing("due_date < %s")
    assert "completed = 0 AND due_date != '' AND due_date < %s" in overdue_sql
    assert "::date" not in overdue_sql  # never cast — a malformed row must not 500
    # param is today's YYYY-MM-DD (10 chars, dash-separated)
    params = rec.params_for("due_date < %s")
    assert len(params[0]) == 10 and params[0].count("-") == 2


# ── Multi-statement writes go through get_connection ──────────────────────────

def test_delete_contact_existence_check_and_cascade_one_txn(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, service, fetchone_results=[(42,)])
    assert service.delete_contact(42) is True
    stmts = [sql for sql, _ in conn.executed]
    assert any("SELECT id FROM contacts WHERE id" in s for s in stmts)
    # activity_log, todos, crm_chatter, crm_field_values, crm_field_provenance, contacts
    assert sum("DELETE FROM" in s for s in stmts) == 6
    assert any("DELETE FROM crm_chatter WHERE entity_type = 'contact'" in s for s in stmts)
    assert any("DELETE FROM crm_field_values WHERE entity_type = 'contact'" in s for s in stmts)
    assert any("DELETE FROM crm_field_provenance WHERE entity_type = 'contact'" in s for s in stmts)
    assert "DELETE FROM contacts WHERE id" in stmts[-1]


def test_delete_contact_missing_returns_false_no_deletes(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, service)  # existence SELECT → None
    assert service.delete_contact(99) is False
    assert not any("DELETE FROM" in s for s, _ in conn.executed)


def test_clear_demo_data_guarded_when_not_sample(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, service, fetchone_results=[(False,)])
    out = service.clear_demo_data()
    assert out == {"ok": True, "cleared": False}
    assert not any("TRUNCATE" in s for s, _ in conn.executed)


def test_clear_demo_data_truncates_when_sample_loaded(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, service, fetchone_results=[(True,)])
    out = service.clear_demo_data()
    assert out == {"ok": True, "cleared": True}
    stmts = [s for s, _ in conn.executed]
    # Base truncate: crm_field_values and crm_field_provenance ARE wiped, but
    # crm_field_definitions is NOT — demo-clear preserves the user's custom-field
    # schema (only clear_all wipes it). proactive_nudges trails: no FK, so nothing
    # cascades it, and a stale cooldown would silence nudges on the reseeded data.
    # deal_ai_touch_evidence (#56) trails it for the same FK-less reason — a reused deal
    # id would otherwise inherit a deleted deal's per-event explanation.
    # crm_chatter_attachments (#57) sits right after crm_chatter and is MANDATORY, not
    # tidy: it holds a real FK to crm_chatter, and Postgres refuses to truncate a
    # referenced table without its child in the same statement — dropping it here makes
    # every CRM reset raise.
    assert any(
        "TRUNCATE companies, contacts, deals, activity_log, todos, todo_projects, "
        "crm_chatter, crm_chatter_attachments, crm_chatter_mentions, crm_field_values, "
        "crm_field_provenance, deal_stage_events, proactive_nudges, deal_ai_touch_evidence, "
        "crm_stage_criteria RESTART IDENTITY"
        in s for s in stmts
    )
    assert not any("crm_field_definitions" in s for s in stmts)


def test_load_sample_data_noop_when_not_empty(monkeypatch, fake_conn):
    # The FOR UPDATE lock does not fetch; the all-tables count is the only
    # fetchone() and returns non-zero → seeding is skipped.
    conn = fake_conn(monkeypatch, service, fetchone_results=[(5,)])
    out = service.load_sample_data()
    assert out == {"ok": True, "seeded": False}
    assert not any("INSERT INTO contacts" in s for s, _ in conn.executed)


def test_load_sample_data_seeds_and_flips_meta_when_empty(monkeypatch, fake_conn):
    # Two count fetches read (0,): load_sample_data's empty-check and
    # seed_demo_data's own idempotence guard → seeds, then flips the meta flags.
    conn = fake_conn(monkeypatch, service, fetchone_results=[(0,), (0,)])
    out = service.load_sample_data()
    assert out == {"ok": True, "seeded": True}
    stmts = [s for s, _ in conn.executed]
    assert any("INSERT INTO contacts" in s for s in stmts)  # seed ran
    assert any("sample_data_loaded = TRUE, onboarding_dismissed = TRUE" in s for s in stmts)


def test_clear_all_truncates_and_resets_flag(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, service)
    assert service.clear_all() == {"ok": True}
    stmts = [s for s, _ in conn.executed]
    # clear_all is the deliberate full reset: it ALSO wipes crm_field_definitions,
    # placed after the entity tables but before crm_field_values (lock order
    # consistent with both set_field_values entity→defs and delete_field_definition
    # defs→values); crm_field_provenance trails both.
    assert any(
        "TRUNCATE companies, contacts, deals, activity_log, todos, todo_projects, "
        "crm_chatter, crm_chatter_attachments, crm_chatter_mentions, crm_field_definitions, "
        "crm_field_values, crm_field_provenance, deal_stage_events, proactive_nudges, "
        "deal_ai_touch_evidence, crm_stage_criteria RESTART IDENTITY"
        in s for s in stmts
    )
    assert any("sample_data_loaded = FALSE" in s for s in stmts)


def test_is_crm_empty_counts_field_values_not_definitions(rec):
    # Decision 9: a stray custom-field VALUE keeps the CRM "non-empty" (orphan-id
    # safety), but a bare field DEFINITION must NOT suppress the first-run seed prompt.
    rec.fetchone_queue = [{"total": 0}]
    assert service.is_crm_empty() is True
    sql = rec.sql_containing("crm_field_values")
    assert "crm_field_definitions" not in sql


# ── AI-key nudge dismissal (issue #9) ─────────────────────────────────────────

def test_dismiss_ai_prompt_sets_flag(rec):
    # Single-statement blind write via pg_execute (the rec fixture patches it).
    assert service.dismiss_ai_prompt() == {"ok": True}
    assert "ai_key_prompt_dismissed = TRUE" in rec.sql_containing("ai_key_prompt_dismissed")


def test_demo_status_includes_ai_prompt_flag(rec):
    # get_demo_status → get_crm_meta (1st fetchone) then is_crm_empty count (2nd).
    rec.fetchone_queue = [
        {"id": 1, "sample_data_loaded": False, "onboarding_dismissed": False,
         "ai_key_prompt_dismissed": True},
        {"total": 5},
    ]
    body = service.get_demo_status()
    assert body["ai_key_prompt_dismissed"] is True


def test_demo_status_ai_prompt_flag_defaults_false_when_absent(rec):
    # Pre-migration/None row shape: the key is absent → bool(get(...)) must be False,
    # NOT a KeyError (the model_dump/get-returns-None footgun).
    rec.fetchone_queue = [
        {"id": 1, "sample_data_loaded": False, "onboarding_dismissed": False},
        {"total": 5},
    ]
    body = service.get_demo_status()
    assert body["ai_key_prompt_dismissed"] is False


# ── create_deal coerces an unknown stage (2 reviewers) ────────────────────────

def test_create_deal_coerces_unknown_stage_to_lead(rec):
    rec.fetchone_queue = [{"id": 3}, {"id": 3, "stage": "lead"}]
    service.create_deal("Big deal", stage="not-a-real-stage")
    # the INSERT binds 'lead', not the bogus stage → deal stays visible in the pipeline
    assert "lead" in rec.params_for("INSERT INTO deals")
    assert "not-a-real-stage" not in rec.params_for("INSERT INTO deals")


def test_create_deal_keeps_valid_stage(rec):
    rec.fetchone_queue = [{"id": 4}, {"id": 4, "stage": "proposal"}]
    service.create_deal("Deal", stage="proposal")
    assert "proposal" in rec.params_for("INSERT INTO deals")


def test_create_deal_clamps_probability(rec):
    rec.fetchone_queue = [{"id": 5}, {"id": 5}]
    service.create_deal("Deal", probability=500)
    assert 100 in rec.params_for("INSERT INTO deals") and 500 not in rec.params_for("INSERT INTO deals")


# ── enum coercion (mirrors the create_deal stage fix; protects the tool surface) ──

def test_create_contact_coerces_unknown_status(rec):
    rec.fetchone_queue = [{"id": 1}, {"id": 1}]
    service.create_contact("Ana", status="prospect")  # not a real status
    assert "active" in rec.params_for("INSERT INTO contacts")
    assert "prospect" not in rec.params_for("INSERT INTO contacts")


def test_update_contact_coerces_unknown_status(rec):
    rec.fetchone_queue = [{"id": 1}]
    service.update_contact(1, status="prospect")
    sql = rec.sql_containing("UPDATE contacts SET")
    assert "status = %s" in sql
    assert "active" in rec.params_for("UPDATE contacts SET")


def test_create_todo_coerces_unknown_priority(rec):
    rec.fetchone_queue = [{"id": 1}, {"id": 1}]
    service.create_todo("T", priority="urgent")  # not a real priority
    assert "medium" in rec.params_for("INSERT INTO todos")
    assert "urgent" not in rec.params_for("INSERT INTO todos")


# ── search pagination: offset + accurate total ────────────────────────────────

def test_search_contacts_forwards_offset(rec):
    service.search_contacts("acme", limit=50, offset=100)
    sql = rec.sql_containing("FROM contacts ct")
    assert "LIMIT %s OFFSET %s" in sql
    assert rec.params_for("FROM contacts ct")[-2:] == [50, 100]


def test_count_search_contacts_uses_count_and_same_where(rec):
    rec.fetchone_queue = [{"cnt": 42}]
    assert service.count_search_contacts("acme", status="active") == 42
    sql = rec.sql_containing("COUNT(*) AS cnt")
    assert "ILIKE" in sql and "ct.status = %s" in sql
    # the WHERE references co.name, so the count must carry the join too; a LEFT
    # JOIN on the companies PK can't multiply rows, so this stays a contact count
    assert "LEFT JOIN companies co ON ct.company_id = co.id" in sql
    assert "LIMIT" not in sql  # count has no pagination


# ── Companies (issue #13) ─────────────────────────────────────────────────────

def test_create_company_insert_returning_and_name_trim(rec):
    rec.fetchone_queue = [{"id": 9}, {"id": 9, "name": "Acme"}]
    result = service.create_company("  Acme  ", industry="Tech")
    insert_sql = rec.sql_containing("INSERT INTO companies")
    assert "RETURNING id" in insert_sql
    assert "%s" in insert_sql and "?" not in insert_sql
    params = rec.params_for("INSERT INTO companies")
    assert params[0] == "Acme"  # name stripped
    assert "Tech" in params
    assert rec.params_for("SELECT * FROM companies WHERE id = %s") == [9]
    assert result == {"id": 9, "name": "Acme"}


def test_create_company_coerces_unknown_status(rec):
    rec.fetchone_queue = [{"id": 1}, {"id": 1}]
    service.create_company("Acme", status="bogus")
    assert "active" in rec.params_for("INSERT INTO companies")


def test_create_company_trims_only_ascii_whitespace(rec):
    # Matches the migration's btrim(x, E' \\t\\n\\r\\f\\x0b') so backfill + service
    # agree: the six ASCII whitespace bytes are trimmed; a non-ASCII NBSP is
    # preserved verbatim (a fixed byte set, deterministic across libc/locale).
    rec.fetchone_queue = [{"id": 1}, {"id": 1}]
    service.create_company("  Acme  ")
    assert rec.params_for("INSERT INTO companies")[0] == "Acme"
    rec.calls.clear()
    rec.fetchone_queue = [{"id": 2}, {"id": 2}]
    service.create_company("\u00a0Acme\u00a0")  # NBSP-wrapped: preserved, not stripped
    assert rec.params_for("INSERT INTO companies")[0] == "\u00a0Acme\u00a0"


def test_list_companies_count_alias_and_sort_whitelist(rec):
    rec.fetchone_queue = [{"cnt": 3}]
    rec.fetchall_queue = [[{"id": 1, "name": "Acme"}]]
    out = service.list_companies(limit=10, offset=5, sort="bogus")
    assert out["total"] == 3
    list_sql = rec.sql_containing("SELECT * FROM companies")
    # unknown sort falls back to name ASC, with an id tie-breaker for stable paging
    assert "ORDER BY name ASC, id ASC" in list_sql
    assert rec.params_for("SELECT * FROM companies")[-2:] == [10, 5]


def test_list_companies_timestamp_sort_is_desc(rec):
    rec.fetchone_queue = [{"cnt": 0}]
    rec.fetchall_queue = [[]]
    service.list_companies(sort="updated_at")
    assert "ORDER BY updated_at DESC, id DESC" in rec.sql_containing("SELECT * FROM companies")


def test_search_companies_four_ilike_and_pagination(rec):
    service.search_companies("acme", limit=20, offset=40)
    sql = rec.sql_containing("FROM companies WHERE")
    assert sql.count("ILIKE") == 4  # name, domain, industry, notes
    assert "LIMIT %s OFFSET %s" in sql
    assert rec.params_for("FROM companies WHERE")[-2:] == [20, 40]


def test_count_search_companies_no_pagination(rec):
    rec.fetchone_queue = [{"cnt": 7}]
    assert service.count_search_companies("acme") == 7
    sql = rec.sql_containing("COUNT(*) AS cnt")
    assert "FROM companies" in sql and "LIMIT" not in sql


def test_update_company_appends_updated_at_and_filters_unknown(rec):
    rec.fetchone_queue = [{"id": 1, "name": "Acme"}]
    service.update_company(1, name="  Acme  ", bogus="x")
    sql = rec.sql_containing("UPDATE companies SET")
    assert "updated_at = %s" in sql and "bogus" not in sql
    params = rec.params_for("UPDATE companies SET")
    assert "Acme" in params  # trimmed
    assert params[-1] == 1  # id last


def test_delete_company_cascades_field_values_in_one_txn(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, service, fetchone_results=[(5,)])
    assert service.delete_company(5) is True
    stmts = [s for s, _ in conn.executed]
    assert any("SELECT id FROM companies WHERE id" in s and "FOR UPDATE" in s for s in stmts)
    assert any("DELETE FROM crm_field_values WHERE entity_type = 'company'" in s for s in stmts)
    assert "DELETE FROM companies WHERE id" in stmts[-1]
    # Global schema is never touched by a per-entity delete cascade.
    assert not any("crm_field_definitions" in s for s in stmts)


def test_delete_company_missing_returns_false_no_deletes(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, service)  # existence SELECT → None
    assert service.delete_company(5) is False
    assert not any("DELETE FROM" in s for s, _ in conn.executed)


def test_get_company_detail_rolls_up_activity_and_open_value(rec):
    rec.fetchone_queue = [{"id": 1, "name": "Acme"}]  # get_company
    rec.fetchall_queue = [
        [{"id": 10, "name": "Contact"}],  # contacts
        [{"id": 20, "stage": "won", "value": 5000}, {"id": 21, "stage": "lead", "value": 300}],  # deals
        [{"id": 30, "activity": "call"}],  # activity
    ]
    out = service.get_company_detail(1)
    # activity rollup joins through the company's contacts AND deals
    act_sql = rec.sql_containing("FROM activity_log")
    assert "IN (SELECT id FROM contacts WHERE company_id = %s)" in act_sql
    # issue #22: archived deals drop out of the rollup with the rest of the sweep
    assert "IN (SELECT id FROM deals WHERE company_id = %s AND archived_at IS NULL)" in act_sql
    # open_deal_value excludes won/lost
    assert out["open_deal_value"] == 300
    assert out["contacts"] and out["deals"] and out["activity"]


def test_get_company_detail_missing_returns_none(rec):
    rec.fetchone_queue = [None]
    assert service.get_company_detail(999) is None


def test_create_contact_forwards_company_id(rec):
    rec.fetchone_queue = [{"id": 1}, {"id": 1}]
    service.create_contact("Ana", company_id=7)
    insert_sql = rec.sql_containing("INSERT INTO contacts")
    assert "company_id" in insert_sql
    assert 7 in rec.params_for("INSERT INTO contacts")


def test_create_deal_forwards_company_id(rec):
    rec.fetchone_queue = [{"id": 1}, {"id": 1}]
    service.create_deal("D", company_id=7)
    insert_sql = rec.sql_containing("INSERT INTO deals")
    assert "company_id" in insert_sql
    assert 7 in rec.params_for("INSERT INTO deals")


def test_update_contact_accepts_explicit_null_company_id(rec):
    rec.fetchone_queue = [{"id": 1}]
    service.update_contact(1, company_id=None)
    sql = rec.sql_containing("UPDATE contacts SET")
    assert "company_id = %s" in sql
    # explicit None reaches the SQL params (unlink)
    assert None in rec.params_for("UPDATE contacts SET")


# ── Company resolution on ingestion (issue #35) ───────────────────────────────

def test_resolve_or_create_company_ids_two_fixed_queries_and_raw_key_map(rec):
    rec.fetchall_queue = [[{"raw": "Acme", "id": 9}, {"raw": "  ACME  ", "id": 9}]]
    out = service.resolve_or_create_company_ids(["Acme", "  ACME  ", "Acme", "", "   "])
    # exactly two statements regardless of input size
    assert len(rec.calls) == 2
    insert_sql = rec.sql_containing("INSERT INTO companies")
    assert "unnest(%s::text[]) WITH ORDINALITY" in insert_sql
    # targeted at the normalized-name index, not a bare ON CONFLICT (a bare one
    # would also swallow a PK conflict and strand the contact unlinked)
    assert "ON CONFLICT (LOWER(btrim(name, E' \\t\\n\\r\\f\\x0b'))) DO NOTHING" in insert_sql
    # blanks dropped, exact-string dedupe preserves first-seen order
    assert rec.params_for("INSERT INTO companies")[0] == ["Acme", "  ACME  "]
    # keyed by the RAW spelling the caller passed; both spellings -> the same id
    assert out == {"Acme": 9, "  ACME  ": 9}


def test_resolve_or_create_company_ids_blank_input_issues_no_queries(rec):
    assert service.resolve_or_create_company_ids([]) == {}
    assert service.resolve_or_create_company_ids(["", "   ", "\t\n"]) == {}
    assert rec.calls == []  # never touches the DB for nothing


def test_create_contact_resolves_company_text_to_id(rec):
    rec.fetchall_queue = [[{"raw": "Acme", "id": 9}]]
    rec.fetchone_queue = [{"id": 1}, {"id": 1}]
    service.create_contact("Ana", company="Acme")
    assert rec.sql_containing("INSERT INTO companies")  # auto-created
    # the resolved id is what lands on the contact row
    assert 9 in rec.params_for("INSERT INTO contacts")


def test_create_contact_explicit_company_id_skips_resolution(rec):
    rec.fetchone_queue = [{"id": 1}, {"id": 1}]
    service.create_contact("Ana", company="Acme", company_id=3)
    assert not any("INSERT INTO companies" in sql for sql, _ in rec.calls)
    assert 3 in rec.params_for("INSERT INTO contacts")


def test_create_contact_blank_company_skips_resolution(rec):
    rec.fetchone_queue = [{"id": 1}, {"id": 1}]
    service.create_contact("Ana", company="   ")
    assert not any("INSERT INTO companies" in sql for sql, _ in rec.calls)


def test_update_contact_resolves_company_text_when_no_id_key(rec):
    rec.fetchall_queue = [[{"raw": "Acme", "id": 9}]]
    rec.fetchone_queue = [{"id": 1}]
    service.update_contact(1, company="Acme")
    sql = rec.sql_containing("UPDATE contacts SET")
    assert "company_id = %s" in sql          # link derived from the text
    assert 9 in rec.params_for("UPDATE contacts SET")


def test_update_contact_blank_company_text_unlinks(rec):
    rec.fetchone_queue = [{"id": 1}]
    service.update_contact(1, company="")
    assert not any("INSERT INTO companies" in sql for sql, _ in rec.calls)
    sql = rec.sql_containing("UPDATE contacts SET")
    assert "company_id = %s" in sql
    assert None in rec.params_for("UPDATE contacts SET")


def test_update_contact_explicit_null_company_id_wins_over_text(rec):
    """ContactForm always sends company_id; an explicit null means unlink and
    must NOT be overridden by resolving the free text sitting next to it."""
    rec.fetchone_queue = [{"id": 1}]
    service.update_contact(1, company="Acme", company_id=None)
    assert not any("INSERT INTO companies" in sql for sql, _ in rec.calls)  # no resolution
    params = rec.params_for("UPDATE contacts SET")
    assert None in params and 9 not in params


def test_list_contacts_company_sort_uses_effective_name(rec):
    rec.fetchone_queue = [{"cnt": 0}]
    rec.fetchall_queue = [[]]
    service.list_contacts(sort="company")
    # Sorts by what the UI renders (link first, legacy text as fallback), ASCENDING —
    # a name sort means A→Z, which is what ?sort=company advertises and what
    # list_companies has always done. It answered Z→A until #77.
    assert "ORDER BY COALESCE(co.name, ct.company) ASC, ct.id ASC" in rec.sql_containing("ORDER BY")


def test_update_deal_accepts_company_id(monkeypatch, rec, fake_conn):
    # Deal writes go through one transaction (issue #22: the stage event must land
    # with the UPDATE), so the UPDATE is on the raw cursor, not pg_execute.
    conn = fake_conn(monkeypatch, service, fetchone_results=[("lead", None, None, None)])
    rec.fetchone_queue = [{"id": 1}]
    service.update_deal(1, company_id=None)
    sql = next(s for s, _ in conn.executed if "UPDATE deals SET" in s)
    assert "company_id = %s" in sql
    params = next(p for s, p in conn.executed if "UPDATE deals SET" in s)
    assert None in params


def test_get_contact_detail_joins_company_name(rec):
    rec.fetchone_queue = [{"id": 1, "name": "Ana", "company_name": "Acme"}]
    rec.fetchall_queue = [[], [], []]  # deals, todos, activity
    out = service.get_contact_detail(1)
    join_sql = rec.sql_containing("company_name")
    assert "LEFT JOIN companies" in join_sql
    assert out["company_name"] == "Acme"


def test_get_deal_joins_company_name(rec):
    rec.fetchone_queue = [{"id": 1, "company_name": "Acme"}]
    service.get_deal(1)
    sql = rec.sql_containing("FROM deals d")
    assert "co.name AS company_name" in sql and "LEFT JOIN companies co" in sql


# ── Pipeline board payload: company_name + derived last_activity_at (issue #21) ─

def test_get_pipeline_includes_company_and_last_activity(rec):
    # deals query, then the stage_summary aggregate query.
    rec.fetchall_queue = [
        [{"id": 1, "stage": "lead", "contact_name": "Ann",
          "company_name": "Acme", "last_activity_at": "2026-07-20T10:00:00+00:00"}],
        [{"stage": "lead", "count": 1, "total_value": 100.0}],
    ]
    out = service.get_pipeline()
    sql = rec.sql_containing("last_activity_at")
    # company join mirrors get_deal; new derived field is aliased.
    assert "co.name AS company_name" in sql and "LEFT JOIN companies co" in sql
    assert "la.last_at AS last_activity_at" in sql
    # last_activity blends BOTH genuine activity lanes via UNION ALL → one row per deal.
    assert "UNION ALL" in sql and "GROUP BY deal_id" in sql
    assert "FROM activity_log WHERE deal_id IS NOT NULL" in sql
    assert "FROM crm_chatter" in sql and "entity_type = 'deal'" in sql and "archived = 0" in sql
    # unfiltered board load carries no stage WHERE.
    assert "WHERE d.stage" not in sql
    # new fields pass straight through the payload.
    assert out["deals"][0]["company_name"] == "Acme"
    assert out["deals"][0]["last_activity_at"] == "2026-07-20T10:00:00+00:00"
    assert out["total_pipeline_value"] == 100.0


def test_get_pipeline_stage_branch_carries_new_fields(rec):
    # The stage-filtered branch (used by the assistant's crm_get_pipeline tool) must
    # carry the same new fields — the unified query guarantees the branches can't drift.
    rec.fetchall_queue = [
        [{"id": 2, "stage": "lead", "company_name": None, "last_activity_at": None}],
        [],
    ]
    service.get_pipeline(stage="lead")
    sql = rec.sql_containing("last_activity_at")
    assert "WHERE d.archived_at IS NULL AND d.stage = %s" in sql
    assert "la.last_at AS last_activity_at" in sql and "co.name AS company_name" in sql
    assert rec.params_for("last_activity_at") == ["lead"]


# ── Pipeline board: the three bounding modes (issue #59) ──────────────────────

def test_get_pipeline_keyset_first_page(rec):
    rec.fetchall_queue = [[], []]
    out = service.get_pipeline(limit=501)

    sql = rec.sql_containing("ORDER BY d.id ASC")
    assert "LIMIT %s" in sql
    # A page must use the per-deal LATERAL, never the whole-table grouped aggregate:
    # the grouped form would scan all of activity_log on every page of a sweep.
    assert "LEFT JOIN LATERAL" in sql and "GROUP BY deal_id" not in sql
    # ...while still carrying every column the unbounded board carries.
    assert "la.last_at AS last_activity_at" in sql and "co.name AS company_name" in sql
    assert "d.id > %s" not in sql, "a first page has no cursor"
    assert rec.params_for("ORDER BY d.id ASC") == [501]
    # A FIRST page still pays for — and returns — the envelope.
    assert out["stage_summary"] == [] and out["total_pipeline_value"] == 0
    # ...and never advertises a truncation flag: that answers a per-stage-cap question
    # this mode was not asked. The sweep derives hasMore from an over-fetched ROW.
    assert "deals_truncated" not in out


def test_get_pipeline_cursor_page_skips_the_aggregate(rec):
    rec.fetchall_queue = [[]]
    out = service.get_pipeline(limit=501, after_id=1207)

    assert rec.params_for("d.id > %s") == [1207, 501]
    assert len(rec.calls) == 1, "a continuation page must not run the stage_summary aggregate"
    assert out["stage_summary"] is None and out["total_pipeline_value"] is None


def test_get_pipeline_keyset_respects_include_archived_and_stage(rec):
    rec.fetchall_queue = [[]]
    service.get_pipeline(limit=10, after_id=5, include_archived=True)
    assert "archived_at IS NULL" not in rec.sql_containing("ORDER BY d.id ASC")

    rec.calls.clear()
    rec.fetchall_queue = [[], []]
    service.get_pipeline(stage="lead", limit=10)
    sql = rec.sql_containing("ORDER BY d.id ASC")
    assert "d.stage = %s" in sql
    # The stage filter leads the params, the cap trails them — one shared condition list.
    assert rec.params_for("ORDER BY d.id ASC") == ["lead", 10]


def test_get_pipeline_window_caps_per_stage_in_sql(rec):
    # Three lead deals ranked 1..3 and one won deal, for a cap of 2: the rank-3 row is the
    # over-fetch probe and must be dropped, leaving the truncation flag set.
    rec.fetchall_queue = [
        [{"id": 3, "stage": "lead", "rn": 1}, {"id": 2, "stage": "lead", "rn": 2},
         {"id": 9, "stage": "won", "rn": 1}, {"id": 1, "stage": "lead", "rn": 3}],
        [{"stage": "lead", "count": 10, "total_value": 999}],
    ]
    out = service.get_pipeline(limit_per_stage=2)

    sql = rec.sql_containing("ROW_NUMBER")
    assert "PARTITION BY d.stage" in sql
    assert "ORDER BY d.updated_at DESC, d.id DESC" in sql
    # The window reads the whole corpus in one statement, so it keeps the grouped join.
    assert "GROUP BY deal_id" in sql and "LEFT JOIN LATERAL" not in sql
    assert rec.params_for("ROW_NUMBER") == [3], "asks for cap + 1, the truncation probe"

    assert [d["id"] for d in out["deals"]] == [3, 2, 9]
    assert all("rn" not in d for d in out["deals"]), "the rank must never leave the service"
    assert out["deals_truncated"] is True
    # Counts and value still cover EVERY deal — trimming the list must not lie.
    assert out["stage_summary"][0]["count"] == 10


def test_get_pipeline_window_reports_no_truncation_when_it_fits(rec):
    rec.fetchall_queue = [[{"id": 1, "stage": "lead", "rn": 1}], []]
    assert service.get_pipeline(limit_per_stage=2)["deals_truncated"] is False

    # The default board has no cap, so it must not advertise a truncation flag at all.
    rec.fetchall_queue = [[{"id": 1, "stage": "lead"}], []]
    assert "deals_truncated" not in service.get_pipeline()


def test_list_deals_treats_contact_id_zero_as_a_filter(rec):
    """The ROUTE deliberately routes `?contact_id=0` here rather than to the board (see
    test_contact_id_zero_is_a_filter_not_a_fallthrough), so a truthiness test in the
    service answered that filtered request with the WHOLE deal list."""
    rec.fetchall_queue = [[]]
    service.list_deals(contact_id=0)
    assert "d.contact_id = %s" in rec.sql_containing("FROM deals d")
    assert rec.params_for("d.contact_id = %s") == [0, 50]

    # An omitted contact_id still means "no filter" — the fix must not invent one.
    # (The needle is the WHERE predicate, not `d.contact_id`, which is also the JOIN key.)
    rec.calls.clear()
    rec.fetchall_queue = [[]]
    service.list_deals()
    assert "d.contact_id = %s" not in rec.sql_containing("FROM deals d")
    assert rec.params_for("FROM deals d") == [50]


def test_get_pipeline_refuses_meaningless_combinations(rec):
    for kwargs in ({"after_id": 5},
                   {"limit": 5, "limit_per_stage": 5},
                   {"after_id": 5, "limit": 5, "limit_per_stage": 5}):
        with pytest.raises(ValueError):
            service.get_pipeline(**kwargs)
    assert rec.calls == [], "a refused call must not reach Postgres"


def test_search_companies_status_filter(rec):
    service.search_companies("acme", status="active")
    sql = rec.sql_containing("FROM companies WHERE")
    assert "status = %s" in sql
    assert "active" in rec.params_for("FROM companies WHERE")


def test_list_companies_status_filter(rec):
    rec.fetchone_queue = [{"cnt": 0}]
    rec.fetchall_queue = [[]]
    service.list_companies(status="archived")
    sql = rec.sql_containing("SELECT * FROM companies")
    assert "status = %s" in sql
    assert "archived" in rec.params_for("SELECT * FROM companies")


def test_update_company_coerces_unknown_status(rec):
    rec.fetchone_queue = [{"id": 1, "name": "Acme"}]
    service.update_company(1, status="bogus")
    assert "active" in rec.params_for("UPDATE companies SET")


def test_update_company_drops_blank_name(rec):
    rec.fetchone_queue = [{"id": 1, "name": "Acme"}]
    # A blank name must never be persisted (guards the tool path that bypasses the router).
    service.update_company(1, name="   ", domain="x.io")
    sql = rec.sql_containing("UPDATE companies SET")
    assert "name = %s" not in sql  # blank name dropped
    assert "domain = %s" in sql


def test_update_company_drops_none_values(rec):
    # A tool call sending explicit None for a NOT NULL text column must be dropped,
    # not passed to SQL (which would NotNullViolation). The HTTP route filters None
    # already; this guards the tool path.
    rec.fetchone_queue = [{"id": 1, "name": "Acme"}]
    service.update_company(1, domain="x.io", phone=None, notes=None)
    sql = rec.sql_containing("UPDATE companies SET")
    assert "domain = %s" in sql
    assert "phone = %s" not in sql and "notes = %s" not in sql


def test_update_company_rejects_unicode_blank_name(rec):
    # A name that is blank once ALL whitespace is stripped (e.g. a lone NBSP) is
    # dropped, so it can't create a visually-blank company via the tool path.
    rec.fetchone_queue = [{"id": 1, "name": "Acme"}]
    service.update_company(1, name="\u00a0", domain="x.io")  # NBSP-only name
    sql = rec.sql_containing("UPDATE companies SET")
    assert "name = %s" not in sql
    assert "domain = %s" in sql


# ── #18 lead-score trigger wiring (score_on_event is fire-and-forget + swallows errors,
#    so a miswired id would fail silently forever — pin the exact ids at each chokepoint) ──

@pytest.fixture
def score_spy(monkeypatch):
    from crm import scoring_service
    calls = []
    monkeypatch.setattr(scoring_service, "score_on_event", lambda **kw: calls.append(kw))
    return calls


def test_create_deal_scores_deal_and_contact(rec, score_spy):
    rec.fetchone_queue = [{"id": 9}, {"id": 9, "contact_id": 3}]  # INSERT id, get_deal
    service.create_deal("D", contact_id=3, stage="lead")
    assert score_spy == [{"deal_ids": (9,), "contact_ids": (3,)}]


def test_update_deal_relink_scores_old_and_new_contact(monkeypatch, rec, fake_conn, score_spy):
    # contact_id in the update -> the funnel's FOR UPDATE row carries the old link (2);
    # the new link (5) rides in the update itself. Both rescore, new first.
    fake_conn(monkeypatch, service, fetchone_results=[("lead", None, 2, None)])
    rec.fetchone_queue = [{"id": 1, "contact_id": 5}]  # get_deal after the funnel
    service.update_deal(1, contact_id=5)
    assert score_spy == [{"deal_ids": (1,), "contact_ids": (5, 2)}]


def test_update_deal_stage_scores_deal_and_linked_contact(monkeypatch, rec, fake_conn, score_spy):
    fake_conn(monkeypatch, service, fetchone_results=[("lead", None, 4, None)])
    rec.fetchone_queue = [{"id": 1, "contact_id": 4}]  # get_deal after the funnel
    service.update_deal_stage(1, "won")
    # No re-link in a stage move: slot 1 (the update's contact_id) is empty, the
    # funnel's FOR UPDATE row supplies the linked contact. score_on_event drops Nones.
    assert score_spy == [{"deal_ids": (1,), "contact_ids": (None, 4)}]


def test_mark_deal_won_scores_through_the_funnel(monkeypatch, rec, fake_conn, score_spy):
    # The #22 lifecycle verbs postdate #18 — the funnel hook is what guarantees they
    # rescore at all. Pin one so the hook can't silently move back into the callers.
    fake_conn(monkeypatch, service, fetchone_results=[("negotiation", None, 7, None)])
    rec.fetchone_queue = [{"id": 3, "contact_id": 7}]
    service.mark_deal_won(3)
    assert score_spy == [{"deal_ids": (3,), "contact_ids": (None, 7)}]


def test_archive_deal_scores_deal_and_linked_contact(rec, score_spy):
    # Archived deals leave the contact's deal-linkage aggregate (scoring_service
    # carries archived_at IS NULL), so archive/restore must rescore the contact.
    rec.cursor_fetchone_queue = [(None, 7)]  # locked pre-image: live, contact 7
    rec.fetchone_queue = [{"id": 1}]  # get_deal
    service.archive_deal(1, reason="dupe")
    assert score_spy == [{"deal_ids": (1,), "contact_ids": (7,)}]


def test_create_and_update_contact_score_the_contact(rec, score_spy):
    rec.fetchone_queue = [{"id": 7}, {"id": 7}]  # INSERT id, get_contact
    service.create_contact("Ada")
    rec.fetchone_queue = [{"id": 7}]  # get_contact after UPDATE
    service.update_contact(7, status="inactive")
    assert score_spy == [{"contact_ids": (7,)}, {"contact_ids": (7,)}]


def test_log_activity_scores_deal_and_contact(rec, score_spy, monkeypatch):
    from crm import touch_count_service
    monkeypatch.setattr(touch_count_service, "schedule_recompute", lambda *a, **k: None)
    rec.fetchone_queue = [{"id": 3}, {"id": 3, "deal_id": 8, "contact_id": 6}]  # INSERT id, re-select
    service.log_activity("call", deal_id=8, contact_id=6)
    assert score_spy == [{"deal_ids": (8,), "contact_ids": (6,)}]


def test_delete_activity_scores_returned_links(rec, score_spy):
    rec.fetchone_queue = [{"contact_id": 5, "deal_id": 7}]  # DELETE ... RETURNING
    assert service.delete_activity(3) is True
    assert score_spy == [{"deal_ids": (7,), "contact_ids": (5,)}]


# ── #18 contact sort fragment (allowlisted, DESC NULLS LAST + id tiebreak) ──

def test_list_contacts_lead_score_sort_fragment(rec):
    rec.fetchone_queue = [{"cnt": 0}]
    rec.fetchall_queue = [[]]
    service.list_contacts(sort="lead_score")
    sql = rec.sql_containing("ORDER BY ct.lead_score")
    assert "lead_score DESC NULLS LAST" in sql and "id DESC" in sql


def test_list_contacts_unknown_sort_falls_back_to_updated_at(rec):
    rec.fetchone_queue = [{"cnt": 0}]
    rec.fetchall_queue = [[]]
    service.list_contacts(sort="bogus; DROP TABLE contacts")  # never interpolated (allowlist)
    sql = rec.sql_containing("ORDER BY")
    assert "ORDER BY ct.updated_at DESC, ct.id DESC" in sql
    assert "DROP TABLE" not in sql


def test_search_contacts_honors_lead_score_sort(rec):
    service.search_contacts("acme", sort="lead_score")
    assert "lead_score DESC NULLS LAST" in rec.sql_containing("FROM contacts ct")


# ── #77: the list pages' keyset assembly, and the reads it needs ──────────────


def test_list_todos_default_order_gains_an_id_tiebreaker(rec):
    """The historical due order, made deterministic.

    Every pre-#77 caller (the crm_list_todos tool, the heartbeat, the rollups) still gets
    `completed ASC, due_date ASC` — but ties among todos sharing a due date used to be
    resolved arbitrarily by Postgres, so a LIMIT window could omit one todo and repeat
    another between two identical requests.
    """
    service.list_todos()
    sql = rec.sql_containing("FROM todos t")
    assert "ORDER BY t.completed ASC, t.due_date ASC, t.id ASC LIMIT %s" in sql
    assert "OFFSET" not in sql  # this endpoint never had one and still does not
    assert rec.params_for("FROM todos t")[-1] == 50


def test_list_todos_id_sort_is_the_assembly_key(rec):
    service.list_todos(sort="id", after_id=500, limit=501)
    sql = rec.sql_containing("FROM todos t")
    assert "ORDER BY t.id ASC LIMIT %s" in sql
    assert "t.id > %s" in sql
    params = rec.params_for("FROM todos t")
    assert params[-2:] == [500, 501]  # cursor binds in the WHERE, limit last


def test_list_todos_unknown_sort_falls_back_to_the_due_order(rec):
    service.list_todos(sort="bogus")
    assert "ORDER BY t.completed ASC, t.due_date ASC, t.id ASC" in rec.sql_containing("FROM todos t")


@pytest.mark.parametrize(
    "call",
    [
        lambda: service.list_contacts(after_id=5, sort="updated_at"),
        lambda: service.list_contacts(after_id=5),  # the DEFAULT sort is not id either
        lambda: service.list_companies(after_id=5, sort="name"),
        lambda: service.list_todos(after_id=5, sort="due"),
        lambda: service.list_todos(after_id=5),
    ],
)
def test_a_cursor_against_a_mutable_order_is_refused(rec, call):
    """Fail loudly rather than paginate wrong.

    `after_id` means "the rows after this one in the current order"; under updated_at or
    name that is not well defined (not unique, not stable), so the window would silently
    skip and repeat rows. Silently IGNORING the parameter would be worse still — it looks
    exactly like a client stuck re-reading page one.
    """
    rec.fetchone_queue = [{"cnt": 0}]
    with pytest.raises(ValueError):
        call()


def test_contact_name_and_company_sorts_ascend(rec):
    rec.fetchone_queue = [{"cnt": 0}]
    service.list_contacts(sort="name")
    assert "ORDER BY ct.name ASC, ct.id ASC" in rec.sql_containing("ORDER BY")


def test_contact_and_company_id_sorts_are_plain_ascending_keys(rec):
    rec.fetchone_queue = [{"cnt": 0}]
    service.list_contacts(sort="id", after_id=7)
    contact_sql = rec.sql_containing("FROM contacts ct LEFT JOIN")
    assert "ORDER BY ct.id ASC LIMIT" in contact_sql
    assert "ct.id > %s" in contact_sql

    rec.calls.clear()
    rec.fetchone_queue = [{"cnt": 0}]
    service.list_companies(sort="id", after_id=7)
    company_sql = rec.sql_containing("FROM companies WHERE")
    # Its own tie-breaker — emitting "id ASC, id ASC" would be redundant SQL.
    assert "ORDER BY id ASC LIMIT" in company_sql
    assert "id ASC, id ASC" not in company_sql
    assert "id > %s" in company_sql


def test_an_ordinary_list_still_counts_the_whole_filtered_set(rec):
    """A cursor is the WINDOW, not a filter.

    The repo rule that a filter must reach the COUNT and the page query together exists so
    a total cannot disagree with the rows. A real filter (status, owner) therefore does
    reach the COUNT; the cursor never does.
    """
    rec.fetchone_queue = [{"cnt": 4200}]
    result = service.list_contacts(status="active")
    count_sql = rec.sql_containing("COUNT(*)")
    assert "ct.status = %s" in count_sql  # a real filter DOES reach it
    assert result["total"] == 4200


@pytest.mark.parametrize(
    "call",
    [
        lambda: service.list_contacts(sort="id", after_id=900),
        lambda: service.list_companies(sort="id", after_id=900),
    ],
)
def test_a_cursor_page_skips_the_count_entirely(rec, call):
    """Not a micro-optimisation: a corpus sweep is up to MAX_PAGES requests and never reads
    `total`, so counting on each would add a scan of the whole filtered set to the heaviest
    read path in the app. `total` is None there rather than a stale or wrong integer."""
    result = call()
    assert result["total"] is None
    assert not any("COUNT(*)" in sql for sql, _ in rec.calls)


@pytest.mark.parametrize(
    "call",
    [
        lambda: service.list_contacts(sort="id", offset=50),
        lambda: service.list_companies(sort="id", offset=50),
    ],
)
def test_plain_offset_pagination_on_the_id_order_still_gets_a_total(rec, call):
    """`sort=id` alone is ordinary offset pagination, not a sweep.

    Keying the skip on the SORT would strip `total` from a caller paging with
    `?sort=id&offset=N`, who needs it to know how many pages remain — and the sweep's own
    first page is indistinguishable from that request anyway, so it pays one COUNT.
    """
    rec.fetchone_queue = [{"cnt": 4200}]
    assert call()["total"] == 4200


@pytest.mark.parametrize(
    "call",
    [
        lambda: service.list_contacts(sort="id", after_id=5, offset=10),
        lambda: service.list_companies(sort="id", after_id=5, offset=10),
    ],
)
def test_a_cursor_combined_with_an_offset_is_refused(rec, call):
    """Two competing ways to say where the window starts. Applying both silently skips
    exactly `offset` eligible rows — the same class of quiet wrongness the sort pairing
    check exists to prevent, so it fails the same way."""
    with pytest.raises(ValueError):
        call()


def test_contact_list_search_and_detail_all_derive_last_contact_at(rec):
    """One definition of "when did we last talk to this person", used by three reads."""
    for prime, call in (
        ([{"cnt": 0}], lambda: service.list_contacts()),
        ([], lambda: service.search_contacts("acme")),
        ([{"id": 1}, None, None], lambda: service.get_contact_detail(1)),
    ):
        rec.calls.clear()
        rec.fetchone_queue = list(prime)
        rec.fetchall_queue = [[], [], [], []]
        call()
        sql = rec.sql_containing("last_contact_at")
        assert "lt.last_at AS last_contact_at" in sql
        # Both signals, and the archived + housekeeping exclusions.
        assert "FROM activity_log a WHERE a.contact_id = ct.id" in sql
        assert "ch.entity_type = 'contact'" in sql
        assert "ch.archived = 0" in sql
        # The housekeeping family (#239) — provenance, archive and restore notes — is a
        # rendered predicate, so no pattern param rides along any more.
        assert scoring_service.not_housekeeping_sql("ch.message") in sql
        assert "NOT LIKE" not in sql


def test_the_contact_count_query_stays_join_free(rec):
    """The COUNT must not pay for the derived touch — it does not select it."""
    rec.fetchone_queue = [{"cnt": 0}]
    service.list_contacts()
    count_sql = rec.sql_containing("COUNT(*)")
    assert "LEFT JOIN" not in count_sql
    assert "crm_chatter" not in count_sql


def test_get_todo_returns_a_list_shaped_row(rec):
    """Todo writes return get_todo, and the list patches itself from those bodies (#77).

    Without the joins a saved todo loses its contact/deal label in the list — and a todo
    re-linked to another contact would keep showing the old name.
    """
    service.get_todo(1)
    sql = rec.sql_containing("FROM todos t")
    assert "c.name AS contact_name" in sql
    assert "d.title AS deal_title" in sql


# ── Link labels must ride every row that can reach the deal form (issue #123) ─


def test_top_deals_joins_the_company_name(rec):
    """The dashboard hands `top_deals` rows straight to the deal sheet and on to DealForm,
    whose link pickers render the NAME the row arrives with. A row carrying a company_id and
    no company_name renders an EMPTY company box on a deal that has one — reading as "no
    company", which is the exact hazard those pickers replaced a capped <select> to end.
    """
    rec.fetchone_queue = [{"cnt": 0}] * 12
    service.get_dashboard_stats()

    sql = rec.sql_containing(service.TOP_DEAL_SCORE_SQL)
    assert "co.name AS company_name" in sql
    assert "LEFT JOIN companies co ON d.company_id = co.id" in sql
    # ...alongside the contact name it already carried, not instead of it.
    assert "c.name AS contact_name" in sql


# ── Top deals: qualified-only, probability-weighted (issue #240) ──────────────

def test_top_deals_leave_out_leads_and_rank_by_the_weighted_score(rec):
    """The list used to be `ORDER BY d.value` over every open deal, so a $1M lead sat on
    top forever. The ranking itself is pinned against real Postgres in
    test_integration_crm_lifecycle_pg; this pins the query shape the dashboard emits."""
    rec.fetchone_queue = [{"cnt": 0}] * 12
    service.get_dashboard_stats()

    sql = rec.sql_containing(service.TOP_DEAL_SCORE_SQL)
    assert "d.stage NOT IN ('won', 'lost')" in sql
    assert "d.stage <> 'lead'" in sql
    assert "d.archived_at IS NULL" in sql
    assert f"ORDER BY {service.TOP_DEAL_SCORE_SQL} DESC, d.updated_at DESC, d.id DESC LIMIT 5" in sql
    assert "ORDER BY d.value" not in sql


# ── Archiving a contact or company records who and why (#239) ───────────────

def _writes(conn):
    return [s for s, _ in conn.executed if s.startswith(("UPDATE", "INSERT"))]


@pytest.mark.parametrize("update, table, entity", [
    (service.update_contact, "contacts", "contact"),
    (service.update_company, "companies", "company"),
])
def test_archiving_needs_a_reason_and_writes_the_note_with_the_update(
    monkeypatch, rec, fake_conn, update, table, entity,
):
    conn = fake_conn(monkeypatch, service, fetchone_results=[("active",)])
    rec.fetchone_queue = [{"id": 1, "status": "active"}]  # the unlocked peek (contact) / the getter
    with pytest.raises(ValueError, match="reason is required"):
        update(1, status="archived", archive_reason="  ")
    assert _writes(conn) == []

    conn = fake_conn(monkeypatch, service, fetchone_results=[("active",)])
    rec.fetchone_queue = [{"id": 1}]
    update(1, status="archived", archive_reason=" went dark ", actor_id=5)
    stmts = [s for s, _ in conn.executed]
    assert stmts[0] == f"SELECT status FROM {table} WHERE id = %s FOR UPDATE"
    note = next(p for s, p in conn.executed if "INSERT INTO crm_chatter" in s)
    assert note[:3] == (entity, 1, "Archived — went dark") and note[4] == 5
    assert conn.entries == 1 and len(set(conn.executed_by)) == 1


@pytest.mark.parametrize("update", [service.update_contact, service.update_company])
@pytest.mark.parametrize("old, new, note", [
    ("archived", "active", scoring_service.RESTORE_NOTE),
    ("archived", "archived", None),  # re-saving an archived record: no second note
    ("active", "active", None),
])
def test_only_a_move_across_the_archived_line_leaves_a_note(
    monkeypatch, rec, fake_conn, update, old, new, note,
):
    conn = fake_conn(monkeypatch, service, fetchone_results=[(old,)])
    rec.fetchone_queue = [{"id": 1, "status": old}, {"id": 1}]  # contact's peek, getter
    update(1, status=new, actor_id=5)
    notes = [p[2] for s, p in conn.executed if "INSERT INTO crm_chatter" in s]
    assert notes == ([note] if note else [])


def test_a_refused_contact_archive_auto_creates_no_company(monkeypatch, rec, fake_conn):
    """The peek runs BEFORE company-text resolution, which can INSERT a company."""
    fake_conn(monkeypatch, service, fetchone_results=[("active",)])
    rec.fetchone_queue = [{"status": "active"}]
    with pytest.raises(ValueError):
        service.update_contact(1, status="archived", company="Brand New Co")
    assert not any("INSERT INTO companies" in s for s, _ in rec.calls)


def test_a_missing_contact_or_company_update_returns_none(monkeypatch, rec, fake_conn):
    fake_conn(monkeypatch, service, fetchone_results=[None])
    assert service.update_contact(9, name="x") is None
    fake_conn(monkeypatch, service, fetchone_results=[None])
    assert service.update_company(9, name="x") is None


@pytest.mark.parametrize("create", [service.create_contact, service.create_company])
def test_a_record_cannot_be_created_already_archived(rec, create):
    with pytest.raises(ValueError, match="cannot be created already archived"):
        create("A", status="archived")
    assert rec.calls == []


# ── The housekeeping family (#239) ──────────────────────────────────────────

def test_every_housekeeping_writer_matches_the_family_and_renders_without_a_percent():
    """The exclusion must mirror each writer's text, and must carry no `%`: LAST_TOUCH_SQL
    is interpolated into statements psycopg2 is handed parameters for."""
    written = [
        "Confirmed AI-populated value for 'probability'.",  # provenance_service.confirm
        scoring_service.ARCHIVE_NOTE_PREFIX + "dupe",
        scoring_service.RESTORE_NOTE,
        service._closed_on_note(None, "2026-10-01"),  # #279's Closed on set/clear/edit
    ]
    assert all(any(w.startswith(p) for p in scoring_service.HOUSEKEEPING_NOTE_PREFIXES)
               for w in written)
    rendered = scoring_service.not_housekeeping_sql("ch.message")
    assert rendered.count("NOT starts_with(ch.message, '") == 4 and "%" not in rendered


def test_last_touch_excludes_housekeeping_notes_so_archiving_moves_no_clock():
    """Today panel, stale deals, the nudges and Weekly Touches (via TOUCH_AT_SQL) all
    read LAST_TOUCH_SQL, so all of them now agree an audit note is not a touch."""
    predicate = scoring_service.not_housekeeping_sql("ch.message")
    assert predicate in service.LAST_TOUCH_SQL
    assert service.LAST_TOUCH_SQL in service.TOUCH_AT_SQL
    assert "%" not in service.LAST_TOUCH_SQL
