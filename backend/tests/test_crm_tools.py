"""CRM agent-tools contract: unconditional, complete, well-formed.

The issue requires the crm_* tools to be collected unconditionally (no enable
gate). This pins that: 40 schema defs, 41 executors (incl. the crm_log_note
back-compat alias), every def has an executor, and get_crm_tools() returns the
full set with no gating.
"""

import inspect

import psycopg2

from crm import chatter_service, field_service, service, tools
from crm.tools import CRM_TOOL_DEFS, TOOL_EXECUTORS, get_crm_tools


def test_def_and_executor_counts():
    # Absolute counts. ⚠ TOOL-COUNT SUM RULE (coach #67): concurrent sibling issues
    # may add crm_* tools in the same auto-issues run. If so, this is 24 (base) +
    # 6 (#19 custom fields) + 1 (#20 crm_analytics) + 9 (#22 Casey parity: search_deals,
    # mark_deal_won/lost, archive_deal, merge_deals, get_stale_deals,
    # get_contact_staleness, find_duplicates, scan_gaps) + N (sibling additions) — SUM
    # the additions, never overwrite the number. The executor count is always defs + 1
    # (crm_log_note alias).
    assert len(CRM_TOOL_DEFS) == 40
    assert len(TOOL_EXECUTORS) == 41
    # Relative invariant (robust to any future additions): exactly one alias-only executor.
    assert len(TOOL_EXECUTORS) == len(CRM_TOOL_DEFS) + 1


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


# ── writes flags (issue #4 — the assistant confirmation-gate linchpin) ────────
# INVARIANT, not a count: every def (including ones later issues append) must
# self-classify as a write or a read, or the confirmation gate silently lets a
# mutation through. Deliberately no exact-count / exact-set assertions here so
# sibling issues adding tools (companies, chatter) pass through untouched.

_OWNED_WRITE_TOOLS = {
    "crm_create_contact", "crm_update_contact", "crm_delete_contact",
    "crm_create_deal", "crm_update_deal", "crm_update_deal_stage",
    "crm_log_activity", "crm_create_task", "crm_complete_task",
    "crm_set_contact_fields", "crm_set_company_fields", "crm_set_deal_fields",
}
_OWNED_READ_TOOLS = {
    "crm_find_contact", "crm_get_contact", "crm_list_contacts", "crm_get_pipeline",
    "crm_get_deal", "crm_get_activity_log", "crm_list_tasks", "crm_dashboard",
    "crm_analytics",
    "crm_get_contact_fields", "crm_get_company_fields", "crm_get_deal_fields",
}


def test_every_def_declares_a_boolean_writes_flag():
    """Fail loud if ANY tool def is missing a boolean ``writes`` — a missing flag
    defaults to read-only and would bypass the assistant's confirmation gate."""
    missing = [d["name"] for d in CRM_TOOL_DEFS if not isinstance(d.get("writes"), bool)]
    assert not missing, f"tool defs missing a boolean 'writes' flag: {missing}"


def test_owned_write_and_read_tools_are_classified_correctly():
    writes = {d["name"]: d.get("writes") for d in CRM_TOOL_DEFS}
    for name in _OWNED_WRITE_TOOLS:
        assert writes.get(name) is True, f"{name} must be writes=True"
    for name in _OWNED_READ_TOOLS:
        assert writes.get(name) is False, f"{name} must be writes=False"


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


def test_chatter_tools_present_and_shaped():
    by_name = {d["name"]: d for d in CRM_TOOL_DEFS}
    assert {"crm_add_note", "crm_get_chatter"} <= set(by_name)
    for name in ("crm_add_note", "crm_get_chatter"):
        props = by_name[name]["input_schema"]["properties"]
        # 'company' joined in issue #22. The tool enum must track the service's
        # CHATTER_ENTITY_TYPES exactly, or the model is told about a type the service
        # rejects (or denied one it accepts).
        assert set(props["entity_type"]["enum"]) == set(chatter_service.CHATTER_ENTITY_TYPES)
    assert by_name["crm_add_note"]["input_schema"]["required"] == ["entity_type", "entity_id", "message"]


