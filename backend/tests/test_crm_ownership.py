"""Record ownership and per-rep attribution (issue #60 Phase A).

Ownership is an assignment, a filter and an analytics dimension — NOT access
control. Any member may read, edit, delete and reassign any record; that is what
"no per-object ACLs" means, and these tests pin the parts that are easy to get
subtly wrong instead:

* an owner filter must reach BOTH the COUNT and the page query, or the total
  silently disagrees with the rows;
* ownership and authorship are different columns, and per-rep activity is
  attributed by ACTOR;
* absent vs explicitly-null owner_id mean opposite things on create.
"""

import pytest
from conftest import fake_admin
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.auth import get_current_user
from crm import service as crm
from crm.router import router as crm_router

ME = 1
COLLEAGUE = 2


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(crm_router, prefix="/api/crm")
    app.dependency_overrides[get_current_user] = fake_admin
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def captured_sql(monkeypatch):
    """Record every statement the service issues through the pg helpers."""
    seen: list[tuple[str, tuple | list]] = []

    def _all(sql, params=()):
        seen.append((" ".join(sql.split()), params))
        return []

    def _one(sql, params=()):
        seen.append((" ".join(sql.split()), params))
        return {"cnt": 0, "id": 1}

    monkeypatch.setattr(crm, "pg_fetchall", _all)
    monkeypatch.setattr(crm, "pg_fetchone", _one)
    return seen


def _owner_filtered(seen):
    return [sql for sql, _ in seen if "owner_id = %s" in sql]


# ── The filter must reach both halves of a paginated read ────────────────────

def test_contact_browse_filters_count_and_rows(captured_sql):
    crm.list_contacts(owner_id=ME)
    filtered = _owner_filtered(captured_sql)
    assert len(filtered) == 2, "owner filter must be in BOTH the COUNT and the page query"
    assert any("COUNT(*)" in sql for sql in filtered)
    assert any("LIMIT" in sql for sql in filtered)


def test_contact_search_filters_count_and_rows(captured_sql):
    crm.search_contacts("acme", owner_id=ME)
    crm.count_search_contacts("acme", owner_id=ME)
    assert len(_owner_filtered(captured_sql)) == 2


def test_company_browse_filters_count_and_rows(captured_sql):
    crm.list_companies(owner_id=ME)
    filtered = _owner_filtered(captured_sql)
    assert len(filtered) == 2
    assert any("COUNT(*)" in sql for sql in filtered)


def test_company_search_filters_count_and_rows(captured_sql):
    crm.search_companies("acme", owner_id=ME)
    crm.count_search_companies("acme", owner_id=ME)
    assert len(_owner_filtered(captured_sql)) == 2


def test_task_list_filters_by_assignee(captured_sql):
    crm.list_tasks(owner_id=ME)
    assert len(_owner_filtered(captured_sql)) == 1
    assert ME in captured_sql[0][1]


@pytest.mark.parametrize(
    "call",
    [
        lambda: crm.list_contacts(),
        lambda: crm.list_companies(),
        lambda: crm.list_tasks(),
        lambda: crm.search_contacts("x"),
        lambda: crm.search_companies("x"),
    ],
)
def test_absent_owner_means_everyone(captured_sql, call):
    """No behavior change for an install that never assigns owners."""
    call()
    assert _owner_filtered(captured_sql) == []


def test_owner_filter_composes_with_the_other_facets(captured_sql):
    crm.list_contacts(status="active", tags="vip", owner_id=ME)
    count_sql = next(sql for sql, _ in captured_sql if "COUNT(*)" in sql)
    assert "ct.status = %s" in count_sql
    assert "ct.owner_id = %s" in count_sql
    assert "ILIKE" in count_sql  # the tag clause


# ── The pipeline board deliberately has no server-side owner filter ──────────

def test_pipeline_takes_no_owner_parameter():
    """The board is unpaginated and every other #21 facet filters client-side. A
    server filter here would also have to be applied to the separately-computed
    stage_summary, or the cards and the column totals would disagree."""
    import inspect

    assert "owner_id" not in inspect.signature(crm.get_pipeline).parameters


# ── Ownership is not access control ─────────────────────────────────────────

def test_no_read_or_write_path_filters_by_the_calling_user(captured_sql):
    """A member can see and edit a colleague's records: ownership is an assignment,
    not a permission. If this ever starts failing, someone has quietly turned
    owner_id into an ACL — which is a product decision, not a bug fix."""
    crm.list_contacts()
    crm.list_companies()
    crm.list_tasks()
    assert _owner_filtered(captured_sql) == []


# ── Create: absent vs explicitly null ───────────────────────────────────────

