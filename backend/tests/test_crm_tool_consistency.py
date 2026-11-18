"""Shared Baker/MCP record results: missing ids, searches and links (#386)."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from core.config import settings
from crm import links, tools


@pytest.fixture(autouse=True)
def isolated_services(monkeypatch):
    monkeypatch.setattr(settings, "frontend_url_is_default", True)
    monkeypatch.setattr(tools, "_record_provenance", Mock())
    monkeypatch.setattr(tools, "pg_fetchall", lambda *a, **kw: [
        {"id": 5, "name": "Acme", "owner_id": None},
    ])


RELATED_READS = [
    ("crm_get_chatter", tools.chatter_service, "get_chatter", "notes", kind)
    for kind in ("contact", "company", "deal")
] + [
    (tool, tools.crm, service, key, kind)
    for tool, service, key in (
        ("crm_get_activity_log", "get_activity_log", "activities"),
        ("crm_list_todos", "list_todos", "todos"),
    )
    for kind in ("contact", "deal")
]


@pytest.mark.parametrize("tool,module,service,key,kind", RELATED_READS)
@pytest.mark.parametrize("exists,rows", [(False, []), (True, []), (False, [{"id": 9}])])
def test_related_reads(monkeypatch, tool, module, service, key, kind, exists, rows):
    monkeypatch.setattr(module, service, lambda *a, **kw: rows)
    check = Mock(return_value=exists)
    monkeypatch.setattr(tools.field_service, "entity_exists", check)
    args = {"entity_type": kind, "entity_id": 5} if key == "notes" else {f"{kind}_id": 5}
    result = tools.TOOL_EXECUTORS[tool](**args)
    if not rows and not exists:
        assert result == {"error": f"{kind.capitalize()} 5 not found"}
    else:
        assert result == {key: rows, "count": len(rows)}
    if rows:
        check.assert_not_called()
    else:
        check.assert_called_once_with(kind, 5)


@pytest.mark.parametrize("tool,service", [
    ("crm_get_activity_log", "get_activity_log"), ("crm_list_todos", "list_todos"),
])
def test_related_reads_check_both_filters_in_order(monkeypatch, tool, service):
    monkeypatch.setattr(tools.crm, service, lambda **kw: [])
    check = Mock(side_effect=[True, False])
    monkeypatch.setattr(tools.field_service, "entity_exists", check)
    assert tools.TOOL_EXECUTORS[tool](deal_id=5, contact_id=6) == {"error": "Contact 6 not found"}
    assert [call.args for call in check.call_args_list] == [("deal", 5), ("contact", 6)]
    check.reset_mock()
    assert "error" not in tools.TOOL_EXECUTORS[tool]()
    check.assert_not_called()


SEARCHES = [("crm_find_contact", "search_contacts", "contacts"),
            ("crm_search_companies", "search_companies", "companies")]


@pytest.mark.parametrize("tool,service,key", SEARCHES)
@pytest.mark.parametrize("query", ["", " \t\n", None])
def test_blank_search_refused(monkeypatch, tool, service, key, query):
    search = Mock()
    monkeypatch.setattr(tools.crm, service, search)
    result = tools.TOOL_EXECUTORS[tool](query=query)
    assert "Give a search term" in result["error"]
    assert f"crm_list_{key}" in result["error"]
    search.assert_not_called()


@pytest.mark.parametrize("tags", ["", " ", ", ,\t,"])
def test_blank_tags_do_not_allow_blank_search(monkeypatch, tags):
    search = Mock()
    monkeypatch.setattr(tools.crm, "search_contacts", search)
    assert "error" in tools.crm_find_contact(" ", tags=tags)
    search.assert_not_called()


@pytest.mark.parametrize("tool,service,key", SEARCHES)
@pytest.mark.parametrize("filters", [{"status": "active"}, {"owner_id": 7}, {"owner_id": "unassigned"}])
def test_filtered_blank_search(monkeypatch, tool, service, key, filters):
    search = Mock(return_value=[])
    monkeypatch.setattr(tools.crm, service, search)
    assert tools.TOOL_EXECUTORS[tool](query=" \t", **filters) == {key: [], "count": 0}
    assert search.call_args.args == ("",)
    for name, value in filters.items():
        assert search.call_args.kwargs[name] == value


def test_tag_search_and_trimmed_query(monkeypatch):
    search = Mock(return_value=[])
    monkeypatch.setattr(tools.crm, "search_contacts", search)
    assert tools.crm_find_contact(" ", tags=" , vip, ") == {"contacts": [], "count": 0}
    assert search.call_args.args == ("",)
    assert search.call_args.kwargs["tags"] == " , vip, "


@pytest.mark.parametrize("tool,service,key", SEARCHES)
def test_search_trims_query(monkeypatch, tool, service, key):
    search = Mock(return_value=[])
    monkeypatch.setattr(tools.crm, service, search)
    assert tools.TOOL_EXECUTORS[tool](query=" Acme ") == {key: [], "count": 0}
    assert search.call_args.args == ("Acme",)


CONTACT = {"id": 5, "name": "Ana Lopez", "title": "Buyer"}
COMPANY = {"id": 6, "name": "Acme"}
MERGE = {"survivor": {"id": 5, "name": "Acme"}, "absorbed": COMPANY}
# Each case specifies real service payload paths; omitted links cannot hide behind a URL-based walk.
URL_CASES = {
    "crm_find_contact": (tools.crm, "search_contacts", {"query": "Ana"}, [CONTACT], [("contacts", 0)]),
    "crm_list_contacts": (tools.crm, "list_contacts", {}, {"contacts": [CONTACT]}, [("contacts", 0)]),
    "crm_get_contact": (tools.crm, "get_contact_detail", {"contact_id": 5}, CONTACT, [()]),
    "crm_create_contact": (tools.crm, "create_contact", {"name": "Ana Lopez"}, CONTACT, [(), ("target",)]),
    "crm_update_contact": (tools.crm, "update_contact", {"contact_id": 5}, CONTACT, [(), ("target",)]),
    "crm_search_companies": (tools.crm, "search_companies", {"query": "Acme"}, [COMPANY], [("companies", 0)]),
    "crm_list_companies": (tools.crm, "list_companies", {}, {"companies": [COMPANY]}, [("companies", 0)]),
    "crm_get_company": (tools.crm, "get_company_detail", {"company_id": 6},
                        {**COMPANY, "contacts": [CONTACT]}, [(), ("contacts", 0)]),
    "crm_create_company": (tools.crm, "create_company", {"name": "Acme"}, COMPANY, [(), ("target",)]),
    "crm_update_company": (tools.crm, "update_company", {"company_id": 6}, COMPANY, [(), ("target",)]),
    "crm_get_contact_staleness": (tools.analytics_service, "get_contact_staleness", {},
                                 {"contacts": [CONTACT]}, [("contacts", 0)]),
    "crm_find_duplicates": (tools.analytics_service, "find_duplicates", {},
                            {"contacts": [{"records": [CONTACT]}], "companies": [{"records": [COMPANY]}]},
                            [("contacts", 0, "records", 0), ("companies", 0, "records", 0)]),
    "crm_scan_gaps": (tools.analytics_service, "scan_gaps", {},
                      {"contacts": [CONTACT], "companies": [COMPANY]}, [("contacts", 0), ("companies", 0)]),
    "crm_preview_company_merge": (tools.company_merge_service, "plan_merge", {"survivor_id": 5, "absorbed_id": 6},
                                  MERGE, [("survivor",), ("absorbed",)]),
    "crm_merge_companies": (tools.company_merge_service, "merge_companies", {"survivor_id": 5, "absorbed_id": 6},
                            MERGE, [("plan", "survivor"), ("plan", "absorbed"), ("target",)]),
}


@pytest.mark.parametrize("tool", sorted(tools.CRM_RECORD_URL_TOOLS))
@pytest.mark.parametrize("base", ["", "https://crm.example.test"])
def test_every_record_tool_returns_links(monkeypatch, tool, base):
    monkeypatch.setattr(settings, "frontend_url_is_default", not base)
    monkeypatch.setattr(settings, "frontend_url", base + "/")
    module, service, args, payload, paths = URL_CASES[tool]
    monkeypatch.setattr(module, service, lambda *a, **kw: deepcopy(payload))
    result = tools.TOOL_EXECUTORS[tool](**args)
    for path in paths:
        record = result
        for key in path:
            record = record[key]
        kind = "contacts" if record.get("name", record.get("title")) in {"Ana Lopez", "Buyer"} else "companies"
        assert record["url"] == f"{base}/crm/{kind}/{record['id']}"
    definition = next(d for d in tools.CRM_TOOL_DEFS if d["name"] == tool)
    assert tools.CRM_RECORD_URL_GUIDANCE in definition["description"]
    assert links.deal_records(result) == {}


def test_record_guidance_is_complete_and_idempotent():
    assert set(URL_CASES) == tools.CRM_RECORD_URL_TOOLS
    before = deepcopy(tools.CRM_TOOL_DEFS)
    tools._apply_deal_url_guidance()
    assert tools.CRM_TOOL_DEFS == before


@pytest.mark.parametrize("kind", ["contact", "company"])
@pytest.mark.parametrize("record", [None, {}, {"error": "missing"}, {"id": True}, {"id": "5"}])
def test_record_links_ignore_missing_or_invalid_ids(kind, record):
    before = deepcopy(record)
    assert links.with_record_url(kind, record) is record
    assert record == before


@pytest.mark.parametrize("kind", ["contact", "company", "deal"])
@pytest.mark.parametrize("operation", ["get", "set"])
def test_custom_field_not_found(monkeypatch, kind, operation):
    monkeypatch.setattr(tools.field_service, "entity_exists", lambda *a: False)
    args = {f"{kind}_id": 5}
    if operation == "set":
        args["fields"] = {"tier": "gold"}
    assert tools.TOOL_EXECUTORS[f"crm_{operation}_{kind}_fields"](**args) == {
        "error": f"{kind.capitalize()} 5 not found",
    }


@pytest.mark.parametrize("kind", ["contact", "deal"])
def test_lead_score_not_found(monkeypatch, kind):
    monkeypatch.setattr(tools.scoring_service, f"score_{kind}", lambda *a: None)
    monkeypatch.setattr(tools.crm, f"get_{kind}", lambda *a: None)
    assert tools.crm_get_lead_score(kind, 5) == {"error": f"{kind.capitalize()} 5 not found"}


def test_bulk_move_guidance_has_mcp_fallback():
    definition = next(d for d in tools.CRM_TOOL_DEFS if d["name"] == "crm_bulk_move_deals")
    assert "if it is available to you, otherwise crm_update_deal for each deal" in definition["description"]