def test_chatter_executors_wrap_validation_errors():
    # A bad entity_type is rejected in the service before any DB call; the tool
    # surfaces it as {"error": ...} rather than raising.
    assert "error" in tools.crm_add_note("invoice", 1, "hi")
    assert "error" in tools.crm_get_chatter("invoice", 1)


def test_chatter_executors_happy_path_shapes(monkeypatch):
    # The tool return shape is the contract the assistant engine consumes; pin it.
    from crm import chatter_service
    monkeypatch.setattr(chatter_service, "add_note", lambda t, i, m: {"id": 1, "message": m})
    monkeypatch.setattr(chatter_service, "get_chatter", lambda *a, **k: [{"id": 1}, {"id": 2}])
    assert tools.crm_add_note("deal", 3, "hi") == {"ok": True, "note": {"id": 1, "message": "hi"}}
    assert tools.crm_get_chatter("deal", 3) == {"notes": [{"id": 1}, {"id": 2}], "count": 2}


# ── Custom-field tools (issue #19) ────────────────────────────────────────────

def test_field_tools_present_and_shaped():
    by_name = {d["name"]: d for d in CRM_TOOL_DEFS}
    get_tools = {"crm_get_contact_fields", "crm_get_company_fields", "crm_get_deal_fields"}
    set_tools = {"crm_set_contact_fields", "crm_set_company_fields", "crm_set_deal_fields"}
    assert (get_tools | set_tools) <= set(by_name)
    for name in get_tools:
        assert by_name[name]["writes"] is False, name
        assert by_name[name]["input_schema"]["required"] == [], name  # id is optional
    for name in set_tools:
        assert by_name[name]["writes"] is True, name
        props = by_name[name]["input_schema"]["properties"]
        assert "fields" in props and props["fields"]["type"] == "object", name
        req = by_name[name]["input_schema"]["required"]
        assert "fields" in req and any(k.endswith("_id") for k in req), name
    # None of the set tools expose a spoofable user_email param.
    for name in set_tools:
        assert "user_email" not in by_name[name]["input_schema"]["properties"], name


def test_get_fields_no_id_lists_definitions(monkeypatch):
    monkeypatch.setattr(field_service, "list_field_definitions", lambda et: [
        {"id": 7, "field_key": "tier", "name": "Tier", "field_type": "select",
         "is_required": 1, "dropdown_options": ["A", "B"]},
    ])
    out = tools.crm_get_contact_fields()
    assert out["total"] == 1
    field = out["fields"][0]
    assert field == {"field_id": 7, "field_key": "tier", "name": "Tier",
                     "field_type": "select", "is_required": True, "options": ["A", "B"]}


def test_get_fields_with_missing_entity_returns_error(monkeypatch):
    monkeypatch.setattr(field_service, "entity_exists", lambda et, eid: False)
    assert tools.crm_get_contact_fields(999) == {"error": "contact 999 not found"}


def test_get_fields_with_id_returns_normalized_values(monkeypatch):
    monkeypatch.setattr(field_service, "entity_exists", lambda et, eid: True)
    monkeypatch.setattr(field_service, "get_field_values", lambda et, eid: [
        {"field_id": 1, "field_key": "tier", "name": "Tier", "field_type": "select",
         "dropdown_options": ["A", "B"], "is_required": 1, "value": "A",
         "value_updated_at": "t", "updated_by_email": "u"},
    ])
    out = tools.crm_get_deal_fields(5)
    # Same normalized schema shape as the no-id path (is_required bool, 'options'),
    # plus the per-entity value fields.
    assert out == {"fields": [{
        "field_id": 1, "field_key": "tier", "name": "Tier", "field_type": "select",
        "is_required": True, "options": ["A", "B"], "value": "A",
        "value_updated_at": "t", "updated_by_email": "u",
    }], "total": 1}


