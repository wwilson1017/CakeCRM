"""Hermetic CRM service tests — SQL shape + behavior.

The service imports the pg helpers by name, so we monkeypatch
``crm.service.pg_fetchone/pg_fetchall/pg_execute`` with a recorder (FakeCursor
can't feed ``row_to_dict``, which needs ``cursor.description``). Multi-statement
writes go through ``get_connection`` and use the shared ``fake_conn`` fixture.
"""

import pytest

from crm import service


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


@pytest.fixture
def rec(monkeypatch):
    r = Recorder()
    monkeypatch.setattr(service, "pg_fetchone", r.fetchone)
    monkeypatch.setattr(service, "pg_fetchall", r.fetchall)
    monkeypatch.setattr(service, "pg_execute", r.execute)
    return r


# ── Contacts ──────────────────────────────────────────────────────────────────

def test_create_contact_insert_returning_and_tag_normalization(rec):
    rec.fetchone_queue = [{"id": 7}, {"id": 7, "name": "Ana"}]
    result = service.create_contact("Ana", tags=" PT, ET ,MT ")
    insert_sql = rec.sql_containing("INSERT INTO contacts")
    assert "RETURNING id" in insert_sql
    assert "%s" in insert_sql and "?" not in insert_sql
    # tags normalized (whitespace stripped, empties dropped) in the insert params
    assert "PT,ET,MT" in rec.params_for("INSERT INTO contacts")
    # hydrated via a follow-up SELECT by the returned id
    assert rec.params_for("SELECT * FROM contacts WHERE id = %s") == [7]
    assert result == {"id": 7, "name": "Ana"}


def test_search_contacts_ilike_and_tag_boundary(rec):
    service.search_contacts("acme", status="active", tags="vip, lead", limit=15)
    sql = rec.sql_containing("FROM contacts WHERE")
    assert sql.count("ILIKE") >= 4  # name/email/company/notes + tag clauses
    assert "LIKE %s" not in sql.replace("ILIKE %s", "")  # no case-sensitive LIKE
    assert "status = %s" in sql
    params = rec.params_for("FROM contacts WHERE")
    assert params[:4] == ["%acme%"] * 4
    assert "active" in params
    assert "%,vip,%" in params and "%,lead,%" in params
    assert params[-2:] == [15, 0]  # LIMIT %s OFFSET %s (default offset 0)


def test_list_contacts_count_alias_and_sort_whitelist(rec):
    rec.fetchone_queue = [{"cnt": 3}]
    rec.fetchall_queue = [[{"id": 1}]]
    out = service.list_contacts(offset=10, limit=5, sort="bogus")
    assert "COUNT(*) AS cnt" in rec.sql_containing("COUNT(*)")
    # unknown sort falls back to updated_at
    assert "ORDER BY updated_at DESC" in rec.sql_containing("ORDER BY")
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


# ── Tasks ─────────────────────────────────────────────────────────────────────

def test_list_tasks_completed_as_int_and_due_before_guard(rec):
    service.list_tasks(completed=False, due_before="2026-01-01")
    sql = rec.sql_containing("FROM tasks")
    assert "t.completed = %s" in sql
    assert "t.due_date != '' AND t.due_date <= %s" in sql
    params = rec.params_for("FROM tasks")
    assert 0 in params  # completed=False → int 0
    assert "2026-01-01" in params


def test_update_task_coerces_completed_to_0_or_1(rec):
    rec.fetchone_queue = [{"id": 1, "completed": 1}]
    service.update_task(1, completed=2)  # stray truthy value must normalize to 1
    params = rec.params_for("UPDATE tasks SET")
    assert params[0] == 1 and 2 not in params


def test_complete_task_uses_returning_and_none_when_missing(rec):
    rec.fetchone_queue = [None]  # UPDATE ... RETURNING id finds nothing
    assert service.complete_task(999) is None
    assert "UPDATE tasks SET completed = 1" in rec.sql_containing("UPDATE tasks")
    assert "RETURNING id" in rec.sql_containing("UPDATE tasks")


def test_delete_task_rowcount(rec):
    rec.execute_rowcount = 0
    assert service.delete_task(5) is False
    rec.execute_rowcount = 1
    assert service.delete_task(5) is True


def test_delete_activity_rowcount(rec):
    rec.execute_rowcount = 1
    assert service.delete_activity(3) is True


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
    assert sum("DELETE FROM" in s for s in stmts) == 3  # activity_log, tasks, contacts
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
    assert any("TRUNCATE activity_log, tasks, deals, contacts, companies RESTART IDENTITY" in s
               for s, _ in conn.executed)


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
    assert any("TRUNCATE activity_log, tasks, deals, contacts, companies RESTART IDENTITY" in s for s in stmts)
    assert any("sample_data_loaded = FALSE" in s for s in stmts)


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


def test_create_task_coerces_unknown_priority(rec):
    rec.fetchone_queue = [{"id": 1}, {"id": 1}]
    service.create_task("T", priority="urgent")  # not a real priority
    assert "medium" in rec.params_for("INSERT INTO tasks")
    assert "urgent" not in rec.params_for("INSERT INTO tasks")


# ── search pagination: offset + accurate total ────────────────────────────────

def test_search_contacts_forwards_offset(rec):
    service.search_contacts("acme", limit=50, offset=100)
    sql = rec.sql_containing("FROM contacts WHERE")
    assert "LIMIT %s OFFSET %s" in sql
    assert rec.params_for("FROM contacts WHERE")[-2:] == [50, 100]


def test_count_search_contacts_uses_count_and_same_where(rec):
    rec.fetchone_queue = [{"cnt": 42}]
    assert service.count_search_contacts("acme", status="active") == 42
    sql = rec.sql_containing("COUNT(*) AS cnt")
    assert "ILIKE" in sql and "status = %s" in sql
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


def test_delete_company_returns_rowcount_bool(rec):
    rec.execute_rowcount = 1
    assert service.delete_company(5) is True
    rec.execute_rowcount = 0
    assert service.delete_company(5) is False


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
    assert "IN (SELECT id FROM deals WHERE company_id = %s)" in act_sql
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


def test_update_deal_accepts_company_id(rec):
    rec.fetchone_queue = [{"id": 1}]
    service.update_deal(1, company_id=None)
    sql = rec.sql_containing("UPDATE deals SET")
    assert "company_id = %s" in sql
    assert None in rec.params_for("UPDATE deals SET")


def test_get_contact_detail_joins_company_name(rec):
    rec.fetchone_queue = [{"id": 1, "name": "Ana", "company_name": "Acme"}]
    rec.fetchall_queue = [[], [], []]  # deals, tasks, activity
    out = service.get_contact_detail(1)
    join_sql = rec.sql_containing("company_name")
    assert "LEFT JOIN companies" in join_sql
    assert out["company_name"] == "Acme"


def test_get_deal_joins_company_name(rec):
    rec.fetchone_queue = [{"id": 1, "company_name": "Acme"}]
    service.get_deal(1)
    sql = rec.sql_containing("FROM deals d")
    assert "co.name AS company_name" in sql and "LEFT JOIN companies co" in sql
