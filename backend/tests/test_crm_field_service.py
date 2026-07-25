"""Custom-field service (issue #19) — hermetic unit coverage.

pg helpers are monkeypatched; the transactional set_field_values path uses the
shared fake_conn fixture. Real rollback/locking guarantees are proven in
tests/test_integration_crm_pg.py (they need a live Postgres).
"""

import json

import pytest

from crm import field_service

# ── create_field_definition ───────────────────────────────────────────────────

def _capture_create(monkeypatch, *, existing_max=0):
    """Monkeypatch pg_fetchone for create_field_definition; return a dict that will
    hold the captured INSERT params under 'insert'."""
    captured: dict = {}

    def fake_fetchone(sql, params=()):
        if "COALESCE(MAX(display_order)" in sql:
            return {"next": existing_max + 10}
        if sql.lstrip().startswith("INSERT INTO crm_field_definitions"):
            captured["insert"] = params
            return {"id": 1}
        if "SELECT * FROM crm_field_definitions WHERE id" in sql:
            return {"id": 1, "dropdown_options": None}
        return None

    monkeypatch.setattr(field_service, "pg_fetchone", fake_fetchone)
    return captured


def test_create_rejects_invalid_entity_type(monkeypatch):
    _capture_create(monkeypatch)
    with pytest.raises(ValueError):
        field_service.create_field_definition(
            {"entity_type": "widget", "name": "X", "field_type": "text"}
        )


def test_create_rejects_invalid_field_type(monkeypatch):
    _capture_create(monkeypatch)
    with pytest.raises(ValueError):
        field_service.create_field_definition(
            {"entity_type": "contact", "name": "X", "field_type": "multiselect"}
        )


def test_create_rejects_blank_name(monkeypatch):
    _capture_create(monkeypatch)
    with pytest.raises(ValueError):
        field_service.create_field_definition(
            {"entity_type": "contact", "name": "   ", "field_type": "text"}
        )


def test_create_derives_slug_from_name(monkeypatch):
    cap = _capture_create(monkeypatch)
    field_service.create_field_definition(
        {"entity_type": "contact", "name": "Account Manager!", "field_type": "text"}
    )
    # params order: entity_type, name, field_key, field_type, options, is_required, display_order, ...
    assert cap["insert"][2] == "account_manager"


def test_create_all_symbol_name_raises(monkeypatch):
    _capture_create(monkeypatch)
    with pytest.raises(ValueError):
        field_service.create_field_definition(
            {"entity_type": "contact", "name": "!!!", "field_type": "text"}
        )


def test_create_serializes_options_and_int_required_and_server_order(monkeypatch):
    cap = _capture_create(monkeypatch, existing_max=20)
    field_service.create_field_definition({
        "entity_type": "deal", "name": "Tier", "field_type": "select",
        "dropdown_options": ["A", "B"], "is_required": True,
    })
    p = cap["insert"]
    assert json.loads(p[4]) == ["A", "B"]      # options serialized to JSON string
    assert p[5] == 1                            # is_required bool -> int
    assert p[6] == 30                           # display_order = max(20) + 10, server-assigned


# ── update_field_definition ───────────────────────────────────────────────────

def _capture_update(monkeypatch, *, exists=True):
    captured: dict = {"execute": None}

    def fake_fetchone(sql, params=()):
        if "SELECT id FROM crm_field_definitions" in sql:
            return {"id": 1} if exists else None
        if "SELECT * FROM crm_field_definitions WHERE id" in sql:
            return {"id": 1, "dropdown_options": None}
        return None

    def fake_execute(sql, params=()):
        captured["execute"] = (sql, params)
        return 1

    monkeypatch.setattr(field_service, "pg_fetchone", fake_fetchone)
    monkeypatch.setattr(field_service, "pg_execute", fake_execute)
    return captured


def test_update_only_touches_mutable_columns(monkeypatch):
    cap = _capture_update(monkeypatch)
    # entity_type / field_key / field_type must be ignored even if passed.
    field_service.update_field_definition(1, {
        "name": "New", "entity_type": "deal", "field_key": "x", "field_type": "number",
    })
    sql = cap["execute"][0]
    assert "name = %s" in sql
    assert "entity_type" not in sql and "field_key" not in sql and "field_type" not in sql


def test_update_explicit_null_dropdown_clears(monkeypatch):
    cap = _capture_update(monkeypatch)
    field_service.update_field_definition(1, {"dropdown_options": None})
    sql, params = cap["execute"]
    assert "dropdown_options = %s" in sql
    assert params[0] is None            # NULL clears the options


def test_update_blank_name_raises(monkeypatch):
    _capture_update(monkeypatch)
    with pytest.raises(ValueError):
        field_service.update_field_definition(1, {"name": "   "})


def test_update_missing_returns_none(monkeypatch):
    _capture_update(monkeypatch, exists=False)
    assert field_service.update_field_definition(999, {"name": "X"}) is None


def test_update_empty_patch_reselects_without_writing(monkeypatch):
    cap = _capture_update(monkeypatch)
    result = field_service.update_field_definition(1, {})
    assert result == {"id": 1, "dropdown_options": None}
    assert cap["execute"] is None       # no UPDATE issued