def test_set_fields_normalizes_and_reports_unknown(monkeypatch):
    monkeypatch.setattr(field_service, "entity_exists", lambda et, eid: True)
    monkeypatch.setattr(field_service, "list_field_definitions",
                        lambda et: [{"id": 9, "field_key": "vip"}])
    captured = {}
    monkeypatch.setattr(field_service, "set_field_values",
                        lambda et, eid, vals, email: captured.update(
                            et=et, eid=eid, vals=vals, email=email) or {"ok": True, "updated": 1, "errors": []})
    out = tools.crm_set_contact_fields(5, {"vip": True, "ghost": "x"})
    # bool normalized to "1"; unknown key reported, not sent; attribution hardcoded.
    assert captured["vals"] == {"9": "1"}
    assert captured["email"] == "assistant"
    assert out["unknown_keys"] == ["ghost"]


def test_set_fields_rejects_none_with_clear_hint(monkeypatch):
    monkeypatch.setattr(field_service, "entity_exists", lambda et, eid: True)
    monkeypatch.setattr(field_service, "list_field_definitions", lambda et: [{"id": 9, "field_key": "vip"}])
    # Only a None value → nothing valid to set → single error mentioning the clear contract.
    out = tools.crm_set_contact_fields(5, {"vip": None})
    assert "error" in out and "empty string to clear" in out["error"]


def test_set_fields_translates_service_valueerror(monkeypatch):
    monkeypatch.setattr(field_service, "entity_exists", lambda et, eid: True)
    monkeypatch.setattr(field_service, "list_field_definitions", lambda et: [{"id": 9, "field_key": "amount"}])
    def raise_ve(*a, **k):
        raise ValueError("Field 'Amount' requires a number, got 'abc'")
    monkeypatch.setattr(field_service, "set_field_values", raise_ve)
    out = tools.crm_set_deal_fields(5, {"amount": "abc"})
    assert out == {"error": "Field 'Amount' requires a number, got 'abc'"}


def test_set_fields_missing_entity_returns_error(monkeypatch):
    monkeypatch.setattr(field_service, "entity_exists", lambda et, eid: False)
    assert tools.crm_set_contact_fields(999, {"vip": "1"}) == {"error": "contact 999 not found"}


def test_set_fields_empty_map_returns_error():
    assert tools.crm_set_contact_fields(5, {}) == {"error": "No fields provided"}


def test_set_fields_empty_string_clear_forwards(monkeypatch):
    monkeypatch.setattr(field_service, "entity_exists", lambda et, eid: True)
    monkeypatch.setattr(field_service, "list_field_definitions", lambda et: [{"id": 9, "field_key": "vip"}])
    captured = {}
    monkeypatch.setattr(field_service, "set_field_values",
                        lambda et, eid, vals, email: captured.update(vals=vals) or {"ok": True, "updated": 1, "errors": []})
    tools.crm_set_contact_fields(5, {"vip": ""})
    assert captured["vals"] == {"9": ""}   # empty string forwarded (clears downstream)


# ── Deal lifecycle + sales intelligence (issue #22) ──────────────────────────

_LIFECYCLE_TOOLS = {
    "crm_search_deals": False,
    "crm_mark_deal_won": True,
    "crm_mark_deal_lost": True,
    "crm_archive_deal": True,
    "crm_merge_deals": True,
}
_INTELLIGENCE_TOOLS = {
    "crm_get_stale_deals", "crm_get_contact_staleness",
    "crm_find_duplicates", "crm_scan_gaps",
}


def test_issue22_tools_present_with_correct_writes_flags():
    writes = {d["name"]: d["writes"] for d in CRM_TOOL_DEFS}
    for name, expected in _LIFECYCLE_TOOLS.items():
        assert writes.get(name) is expected, f"{name} writes flag"
        assert callable(TOOL_EXECUTORS.get(name)), f"{name} executor"
    for name in _INTELLIGENCE_TOOLS:
        assert writes.get(name) is False, f"{name} must be a read"
        assert callable(TOOL_EXECUTORS.get(name)), f"{name} executor"


