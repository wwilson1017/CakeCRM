"""One definition of customer contact for deals and contacts (#333).

A contact is a logged call/email/meeting/note or a live, non-housekeeping chatter
note, dated by created_at. The newest wins. ``note`` is an activity kind because
the deal sheet's Note action logs an activity row; ``follow_up`` is not contact.
Unlike upstream, there is no separate matched outbound-mail source here.

The SQL arguments (including entity_id and before) are trusted outer-query
expressions, never user input. Rendered fragments carry no percent characters,
so they can be embedded in parameterized statements without extra bindings.
"""

from crm.scoring_service import not_housekeeping_sql

CONTACT_ACTIVITY_KINDS = ("call", "email", "meeting", "note")
_ENTITY_FK = {"deal": "deal_id", "contact": "contact_id"}


def contact_activity_sql(column: str) -> str:
    kinds = ", ".join(f"'{k}'" for k in CONTACT_ACTIVITY_KINDS)
    return f"lower({column}) IN ({kinds})"


def live_note_sql(alias: str) -> str:
    return f"{alias}.archived = 0 AND {not_housekeeping_sql(f'{alias}.message')}"


def last_contact_sql(entity: str, entity_id: str, *, before: str | None = None) -> str:
    if entity not in _ENTITY_FK:
        raise ValueError(f"unknown entity {entity!r}")
    fk = _ENTITY_FK[entity]
    cap_a = f" AND a.created_at <= {before}" if before else ""
    cap_ch = f" AND ch.created_at <= {before}" if before else ""
    return f"""GREATEST(
        (SELECT MAX(a.created_at) FROM activity_log a
          WHERE a.{fk} = {entity_id} AND {contact_activity_sql('a.activity')}{cap_a}),
        (SELECT MAX(ch.created_at) FROM crm_chatter ch
          WHERE ch.entity_type = '{entity}' AND ch.entity_id = {entity_id}
            AND {live_note_sql('ch')}{cap_ch})
    )"""


DEAL_CONTACTS_UNION_SQL = f"""SELECT a.deal_id, a.created_at FROM activity_log a
            WHERE a.deal_id IS NOT NULL AND {contact_activity_sql('a.activity')}
            UNION ALL
            SELECT ch.entity_id AS deal_id, ch.created_at FROM crm_chatter ch
            WHERE ch.entity_type = 'deal' AND {live_note_sql('ch')}"""
