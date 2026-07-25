"""Hermetic unit tests for crm/provenance_service.py + its producer/endpoints (issue #16).

No DB: get_connection is faked (conftest.fake_conn) and pg_fetchall/_load_current_values are
monkeypatched."""

import pytest
from fastapi import HTTPException

from crm import (
    provenance_service as prov,
    router as crm_router,
    service as crm_service,
    tools,
)

# ── record: validation + reset-on-rewrite ─────────────────────────────────────

def test_record_rejects_bad_entity_source_and_field():
    with pytest.raises(ValueError):
        prov.record("widget", 1, "phone", "x", "assistant")
    with pytest.raises(ValueError):
        prov.record("contact", 1, "phone", "x", "web")          # not in VALID_SOURCES
    with pytest.raises(ValueError):
        prov.record("contact", 1, "not_a_column", "x", "assistant")


def test_record_upsert_resets_confirmation(monkeypatch):
    executed = []

    class Cur:
        def execute(self, sql, params):
            executed.append((" ".join(sql.split()), params))

    prov.record("contact", 1, "phone", "555", "assistant", cur=Cur())
    sql, params = executed[0]
    assert "ON CONFLICT (entity_type, entity_id, field_name)" in sql
    assert "confirmed_at = NULL" in sql                          # reset-on-rewrite
    assert params[:5] == ("contact", 1, "phone", "555", "assistant")


# ── record_fields: entity lock, skip-vanished, record-only-matching ───────────

def test_record_fields_records_only_still_matching_fields(monkeypatch, fake_conn):
    # Locked entity current values: phone matches the snapshot, email was changed by a
    # human in the gap (current != snapshot) → email is skipped, only phone recorded.
    conn = fake_conn(monkeypatch, prov, fetchone_results=[(1,)])  # entity exists
    monkeypatch.setattr(prov, "row_to_dict", lambda cur, r: {"phone": "555", "email": "new@x.com"})
    prov.record_fields("contact", 1, {"phone": "555", "email": "OLD@x.com"})
    inserts = [p for s, p in conn.executed if "INSERT INTO crm_field_provenance" in s]
    assert len(inserts) == 1
    assert inserts[0][2] == "phone"                              # field_name of the only insert
    assert any("SELECT * FROM contacts WHERE id = %s FOR UPDATE" in s for s, _ in conn.executed)


def test_record_fields_skips_vanished_entity(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, prov, fetchone_results=[None])   # entity gone
    prov.record_fields("contact", 1, {"phone": "555"})
    assert not any("INSERT INTO crm_field_provenance" in s for s, _ in conn.executed)


def test_record_fields_ignores_non_allowlisted_keys(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, prov, fetchone_results=[(1,)])
    monkeypatch.setattr(prov, "row_to_dict", lambda cur, r: {"phone": "555"})
    prov.record_fields("contact", 1, {"unknown": "x"})            # not in PROVENANCE_FIELDS
    # nothing to write → no entity lock, no insert
    assert not any("INSERT INTO crm_field_provenance" in s for s, _ in conn.executed)


# ── get_provenance: staleness + default filtering ─────────────────────────────

def test_get_provenance_filters_stale_and_confirmed_by_default(monkeypatch):
    rows = [
        {"field_name": "phone", "value_snapshot": "555", "confirmed_at": None},
        {"field_name": "email", "value_snapshot": "old@x.com", "confirmed_at": None},
        {"field_name": "title", "value_snapshot": "Mgr", "confirmed_at": "2026-01-01T00:00:00+00:00"},
    ]
    monkeypatch.setattr(prov, "pg_fetchall", lambda *a: [dict(r) for r in rows])
    monkeypatch.setattr(prov, "_load_current_values", lambda *a: {"phone": "555", "email": "new@x.com", "title": "Mgr"})
    live = prov.get_provenance("contact", 1)
    assert [r["field_name"] for r in live] == ["phone"]           # email stale, title confirmed
    assert live[0]["stale"] is False


def test_get_provenance_include_flags_return_stale_and_confirmed(monkeypatch):
    rows = [
        {"field_name": "email", "value_snapshot": "old@x.com", "confirmed_at": None},
        {"field_name": "title", "value_snapshot": "Mgr", "confirmed_at": "2026-01-01T00:00:00+00:00"},
    ]
    monkeypatch.setattr(prov, "pg_fetchall", lambda *a: [dict(r) for r in rows])
    monkeypatch.setattr(prov, "_load_current_values", lambda *a: {"email": "new@x.com", "title": "Mgr"})
    out = prov.get_provenance("contact", 1, include_confirmed=True, include_stale=True)
    by = {r["field_name"]: r for r in out}
    assert by["email"]["stale"] is True and by["title"]["stale"] is False


def test_get_provenance_norm_treats_none_as_empty(monkeypatch):
    monkeypatch.setattr(prov, "pg_fetchall", lambda *a: [{"field_name": "notes", "value_snapshot": "", "confirmed_at": None}])
    monkeypatch.setattr(prov, "_load_current_values", lambda *a: {"notes": None})
    out = prov.get_provenance("deal", 1)
    assert out and out[0]["stale"] is False                       # None ~ "" ~ not stale


# ── confirm: one transaction, locked snapshot ─────────────────────────────────