def test_intelligence_reads_are_available_to_the_background_turn():
    """The proactive heartbeat's allowlist is DERIVED from the writes flags (reads +
    notify_user). These four reads exist so the heartbeat can find stale work, so
    flipping any of them to writes=True would silently disable that — pin it."""
    from assistant.background import background_allowlist
    from assistant.registry import ToolRegistry

    allowed = background_allowlist(ToolRegistry())
    assert _INTELLIGENCE_TOOLS <= allowed
    # ...and none of the destructive lifecycle verbs may ever be background-callable.
    assert not ({n for n, w in _LIFECYCLE_TOOLS.items() if w} & allowed)


def test_get_pipeline_tool_caps_the_list_but_not_the_totals(monkeypatch):
    deals = [{"id": i, "stage": "lead"} for i in range(10)]
    deals += [{"id": 100 + i, "stage": "won"} for i in range(3)]
    monkeypatch.setattr(service, "get_pipeline", lambda stage=None: {
        "deals": deals,
        "stage_summary": [{"stage": "lead", "count": 10, "total_value": 999}],
        "total_pipeline_value": 999,
    })
    out = tools.crm_get_pipeline(limit_per_stage=2)
    assert [d["id"] for d in out["deals"]] == [0, 1, 100, 101]  # 2 per stage
    assert out["deals_truncated"] is True
    # Counts and value are computed over EVERY deal — trimming the list must not lie.
    assert out["stage_summary"][0]["count"] == 10
    assert out["total_pipeline_value"] == 999


def test_get_pipeline_tool_reports_no_truncation_when_it_fits(monkeypatch):
    monkeypatch.setattr(service, "get_pipeline", lambda stage=None: {
        "deals": [{"id": 1, "stage": "lead"}], "stage_summary": [], "total_pipeline_value": 0,
    })
    assert tools.crm_get_pipeline()["deals_truncated"] is False


def test_search_deals_tool_rejects_a_non_map_filter():
    assert "error" in tools.crm_search_deals(custom_field_filters=["region"])


def test_merge_deals_tool_wraps_validation_errors(monkeypatch):
    def boom(t, s):
        raise ValueError("Cannot merge a deal into itself")
    monkeypatch.setattr(service, "merge_deals", boom)
    assert tools.crm_merge_deals(1, 1) == {"error": "Cannot merge a deal into itself"}


def test_lifecycle_tools_report_a_missing_deal(monkeypatch):
    monkeypatch.setattr(service, "mark_deal_won", lambda d: None)
    monkeypatch.setattr(service, "mark_deal_lost", lambda d, lost_reason="": None)
    monkeypatch.setattr(service, "archive_deal", lambda d, archived=True: None)
    assert "error" in tools.crm_mark_deal_won(9)
    assert "error" in tools.crm_mark_deal_lost(9)
    assert "error" in tools.crm_archive_deal(9)


def test_won_and_lost_record_provenance(monkeypatch):
    """These executors are reached only through the assistant, so a successful write
    IS an 'AI set this field' event — same contract as crm_update_deal."""
    from crm import provenance_service
    recorded = []
    monkeypatch.setattr(service, "mark_deal_won", lambda d: {"id": d, "stage": "won", "probability": 100})
    monkeypatch.setattr(provenance_service, "record_fields",
                        lambda et, eid, fields: recorded.append((et, eid, sorted(fields))))
    tools.crm_mark_deal_won(4)
    assert recorded == [("deal", 4, ["probability", "stage"])]


def test_bounded_limit_clamps_model_supplied_values():
    assert tools._bounded_limit(0) == 1
    assert tools._bounded_limit(10_000) == 100
    assert tools._bounded_limit("many") == 20
    assert tools._bounded_limit(None, default=5) == 5


def test_find_contact_and_search_companies_pass_the_limit_through(monkeypatch):
    seen = {}

    def fake_contacts(q, status=None, tags=None, limit=20):
        seen["c"] = limit
        return []

    def fake_companies(q, status=None, limit=20):
        seen["co"] = limit
        return []

    monkeypatch.setattr(service, "search_contacts", fake_contacts)
    monkeypatch.setattr(service, "search_companies", fake_companies)
    tools.crm_find_contact("a", limit=5)
    tools.crm_search_companies("b", limit=9999)
    assert seen == {"c": 5, "co": 100}
