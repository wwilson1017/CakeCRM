"""Hermetic pins for the shared customer-contact predicates (#333)."""

import pytest

from crm import last_contact, scoring_service


def test_contact_activity_kinds_are_the_customer_contact_allowlist():
    assert last_contact.CONTACT_ACTIVITY_KINDS == ("call", "email", "meeting", "note")
    assert "follow_up" not in last_contact.CONTACT_ACTIVITY_KINDS
    assert all("%" not in k and "'" not in k for k in last_contact.CONTACT_ACTIVITY_KINDS)
    predicate = last_contact.contact_activity_sql("a.activity")
    assert "lower(a.activity) IN" in predicate
    for kind in last_contact.CONTACT_ACTIVITY_KINDS:
        assert f"'{kind}'" in predicate


def test_live_notes_exclude_archived_and_housekeeping():
    predicate = last_contact.live_note_sql("ch")
    assert "ch.archived = 0" in predicate
    assert scoring_service.not_housekeeping_sql("ch.message") in predicate


@pytest.mark.parametrize("entity,entity_id", [("deal", "d.id"), ("contact", "ct.id")])
@pytest.mark.parametrize("before", [None, "x.won_at"])
def test_contact_expression_uses_both_signals_and_only_the_optional_cutoff(entity, entity_id, before):
    sql = last_contact.last_contact_sql(entity, entity_id, before=before)
    assert f"a.{entity}_id = {entity_id}" in sql
    assert f"ch.entity_type = '{entity}'" in sql
    assert f"ch.entity_id = {entity_id}" in sql
    assert last_contact.contact_activity_sql("a.activity") in sql
    assert last_contact.live_note_sql("ch") in sql
    assert "%" not in sql and "updated_at" not in sql and "deal_stage_events" not in sql
    for alias in ("a", "ch"):
        assert (f"{alias}.created_at <= x.won_at" in sql) is (before is not None)


def test_unknown_entity_is_refused():
    with pytest.raises(ValueError, match="unknown entity"):
        last_contact.last_contact_sql("company", "co.id")


def test_grouped_pipeline_uses_the_same_signal_predicates():
    sql = last_contact.DEAL_CONTACTS_UNION_SQL
    assert last_contact.contact_activity_sql("a.activity") in sql
    assert last_contact.live_note_sql("ch") in sql
    assert "ch.entity_type = 'deal'" in sql
    assert "%" not in sql and "updated_at" not in sql
