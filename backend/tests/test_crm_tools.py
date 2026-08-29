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
    # 6 (#19 custom fields) + 2 (#18 lead scores) + 1 (#20 crm_analytics) + 9 (#22
    # Casey parity: search_deals, mark_deal_won/lost, archive_deal, merge_deals,
    # get_stale_deals, get_contact_staleness, find_duplicates, scan_gaps) + 2 (#22
    # Phase 2: get_deal_health, get_pipeline_analytics) + 2 (#70 task parity:
    # crm_update_task, crm_delete_task) + 1 (#55 crm_bulk_move_deals) + N (sibling
    # additions) — SUM the additions, never overwrite the number. On rebase behind a
    # sibling that also adds a tool, recompute cumulative (do NOT keep-both a single
    # number). The executor count is always defs + 1 (crm_log_note alias).
    assert len(CRM_TOOL_DEFS) == 47
    assert len(TOOL_EXECUTORS) == 48
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


def test_get_crm_tools_has_no_enable_gate(task_mode):
    """The CRM tools are always on — there is no per-integration enable flag.

    The mode is pinned explicitly because #70 made the def list conditional on ONE
    thing (GTD hides the five task tools) and #102 made GTD the fail-safe answer with
    no database. Before that this test read the full list by accident of the old
    'normal' fallback; asking for normal mode is what it always meant.
    """
    task_mode("normal")
    defs, execs = get_crm_tools()
    assert defs is CRM_TOOL_DEFS
    assert execs is TOOL_EXECUTORS
    # No enable gate: the accessor takes no required args.
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
    "crm_update_task", "crm_delete_task",  # #70
    "crm_set_contact_fields", "crm_set_company_fields", "crm_set_deal_fields",
    "crm_recompute_lead_scores",  # #18
    "crm_bulk_move_deals",  # #55
}
_OWNED_READ_TOOLS = {
    "crm_find_contact", "crm_get_contact", "crm_list_contacts", "crm_get_pipeline",
    "crm_get_deal", "crm_get_activity_log", "crm_list_tasks", "crm_dashboard",
    "crm_analytics",
    "crm_get_contact_fields", "crm_get_company_fields", "crm_get_deal_fields",
    "crm_get_lead_score",  # #18
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


# ── crm_bulk_move_deals (#55) ────────────────────────────────────────────────

def _refuse_service(monkeypatch):
    """Make the service a tripwire: these guards must answer before any DB work."""
    def explode(*a, **k):
        raise AssertionError("the executor reached the service with a refused argument")
    monkeypatch.setattr(service, "bulk_move_deals", explode)


def test_bulk_move_rejects_an_empty_id_list(monkeypatch):
    _refuse_service(monkeypatch)
    assert tools.crm_bulk_move_deals([], "qualified") == {"error": "No deal IDs provided"}
    assert tools.crm_bulk_move_deals(None, "qualified") == {"error": "No deal IDs provided"}


def test_bulk_move_rejects_non_positive_and_non_integer_ids(monkeypatch):
    _refuse_service(monkeypatch)
    for bad in ([0], [-3], ["7"], [1.5], [None]):
        assert tools.crm_bulk_move_deals(bad, "qualified") == {
            "error": "deal_ids must be positive integers"
        }


def test_bulk_move_rejects_booleans_that_masquerade_as_ints(monkeypatch):
    """isinstance(True, int) is True, so an unguarded check would move deal 1."""
    _refuse_service(monkeypatch)
    assert "error" in tools.crm_bulk_move_deals([True, 2], "qualified")


def test_bulk_move_records_provenance_for_every_moved_deal(monkeypatch):
    """crm_update_deal_stage badges the stage it wrote; bulk must too, or the
    'an AI wrote this' audit misses the surface that changes the most records."""
    from crm import provenance_service
    recorded = []
    monkeypatch.setattr(service, "bulk_move_deals",
                        lambda ids, stage: {"ok": True, "updated": 2,
                                            "updated_ids": [4, 9], "errors": []})
    monkeypatch.setattr(provenance_service, "record_fields",
                        lambda et, eid, fields: recorded.append((et, eid, sorted(fields))))
    tools.crm_bulk_move_deals([4, 9], "proposal")
    assert recorded == [("deal", 4, ["stage"]), ("deal", 9, ["stage"])]


def test_bulk_move_badges_only_the_deals_that_actually_moved(monkeypatch):
    from crm import provenance_service
    recorded = []
    monkeypatch.setattr(service, "bulk_move_deals",
                        lambda ids, stage: {"ok": True, "updated": 1, "updated_ids": [4],
                                            "errors": ["Deal 9 not found"]})
    monkeypatch.setattr(provenance_service, "record_fields",
                        lambda et, eid, fields: recorded.append((et, eid)))
    tools.crm_bulk_move_deals([4, 9], "proposal")
    assert recorded == [("deal", 4)]


def test_bulk_move_passes_a_service_refusal_through_verbatim(monkeypatch):
    """An ok:false body is already a renderable sentence — the executor must not
    reshape it into a provenance write or a different error key."""
    from crm import provenance_service
    refusal = {"ok": False, "updated": 0, "updated_ids": [],
               "errors": ["Invalid stage: nonsense"]}
    monkeypatch.setattr(service, "bulk_move_deals", lambda ids, stage: refusal)
    monkeypatch.setattr(provenance_service, "record_fields",
                        lambda *a: (_ for _ in ()).throw(AssertionError("badged a refusal")))
    assert tools.crm_bulk_move_deals([1], "nonsense") == refusal


def test_bulk_move_dedupes_ids_before_calling_the_service(monkeypatch):
    seen = []
    monkeypatch.setattr(service, "bulk_move_deals",
                        lambda ids, stage: seen.append(ids) or {"ok": True, "updated": 0,
                                                               "updated_ids": [], "errors": []})
    tools.crm_bulk_move_deals([9, 5, 9], "qualified")
    assert seen == [[9, 5]]


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


def test_archive_tool_parses_booleans_strictly(monkeypatch):
    """A model sending the STRING "false" must restore, not archive — bool("false")
    is True, so a naive cast would do the destructive thing on a restore request."""
    seen = []
    monkeypatch.setattr(service, "archive_deal",
                        lambda d, archived=True: seen.append(archived) or {"id": d})
    tools.crm_archive_deal(1, archived="false")
    tools.crm_archive_deal(1, archived="true")
    tools.crm_archive_deal(1, archived=False)
    tools.crm_archive_deal(1)
    assert seen == [False, True, False, True]
    # Anything ambiguous is refused rather than guessed at.
    assert "error" in tools.crm_archive_deal(1, archived="maybe")
    assert len(seen) == 4  # the service was never reached for the bad value


def test_archive_tool_rejects_ambiguous_integers(monkeypatch):
    """0/1 are unambiguous; 2 or -1 are not, and this flag decides whether a deal
    disappears from every view."""
    monkeypatch.setattr(service, "archive_deal", lambda d, archived=True: {"id": d})
    assert tools.crm_archive_deal(1, archived=1)["archived"] is True
    assert tools.crm_archive_deal(1, archived=0)["archived"] is False
    assert "error" in tools.crm_archive_deal(1, archived=2)
    assert "error" in tools.crm_archive_deal(1, archived=-1)


def test_mark_deal_lost_badges_an_assistant_written_reason(monkeypatch):
    """Why a deal was lost is a judgement the assistant made — it feeds win/loss
    review, so it should carry a badge until a human confirms it."""
    from crm import provenance_service
    recorded = []
    monkeypatch.setattr(service, "mark_deal_lost",
                        lambda d, lost_reason="": {"id": d, "stage": "lost",
                                                   "probability": 0,
                                                   "lost_reason": lost_reason})
    monkeypatch.setattr(provenance_service, "record_fields",
                        lambda et, eid, fields: recorded.append(sorted(fields)))
    tools.crm_mark_deal_lost(4, lost_reason="chose a competitor")
    assert recorded == [["lost_reason", "probability", "stage"]]
    assert "lost_reason" in provenance_service.PROVENANCE_FIELDS["deal"]


def test_pipeline_and_search_tools_project_the_payload(monkeypatch):
    """The service returns d.* for the UI (notes, ai_touch_*, timestamps). Forwarding
    all of it costs ~27k tokens for a 150-deal board and buys nothing — the model can
    always crm_get_deal for the full record."""
    fat = {
        "id": 1, "title": "T", "stage": "lead", "value": 10, "currency": "USD",
        "probability": 20, "expected_close_date": "", "contact_id": None,
        "contact_name": None, "company_id": None, "company_name": None,
        "last_activity_at": None, "archived_at": None,
        # noise the model never needs:
        "notes": "x" * 5000, "created_at": "t", "updated_at": "t",
        "ai_touch_count": 3, "ai_touch_count_at": "t", "ai_touch_evidence_count": 2,
        "lost_reason": "",
    }
    monkeypatch.setattr(service, "get_pipeline", lambda stage=None: {
        "deals": [dict(fat)], "stage_summary": [], "total_pipeline_value": 0})
    monkeypatch.setattr(service, "search_deals", lambda **kw: [dict(fat)])
    monkeypatch.setattr(field_service, "list_field_definitions", lambda et: [])

    for payload in (tools.crm_get_pipeline()["deals"][0],
                    tools.crm_search_deals()["deals"][0]):
        assert set(payload) <= set(tools._DEAL_SUMMARY_FIELDS) | {"custom_fields"}
        assert "notes" not in payload and "ai_touch_count" not in payload
        assert payload["title"] == "T"   # the useful fields survive


def test_search_reports_an_unknown_custom_field_key(monkeypatch):
    """Without this the model says 'no deals match' when the field doesn't exist —
    a wrong answer, not a missing one."""
    monkeypatch.setattr(service, "search_deals", lambda **kw: [])
    monkeypatch.setattr(field_service, "list_field_definitions",
                        lambda et: [{"field_key": "region"}])
    out = tools.crm_search_deals(custom_field_filters={"region": "n", "nope": "x"})
    assert out["unknown_field_keys"] == ["nope"]
    assert "unknown_field_keys" not in tools.crm_search_deals(
        custom_field_filters={"Region": "n"})   # case-insensitive, so not unknown


def test_search_can_be_asked_for_archived_deals(monkeypatch):
    seen = {}

    def fake(**kw):
        seen.update(kw)
        return []
    monkeypatch.setattr(service, "search_deals", fake)
    monkeypatch.setattr(field_service, "list_field_definitions", lambda et: [])
    tools.crm_search_deals(search="junk", include_archived="true")
    assert seen["include_archived"] is True
    assert "error" in tools.crm_search_deals(include_archived="perhaps")


def test_stage_tool_no_longer_advertises_closing():
    """Tool descriptions are read at call time and beat a prompt block hundreds of
    tokens earlier — this one used to say 'quick way to close a deal', steering the
    model past the lifecycle verbs that capture the reason."""
    by_name = {d["name"]: d for d in CRM_TOOL_DEFS}
    for name in ("crm_update_deal_stage", "crm_bulk_move_deals"):
        stage_tool = by_name[name]
        assert "crm_mark_deal_won" in stage_tool["description"], name
        assert "won" not in stage_tool["input_schema"]["properties"]["stage"]["description"], name


# ── Open-stage-only promise (#99) ─────────────────────────────────────────────
#
# Two tools promise "OPEN pipeline stages only" in their descriptions. The schema
# enum steers the model; the executor guard is what actually enforces, because the
# assistant registry does not validate tool arguments against the schema. The
# service and the REST route stay permissive on purpose — pinned by their own tests
# in test_crm_bulk_move.py / test_crm_router.py, which this section must not disturb.


def test_closed_stages_are_won_and_lost():
    """Pin the REQUIREMENT independently of the constant, so the guard tests below
    cannot pass vacuously if CLOSED_STAGES is ever emptied or mistyped ('loss')."""
    assert service.CLOSED_STAGES == ("won", "lost")
    # Non-tautological despite OPEN_STAGES being the complement: a closed stage that
    # is not a real stage (a typo) puts a name in the union that DEAL_STAGES lacks.
    assert set(service.OPEN_STAGES) | set(service.CLOSED_STAGES) == set(service.DEAL_STAGES)


def test_bulk_move_refuses_closed_stages(monkeypatch):
    """The blast radius this guard exists for: one call closing up to BULK_MOVE_MAX
    deals with no loss reason. The tripwire proves the executor answers first."""
    _refuse_service(monkeypatch)
    for stage in ("won", "lost"):
        out = tools.crm_bulk_move_deals([1, 2], stage)
        assert "crm_mark_deal_won" in out["error"], stage
        assert "crm_mark_deal_lost" in out["error"], stage


def test_update_deal_stage_refuses_closed_stages(monkeypatch):
    """The single-deal sibling makes the identical promise, so it gets the identical
    guard — enforcing only bulk would leave the same broken contract one tool over."""
    def explode(*a, **k):
        raise AssertionError("the executor reached the service with a closed stage")
    monkeypatch.setattr(service, "update_deal_stage", explode)
    for stage in ("won", "lost"):
        out = tools.crm_update_deal_stage(7, stage)
        assert "crm_mark_deal_won" in out["error"], stage
        assert "crm_mark_deal_lost" in out["error"], stage


def test_both_stage_tool_schemas_advertise_exactly_the_open_stages():
    """Schema-only (it never calls an executor): the enum and the human-readable list
    are both generated from OPEN_STAGES, so a tool cannot advertise a stage its
    executor refuses — the drift that let these descriptions promise one thing while
    the schema allowed another."""
    by_name = {d["name"]: d for d in CRM_TOOL_DEFS}
    for name in ("crm_update_deal_stage", "crm_bulk_move_deals"):
        prop = by_name[name]["input_schema"]["properties"]["stage"]
        assert prop["enum"] == list(service.OPEN_STAGES), name
        assert all(s in prop["description"] for s in service.OPEN_STAGES), name


def test_every_open_stage_still_reaches_the_service(monkeypatch):
    """Positive path (NOT guard coverage — this passes with the guards deleted): the
    guard tests only the TARGET stage, so every open stage must still forward."""
    from crm import provenance_service
    seen = []

    def fake_bulk(ids, stage):
        seen.append(("bulk", stage))
        return {"ok": True, "updated": 0, "updated_ids": [], "errors": []}

    def fake_single(deal_id, stage):
        seen.append(("single", stage))
        return {"id": deal_id, "stage": stage}

    monkeypatch.setattr(service, "bulk_move_deals", fake_bulk)
    monkeypatch.setattr(service, "update_deal_stage", fake_single)
    monkeypatch.setattr(provenance_service, "record_fields", lambda *a, **k: None)
    for stage in service.OPEN_STAGES:
        tools.crm_bulk_move_deals([1], stage)
        tools.crm_update_deal_stage(1, stage)
    assert seen == [(kind, s) for s in service.OPEN_STAGES for kind in ("bulk", "single")]


def test_the_guard_refuses_ONLY_closed_stages_not_every_invalid_one(monkeypatch):
    """The guard is `stage in CLOSED_STAGES`, not `stage not in OPEN_STAGES` — a typo
    must still get the service's own invalid-stage answer, not advice to close the
    deal. Widening the guard to the complement would break exactly this."""
    reached = []

    def fake_single(deal_id, stage):
        reached.append(("single", stage))
        return None  # the service's own "invalid stage" answer

    def fake_bulk(ids, stage):
        reached.append(("bulk", stage))
        return {"ok": False, "updated": 0, "updated_ids": [], "errors": [f"Invalid stage: {stage}"]}

    monkeypatch.setattr(service, "update_deal_stage", fake_single)
    monkeypatch.setattr(service, "bulk_move_deals", fake_bulk)
    for bad in ("banana", "Won", " lost"):
        single = tools.crm_update_deal_stage(7, bad)
        assert "crm_mark_deal_won" not in single["error"], bad
        bulk = tools.crm_bulk_move_deals([1], bad)
        assert bulk["errors"] == [f"Invalid stage: {bad}"], bad
    # Delegation, not just wording: the guard let every one of these through to the
    # service. Asserting only on the message would pass if the guard swallowed them.
    assert reached == [(kind, s) for s in ("banana", "Won", " lost")
                       for kind in ("single", "bulk")]


def test_open_predicate_sql_agrees_with_the_closed_stages_tuple():
    """OPEN_PREDICATE is the SQL spelling of CLOSED_STAGES and stays a hand-written
    literal (every deal-reading query embeds it). This is what keeps the two in step."""
    for stage in service.CLOSED_STAGES:
        assert f"'{stage}'" in service.OPEN_PREDICATE
        assert f"'{stage}'" in service.OPEN_PREDICATE_D
    for stage in service.OPEN_STAGES:
        assert f"'{stage}'" not in service.OPEN_PREDICATE
    assert service.OPEN_PREDICATE == "stage NOT IN ('won', 'lost')"
    assert service.OPEN_PREDICATE_D == "d.stage NOT IN ('won', 'lost')"


def test_lifecycle_tools_surface_a_refusal_instead_of_raising(monkeypatch):
    """A ValueError escaping an executor becomes registry.execute_tool_sync's generic
    "failed, please try again" — which sends the model into a retry loop on a
    permanent condition and hides the actionable message."""
    def refuse(*a, **k):
        raise ValueError("Cannot change the stage of archived deal #7 — restore it first")

    for name, call in (
        ("mark_deal_won", lambda: tools.crm_mark_deal_won(7)),
        ("mark_deal_lost", lambda: tools.crm_mark_deal_lost(7, lost_reason="x")),
        # An OPEN stage: since #99 this executor refuses won/lost before it reaches the
        # service, and this test's subject is the ValueError→{"error"} passthrough.
        ("update_deal_stage", lambda: tools.crm_update_deal_stage(7, "qualified")),
        ("update_deal", lambda: tools.crm_update_deal(7, stage="won")),
    ):
        monkeypatch.setattr(service, name, refuse)
        out = call()
        assert "restore it first" in out.get("error", ""), name


def test_summary_projection_keeps_the_fields_the_prompt_relies_on():
    """SALES_GUIDE calls lost_reason 'the most useful field when reviewing a quarter',
    and a search row's only recency signal is updated_at (last_activity_at is computed
    by get_pipeline alone)."""
    assert "lost_reason" in tools._DEAL_SUMMARY_FIELDS
    assert "updated_at" in tools._DEAL_SUMMARY_FIELDS


# ── Lead-score tools (issue #18) ──────────────────────────────────────────────

def test_lead_score_tool_defs_shaped():
    by_name = {d["name"]: d for d in CRM_TOOL_DEFS}
    assert by_name["crm_get_lead_score"]["writes"] is False
    assert by_name["crm_get_lead_score"]["input_schema"]["required"] == ["entity_type", "entity_id"]
    assert by_name["crm_recompute_lead_scores"]["writes"] is True
    assert by_name["crm_recompute_lead_scores"]["input_schema"]["required"] == []  # scope optional


def test_crm_get_lead_score_deal_happy(monkeypatch):
    from crm import scoring_service
    monkeypatch.setattr(scoring_service, "score_deal", lambda eid: {"score": 71, "factors": {}})
    monkeypatch.setattr(service, "get_deal", lambda eid: {"id": eid, "lead_score": 68})
    out = tools.crm_get_lead_score("deal", 5)
    assert out["score"] == 71 and out["stored_score"] == 68


def test_crm_get_lead_score_contact_happy(monkeypatch):
    from crm import scoring_service
    monkeypatch.setattr(scoring_service, "score_contact", lambda eid: {"score": 40, "factors": {}})
    monkeypatch.setattr(service, "get_contact", lambda eid: {"id": eid, "lead_score": 40})
    out = tools.crm_get_lead_score("contact", 9)
    assert out["score"] == 40 and out["stored_score"] == 40


def test_crm_get_lead_score_bad_type_and_missing(monkeypatch):
    from crm import scoring_service
    assert "error" in tools.crm_get_lead_score("company", 1)  # invalid entity_type
    monkeypatch.setattr(scoring_service, "score_deal", lambda eid: None)  # missing entity
    monkeypatch.setattr(service, "get_deal", lambda eid: None)
    assert "error" in tools.crm_get_lead_score("deal", 999)


def test_crm_recompute_lead_scores_passes_scope_and_wraps_error(monkeypatch):
    from crm import scoring_service
    seen = {}
    monkeypatch.setattr(scoring_service, "backfill_scores",
                        lambda scope: seen.update({"scope": scope}) or {"deals_scored": 1, "contacts_scored": 0, "errors": 0, "capped": False})
    assert tools.crm_recompute_lead_scores("all")["deals_scored"] == 1
    assert seen["scope"] == "all"
    assert tools.crm_recompute_lead_scores()  # default scope reaches the service
    assert seen["scope"] == "all"  # executor default

    def boom(scope):
        raise ValueError("bad scope")
    monkeypatch.setattr(scoring_service, "backfill_scores", boom)
    assert "error" in tools.crm_recompute_lead_scores("weird")