# ── validate_field_value ──────────────────────────────────────────────────────

def test_validate_number():
    field_service.validate_field_value({"field_type": "number", "name": "Amt"}, "42.5")
    with pytest.raises(ValueError):
        field_service.validate_field_value({"field_type": "number", "name": "Amt"}, "abc")


def test_validate_boolean():
    for good in ("0", "1"):
        field_service.validate_field_value({"field_type": "boolean", "name": "VIP"}, good)
    with pytest.raises(ValueError):
        field_service.validate_field_value({"field_type": "boolean", "name": "VIP"}, "yes")


def test_validate_select():
    fd = {"field_type": "select", "name": "Tier", "dropdown_options": json.dumps(["A", "B"])}
    field_service.validate_field_value(fd, "A")
    with pytest.raises(ValueError):
        field_service.validate_field_value(fd, "C")


def test_validate_empty_string_clears_any_type():
    # Empty string bypasses validation for every type (the "clear" contract).
    for ft in ("number", "boolean", "select"):
        field_service.validate_field_value(
            {"field_type": ft, "name": "X", "dropdown_options": json.dumps(["A"])}, ""
        )
        field_service.validate_field_value(
            {"field_type": ft, "name": "X", "dropdown_options": json.dumps(["A"])}, None
        )


# ── set_field_values (transactional; fake_conn) ───────────────────────────────

def test_set_values_entity_missing_raises_no_writes(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, field_service, fetchone_results=[None])
    with pytest.raises(ValueError):
        field_service.set_field_values("contact", 5, {"9": "x"}, "u")
    # Only the entity FOR UPDATE select ran; no upsert.
    assert len(conn.executed) == 1
    assert "FOR UPDATE" in conn.executed[0][0]


def test_set_values_upserts_and_locks_defs_for_share(monkeypatch, fake_conn):
    conn = fake_conn(
        monkeypatch, field_service,
        fetchone_results=[(5,)],                                  # entity exists
        fetchall_results=[[(9, "Amount", "number", None)]],       # defs FOR SHARE
    )
    out = field_service.set_field_values("deal", 5, {"9": "42"}, "will")
    assert out == {"ok": True, "updated": 1, "errors": []}
    sqls = [e[0] for e in conn.executed]
    assert any("FOR UPDATE" in s for s in sqls)
    assert any("FOR SHARE" in s for s in sqls)
    upsert = [e for e in conn.executed if "ON CONFLICT" in e[0]][0]
    assert "ON CONFLICT (entity_type, entity_id, field_id)" in upsert[0]
    assert "will" in upsert[1]                                    # attribution stored


def test_set_values_unknown_field_collected_not_raised(monkeypatch, fake_conn):
    conn = fake_conn(
        monkeypatch, field_service,
        fetchone_results=[(5,)],
        fetchall_results=[[(9, "F", "text", None)]],              # only field 9 exists
    )
    out = field_service.set_field_values("contact", 5, {"9": "ok", "99": "x"}, "u")
    assert out["updated"] == 1
    assert out["errors"] == ["Field 99 not found for contact"]
    assert sum("ON CONFLICT" in e[0] for e in conn.executed) == 1  # only the known field upserted


def test_set_values_validation_error_propagates(monkeypatch, fake_conn):
    conn = fake_conn(
        monkeypatch, field_service,
        fetchone_results=[(5,)],
        fetchall_results=[[(9, "Amount", "number", None)]],
    )
    with pytest.raises(ValueError):
        field_service.set_field_values("deal", 5, {"9": "abc"}, "u")
    # Validation happens before any upsert — no ON CONFLICT statement ran.
    assert not any("ON CONFLICT" in e[0] for e in conn.executed)


def test_set_values_rejects_oversized_request(monkeypatch):
    big = {str(i): "x" for i in range(field_service._MAX_FIELDS_PER_WRITE + 1)}
    with pytest.raises(ValueError):
        field_service.set_field_values("contact", 1, big, "u")


# ── reads ─────────────────────────────────────────────────────────────────────

def test_get_field_values_invalid_type_raises():
    with pytest.raises(ValueError):
        field_service.get_field_values("widget", 1)


def test_get_field_values_left_join_and_parse(monkeypatch):
    captured = {}

    def fake_fetchall(sql, params=()):
        captured["sql"] = sql
        return [{"field_id": 1, "dropdown_options": json.dumps(["A"]), "value": None}]

    monkeypatch.setattr(field_service, "pg_fetchall", fake_fetchall)
    rows = field_service.get_field_values("contact", 7)
    assert "LEFT JOIN crm_field_values" in captured["sql"]
    assert rows[0]["dropdown_options"] == ["A"]        # parsed from JSON string


def test_get_field_values_batch_guards(monkeypatch):
    assert field_service.get_field_values_batch("widget", [1]) == {}
    assert field_service.get_field_values_batch("contact", []) == {}


def test_list_invalid_entity_type_raises():
    with pytest.raises(ValueError):
        field_service.list_field_definitions("widget")