def test_confirm_happy_path_updates_and_audits_in_one_txn(monkeypatch, fake_conn):
    conn = fake_conn(
        monkeypatch, prov,
        fetchone_results=[{"phone": "555"}, ("555",), {"field_name": "phone", "confirmed_at": "c"}],
    )
    monkeypatch.setattr(prov, "row_to_dict", lambda cur, r: r)     # rows are already dicts
    out = prov.confirm("contact", 1, "phone")
    stmts = [s for s, _ in conn.executed]
    assert any("SELECT * FROM contacts WHERE id = %s FOR UPDATE" in s for s in stmts)
    assert any("FROM crm_field_provenance WHERE entity_type = %s AND entity_id = %s AND field_name = %s FOR UPDATE" in s for s in stmts)
    assert any("UPDATE crm_field_provenance SET confirmed_at" in s for s in stmts)
    assert any("INSERT INTO crm_chatter" in s for s in stmts)
    assert out["confirmed_at"] == "c"


def test_confirm_stale_value_is_a_noop(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, prov, fetchone_results=[{"phone": "999"}, ("555",)])
    monkeypatch.setattr(prov, "row_to_dict", lambda cur, r: r)
    out = prov.confirm("contact", 1, "phone")                     # live 999 != snapshot 555
    assert out == {"stale": True}
    assert not any("UPDATE crm_field_provenance SET confirmed_at" in s for s, _ in conn.executed)
    assert not any("INSERT INTO crm_chatter" in s for s, _ in conn.executed)


def test_confirm_no_provenance_row_returns_none(monkeypatch, fake_conn):
    fake_conn(monkeypatch, prov, fetchone_results=[{"phone": "555"}, None])  # prov row missing
    monkeypatch.setattr(prov, "row_to_dict", lambda cur, r: r)
    assert prov.confirm("contact", 1, "phone") is None


def test_confirm_rejects_bad_entity_type():
    with pytest.raises(ValueError):
        prov.confirm("widget", 1, "phone")


# ── Producer wiring in tools.py (delta = 0 tools) ─────────────────────────────

def test_update_contact_tool_records_post_write_values(monkeypatch):
    calls = []
    monkeypatch.setattr(crm_service, "update_contact",
                        lambda cid, **kw: {"id": cid, "phone": "555", "tags": "a,b"})
    monkeypatch.setattr(prov, "record_fields", lambda et, eid, f: calls.append((et, eid, f)))
    out = tools.crm_update_contact(1, phone="555", tags="a, b")
    assert out["id"] == 1
    # snapshots come from the POST-write dict (normalized "a,b"), not the raw input.
    assert calls == [("contact", 1, {"phone": "555", "tags": "a,b"})]


def test_tool_skips_empty_values(monkeypatch):
    calls = []
    monkeypatch.setattr(crm_service, "update_contact",
                        lambda cid, **kw: {"id": cid, "phone": "", "title": "Mgr"})
    monkeypatch.setattr(prov, "record_fields", lambda et, eid, f: calls.append(f))
    tools.crm_update_contact(1, phone="", title="Mgr")
    assert calls == [{"title": "Mgr"}]                            # empty phone skipped


def test_tool_error_result_records_nothing(monkeypatch):
    calls = []
    monkeypatch.setattr(crm_service, "update_contact", lambda cid, **kw: None)  # not found
    monkeypatch.setattr(prov, "record_fields", lambda *a: calls.append(a))
    out = tools.crm_update_contact(99, phone="555")
    assert "error" in out and calls == []


def test_tool_provenance_failure_does_not_break_the_write(monkeypatch):
    def boom(*a):
        raise RuntimeError("provenance down")

    monkeypatch.setattr(crm_service, "update_deal", lambda did, **kw: {"id": did, "stage": "won"})
    monkeypatch.setattr(prov, "record_fields", boom)
    out = tools.crm_update_deal(3, stage="won")                   # must still succeed
    assert out == {"id": 3, "stage": "won"}


def test_update_deal_stage_tool_records_stage(monkeypatch):
    calls = []
    monkeypatch.setattr(crm_service, "update_deal_stage", lambda did, stage: {"id": did, "stage": stage})
    monkeypatch.setattr(prov, "record_fields", lambda et, eid, f: calls.append((et, eid, f)))
    tools.crm_update_deal_stage(7, "negotiation")
    assert calls == [("deal", 7, {"stage": "negotiation"})]


# ── Router contract (direct-call: 200-stale, 404, 400) ────────────────────────

async def test_confirm_endpoint_returns_200_stale_payload(monkeypatch):
    monkeypatch.setattr(prov, "confirm", lambda *a: {"stale": True})
    body = crm_router.ProvenanceConfirmBody(field_name="phone")
    out = await crm_router.confirm_provenance("contact", 1, body, user={})
    assert out == {"confirmed": False, "stale": True}


async def test_confirm_endpoint_404_when_no_row(monkeypatch):
    monkeypatch.setattr(prov, "confirm", lambda *a: None)
    body = crm_router.ProvenanceConfirmBody(field_name="phone")
    with pytest.raises(HTTPException) as ei:
        await crm_router.confirm_provenance("contact", 1, body, user={})
    assert ei.value.status_code == 404


async def test_get_provenance_endpoint_400_on_bad_entity(monkeypatch):
    with pytest.raises(HTTPException) as ei:
        await crm_router.get_provenance("widget", 1, user={})
    assert ei.value.status_code == 400
