"""CRM agent-tools contract: unconditional, complete, well-formed.

The issue requires the crm_* tools to be collected unconditionally (no enable
gate). This pins that: 22 schema defs, 23 executors (incl. the crm_log_note
back-compat alias), every def has an executor, and get_crm_tools() returns the
full set with no gating.
"""

import inspect

import psycopg2

from crm import service, tools
from crm.tools import CRM_TOOL_DEFS, TOOL_EXECUTORS, get_crm_tools


def test_twentytwo_defs_twentythree_executors():
    assert len(CRM_TOOL_DEFS) == 22
    assert len(TOOL_EXECUTORS) == 23


def test_company_tools_present():
    """The 5 company tools (issue #13) are all defined with executors."""
    names = {d["name"] for d in CRM_TOOL_DEFS}
    company_tools = {
        "crm_search_companies", "crm_get_company", "crm_list_companies",
        "crm_create_company", "crm_update_company",
    }
    assert company_tools <= names
    for name in company_tools:
        assert name in TOOL_EXECUTORS and callable(TOOL_EXECUTORS[name])


def test_contact_and_deal_tools_accept_company_id():
    """company_id is exposed on the create/update contact + deal tool schemas.

    Create tools take a plain integer; update tools accept null so an agent can
    unlink a contact/deal from its company (parity with the HTTP update path).
    """
    by_name = {d["name"]: d for d in CRM_TOOL_DEFS}
    for name in ("crm_create_contact", "crm_create_deal"):
        assert by_name[name]["input_schema"]["properties"]["company_id"]["type"] == "integer", name
    for name in ("crm_update_contact", "crm_update_deal"):
        assert by_name[name]["input_schema"]["properties"]["company_id"]["type"] == ["integer", "null"], name


def test_def_names_unique_prefixed_and_schema_shaped():
    names = [d["name"] for d in CRM_TOOL_DEFS]
    assert len(names) == len(set(names))  # unique
    for d in CRM_TOOL_DEFS:
        assert d["name"].startswith("crm_"), d["name"]
        assert d["kind"] == "integration"
        assert isinstance(d.get("description"), str) and d["description"]
        schema = d["input_schema"]
        assert schema["type"] == "object"
        assert isinstance(schema.get("properties"), dict)


def test_every_def_has_executor_and_alias_is_the_extra():
    def_names = {d["name"] for d in CRM_TOOL_DEFS}
    for name in def_names:
        assert name in TOOL_EXECUTORS, f"{name} has no executor"
        assert callable(TOOL_EXECUTORS[name])
    # the only executor without a schema def is the back-compat alias
    assert set(TOOL_EXECUTORS) == def_names | {"crm_log_note"}


def test_get_crm_tools_returns_full_set_unconditionally():
    defs, execs = get_crm_tools()
    assert defs is CRM_TOOL_DEFS
    assert execs is TOOL_EXECUTORS
    # No enable gate: the accessor takes no required args and never filters.
    sig = inspect.signature(get_crm_tools)
    assert not [p for p in sig.parameters.values()
                if p.default is inspect.Parameter.empty and p.kind in
                (p.POSITIONAL_OR_KEYWORD, p.POSITIONAL_ONLY)]


def test_no_enable_gate_in_source():
    src = inspect.getsource(tools)
    assert "is_enabled" not in src  # chatty's crm_lite gate must not survive the port


def test_company_tool_defs_declare_writes_flag():
    """The 5 company defs carry the writes flag (#4 convention): reads false, writes true."""
    by_name = {d["name"]: d for d in CRM_TOOL_DEFS}
    for name in ("crm_search_companies", "crm_get_company", "crm_list_companies"):
        assert by_name[name]["writes"] is False, name
    for name in ("crm_create_company", "crm_update_company"):
        assert by_name[name]["writes"] is True, name


# ── Company tool executors (the new UniqueViolation/blank-name/not-found branches) ──

def test_crm_create_company_translates_unique_violation(monkeypatch):
    def raise_unique(name, **kw):
        raise psycopg2.errors.UniqueViolation()
    monkeypatch.setattr(service, "create_company", raise_unique)
    out = tools.crm_create_company("Acme")
    assert out == {"error": "A company with that name already exists"}


def test_crm_create_company_rejects_blank_name(monkeypatch):
    called = []
    monkeypatch.setattr(service, "create_company", lambda name, **kw: called.append(name) or {"id": 1})
    assert tools.crm_create_company("   ") == {"error": "Name is required"}
    assert called == []  # service never reached


def test_crm_update_company_translates_unique_violation(monkeypatch):
    def raise_unique(cid, **kw):
        raise psycopg2.errors.UniqueViolation()
    monkeypatch.setattr(service, "update_company", raise_unique)
    assert tools.crm_update_company(1, name="Acme") == {"error": "A company with that name already exists"}


def test_crm_update_company_missing_returns_error(monkeypatch):
    monkeypatch.setattr(service, "update_company", lambda cid, **kw: None)
    assert tools.crm_update_company(999, name="X") == {"error": "Company 999 not found"}


def test_crm_get_company_missing_returns_error(monkeypatch):
    monkeypatch.setattr(service, "get_company_detail", lambda cid: None)
    assert tools.crm_get_company(999) == {"error": "Company 999 not found"}


def test_crm_search_and_list_companies_pass_through(monkeypatch):
    monkeypatch.setattr(service, "search_companies", lambda q, **kw: [{"id": 1}, {"id": 2}])
    monkeypatch.setattr(service, "list_companies", lambda **kw: {"companies": [{"id": 1}], "total": 1})
    assert tools.crm_search_companies("acme") == {"companies": [{"id": 1}, {"id": 2}], "count": 2}
    assert tools.crm_list_companies(status="active") == {"companies": [{"id": 1}], "total": 1}


def test_contact_deal_tools_translate_fk_violation(monkeypatch):
    """An invalid company_id (or contact_id) through the tool path returns a
    structured error rather than raising a raw psycopg2 error."""
    def raise_fk(*a, **kw):
        raise psycopg2.errors.ForeignKeyViolation()
    monkeypatch.setattr(service, "create_contact", raise_fk)
    monkeypatch.setattr(service, "update_contact", raise_fk)
    monkeypatch.setattr(service, "create_deal", raise_fk)
    monkeypatch.setattr(service, "update_deal", raise_fk)
    assert tools.crm_create_contact("Ana", company_id=999) == {"error": "Referenced company does not exist"}
    assert tools.crm_update_contact(1, company_id=999) == {"error": "Referenced company does not exist"}
    assert tools.crm_create_deal("D", company_id=999) == {"error": "Referenced contact or company does not exist"}
    assert tools.crm_update_deal(1, company_id=999) == {"error": "Referenced contact or company does not exist"}