def test_creating_a_record_makes_it_yours(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(crm, "create_contact", lambda **kw: captured.update(kw) or {"id": 1})
    client.post("/api/crm/contacts", json={"name": "Ada"})
    assert captured["owner_id"] == ME


def test_an_explicit_null_owner_means_unassigned(client, monkeypatch):
    """model_dump() collapses absent and null, so this is the case a naive
    implementation silently turns into "assign it to me"."""
    captured = {}
    monkeypatch.setattr(crm, "create_contact", lambda **kw: captured.update(kw) or {"id": 1})
    client.post("/api/crm/contacts", json={"name": "Ada", "owner_id": None})
    assert captured["owner_id"] is None


def test_an_explicit_owner_is_honoured(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(crm, "create_contact", lambda **kw: captured.update(kw) or {"id": 1})
    client.post("/api/crm/contacts", json={"name": "Ada", "owner_id": COLLEAGUE})
    assert captured["owner_id"] == COLLEAGUE


@pytest.mark.parametrize(
    "path,service_fn,body",
    [
        ("/api/crm/deals", "create_deal", {"title": "D"}),
        ("/api/crm/tasks", "create_task", {"title": "T"}),
        ("/api/crm/companies", "create_company", {"name": "C"}),
    ],
)
def test_every_entity_create_defaults_the_owner(client, monkeypatch, path, service_fn, body):
    captured = {}
    monkeypatch.setattr(crm, service_fn, lambda **kw: captured.update(kw) or {"id": 1})
    client.post(path, json=body)
    assert captured["owner_id"] == ME


# ── Update: clearing an owner has to be expressible ─────────────────────────

@pytest.mark.parametrize(
    "path,service_fn",
    [
        ("/api/crm/contacts/5", "update_contact"),
        ("/api/crm/deals/5", "update_deal"),
        ("/api/crm/tasks/5", "update_task"),
        ("/api/crm/companies/5", "update_company"),
    ],
)
def test_an_owner_can_be_cleared(client, monkeypatch, path, service_fn):
    """An explicit null must survive the None-stripping every update route does, or
    a record could be assigned but never unassigned."""
    captured = {}

    def _update(entity_id, **kw):
        captured.update(kw)
        return {"id": entity_id}

    monkeypatch.setattr(crm, service_fn, _update)
    r = client.put(path, json={"owner_id": None})
    assert r.status_code == 200
    assert "owner_id" in captured and captured["owner_id"] is None


def test_reassignment_reaches_the_service(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        crm, "update_deal", lambda did, **kw: captured.update(kw) or {"id": did}
    )
    client.put("/api/crm/deals/5", json={"owner_id": COLLEAGUE})
    assert captured["owner_id"] == COLLEAGUE


def test_company_update_still_drops_nulls_for_not_null_columns(client, monkeypatch):
    """The owner_id carve-out must not accidentally let a NULL through to a column
    that would raise NotNullViolation."""
    captured = {}
    monkeypatch.setattr(
        crm, "update_company", lambda cid, **kw: captured.update(kw) or {"id": cid}
    )
    client.put("/api/crm/companies/5", json={"name": "Acme", "domain": None, "owner_id": None})
    assert "domain" not in captured
    assert captured["owner_id"] is None
    assert captured["name"] == "Acme"


# ── Imports ─────────────────────────────────────────────────────────────────

def test_auto_created_companies_are_left_unassigned():
    """Not an oversight: a company created as a side effect of linking a contact was
    never something anyone chose to own, and NULL means exactly that."""
    import inspect

    src = inspect.getsource(crm.resolve_or_create_company_ids)
    assert "owner_id" not in src.split('"""')[-1], (
        "resolve_or_create_company_ids must not stamp an owner — see its comment"
    )


# ── Per-rep shaping ─────────────────────────────────────────────────────────

def test_per_rep_merges_ownership_and_actor_halves():
    pipeline = [
        {"user_id": ME, "name": "Ada", "email": "ada@x.test", "deals_open": 3,
         "open_value": 900.0, "deals_won": 2, "deals_lost": 1, "won_value": 400.0},
    ]
    activity = [
        {"user_id": ME, "name": "Ada", "email": "ada@x.test",
         "activity_count": 12, "records_touched": 5},
    ]
    rows = crm._shape_per_rep(pipeline, activity)
    assert len(rows) == 1
    assert rows[0]["deals_open"] == 3 and rows[0]["records_touched"] == 5


def test_a_rep_with_activity_but_no_owned_deals_still_appears():
    """Attribution is by actor, so someone working a colleague's records earns a row."""
    rows = crm._shape_per_rep(
        [],
        [{"user_id": COLLEAGUE, "name": "Bo", "email": "bo@x.test",
          "activity_count": 4, "records_touched": 3}],
    )
    assert [r["name"] for r in rows] == ["Bo"]
    assert rows[0]["deals_open"] == 0


def test_unattributed_work_is_surfaced_not_dropped():
    rows = crm._shape_per_rep(
        [{"user_id": None, "name": None, "email": None, "deals_open": 1,
          "open_value": 50.0, "deals_won": 0, "deals_lost": 0, "won_value": 0}],
        [{"user_id": None, "name": None, "email": None,
          "activity_count": 9, "records_touched": 2}],
    )
    assert len(rows) == 1
    assert rows[0]["name"] == "Unattributed"
    assert rows[0]["activity_count"] == 9


def test_unattributed_sorts_last():
    rows = crm._shape_per_rep(
        [],
        [
            {"user_id": None, "name": None, "email": None,
             "activity_count": 99, "records_touched": 99},
            {"user_id": ME, "name": "Ada", "email": "a@x.test",
             "activity_count": 1, "records_touched": 1},
        ],
    )
    assert [r["name"] for r in rows] == ["Ada", "Unattributed"]


def test_a_rep_with_no_name_falls_back_to_their_email():
    rows = crm._shape_per_rep(
        [], [{"user_id": 7, "name": "  ", "email": "ghost@x.test",
              "activity_count": 1, "records_touched": 1}]
    )
    assert rows[0]["name"] == "ghost@x.test"


def test_reps_are_ranked_on_records_touched_not_raw_count():
    """activity_count is inflatable by one bulk action; records_touched is not."""
    rows = crm._shape_per_rep(
        [],
        [
            {"user_id": 1, "name": "Bulk", "email": "b@x.test",
             "activity_count": 500, "records_touched": 1},
            {"user_id": 2, "name": "Real", "email": "r@x.test",
             "activity_count": 20, "records_touched": 18},
        ],
    )
    assert [r["name"] for r in rows] == ["Real", "Bulk"]


# ── The per-rep SQL's exclusions ────────────────────────────────────────────

def test_activity_sql_excludes_housekeeping_and_merge_copies():
    """Two chatter writers are not somebody's work: provenance confirmations (which
    insert directly, bypassing add_note) and merge_deals' copies (which leave the
    originals on the archived source, so both sides read archived = 0)."""
    assert crm._PROVENANCE_NOTE_PATTERN == "Confirmed AI-populated value for %"
    assert crm._MERGE_COPY_PATTERN == "[Merged from deal #%"


def test_like_patterns_are_bound_not_inlined(captured_sql):
    """A literal '%' inside a statement psycopg2 is given parameters for raises
    IndexError at execute time — a runtime 500 that no syntax check catches. Found
    exactly once, by booting the app against a real database; this keeps it found.

    The assertion is on the STATEMENT: every % in it must belong to a %s
    placeholder, and the patterns must arrive as parameters instead.
    """
    crm.get_analytics()
    activity_sql, params = next(
        (sql, p) for sql, p in captured_sql if "records_touched" in sql
    )
    assert "%" not in activity_sql.replace("%s", "")
    assert crm._PROVENANCE_NOTE_PATTERN in params
    assert crm._MERGE_COPY_PATTERN in params


def test_analytics_reports_per_rep(monkeypatch):
    monkeypatch.setattr(crm, "pg_fetchone", lambda sql, params=(): {})
    monkeypatch.setattr(crm, "pg_fetchall", lambda sql, params=(): [])
    assert crm.get_analytics()["per_rep"] == []


# ── An update must never invent an owner ────────────────────────────────────

def test_an_update_without_owner_id_leaves_ownership_alone(client, monkeypatch):
    """The server half of the "editing an unassigned record must not claim it" fix.

    The forms now omit owner_id on an untouched create and send the record's own
    value on an edit — but the route must also not synthesize one, or a client that
    simply doesn't know about ownership (an older tab, a script) would reassign
    every record it saves.
    """
    captured = {}
    monkeypatch.setattr(
        crm, "update_contact", lambda cid, **kw: captured.update(kw) or {"id": cid}
    )
    client.put("/api/crm/contacts/5", json={"name": "Renamed"})
    assert "owner_id" not in captured


@pytest.mark.parametrize(
    "path,service_fn",
    [
        ("/api/crm/contacts/5", "update_contact"),
        ("/api/crm/deals/5", "update_deal"),
        ("/api/crm/tasks/5", "update_task"),
        ("/api/crm/companies/5", "update_company"),
    ],
)
def test_no_entity_update_synthesizes_an_owner(client, monkeypatch, path, service_fn):
    captured = {}
    monkeypatch.setattr(
        crm, service_fn, lambda eid, **kw: captured.update(kw) or {"id": eid}
    )
    client.put(path, json={"name": "X", "title": "X"})
    assert "owner_id" not in captured
