"""Uncapped watched-record sets and the heartbeat's announced-record memory."""

from core.postgres import pg_execute, pg_fetchall

KIND = "heartbeat"
LISTED_PER_TYPE = 10
_HEADINGS = (("todo", "Overdue todos"), ("deal", "Deals with no recent activity"),
             ("contact", "Contacts with no recent contact"))


def current_items() -> dict[tuple[str, int], str]:
    from crm import analytics_service, gtd_common, last_contact
    from crm.service import (
        LAST_TOUCH_SQL,
        LIVE_PREDICATE_D,
        LIVE_TODO_PREDICATE,
        NOT_DROPPED_TODO,
        OPEN_PREDICATE_D,
        brought_back_sql,
    )

    params: list = [gtd_common.today_local_str()]
    todos = pg_fetchall(
        "SELECT id, title AS label FROM todos WHERE completed = 0 AND due_date != '' "
        f"AND due_date < %s AND {LIVE_TODO_PREDICATE} AND {NOT_DROPPED_TODO} "
        f"AND {brought_back_sql('bring_back_on', params)} ORDER BY id", params,
    )
    deals = pg_fetchall(
        f"SELECT d.id, d.title AS label FROM deals d WHERE {OPEN_PREDICATE_D} AND "
        f"{LIVE_PREDICATE_D} AND {LAST_TOUCH_SQL} < now() - make_interval(days => %s) ORDER BY d.id",
        (analytics_service.DEFAULT_DEAL_STALE_DAYS,),
    )
    # simplification: derives a last-touch date per active contact each turn (the same
    # cost get_contact_staleness documents); upgrade path is a maintained last_contacted_at.
    contacts = pg_fetchall(
        f"""SELECT id, label FROM (
            SELECT ct.id, ct.name AS label,
                   {last_contact.last_contact_sql('contact', 'ct.id')} AS touched_at
              FROM contacts ct WHERE ct.status = 'active') s
             WHERE touched_at IS NULL OR touched_at < now() - make_interval(days => %s)
             ORDER BY id""",
        (analytics_service.DEFAULT_CONTACT_STALE_DAYS,),
    )
    return {(kind, row["id"]): row["label"]
            for kind, rows in (("todo", todos), ("deal", deals), ("contact", contacts))
            for row in rows}


def announced() -> set[tuple[str, int]]:
    return {(row["entity_type"], row["entity_id"]) for row in pg_fetchall(
        "SELECT entity_type, entity_id FROM proactive_nudges WHERE kind = %s", (KIND,),
    )}


def save(items) -> None:
    # simplification: a CRM reset during the up-to-120s turn can leave ids from before
    # the reset marked announced; the next turn prunes any that are not current.
    items = list(items)
    pg_execute(
        """WITH cur AS (
            SELECT * FROM unnest(%s::text[], %s::int[]) AS t(entity_type, entity_id)
        ), gone AS (
            DELETE FROM proactive_nudges n
             WHERE n.kind = %s
               AND NOT EXISTS (SELECT 1 FROM cur WHERE cur.entity_type = n.entity_type
                                                   AND cur.entity_id = n.entity_id)
        )
        INSERT INTO proactive_nudges (entity_type, entity_id, kind)
        SELECT entity_type, entity_id, %s FROM cur
        ON CONFLICT (entity_type, entity_id, kind) DO NOTHING""",
        ([kind for kind, _ in items], [item_id for _, item_id in items], KIND, KIND),
    )


def describe(new: dict[tuple[str, int], str]) -> str:
    lines = []
    for kind, heading in _HEADINGS:
        items = sorted((item_id, label) for (item_kind, item_id), label in new.items() if item_kind == kind)
        if not items:
            continue
        listed = "; ".join(f"{kind} {item_id}: {' '.join(label.split())[:80]}"
                           for item_id, label in items[:LISTED_PER_TYPE])
        remainder = f"; and {len(items) - LISTED_PER_TYPE} more" if len(items) > LISTED_PER_TYPE else ""
        lines.append(f"{heading} ({len(items)} new): {listed}{remainder}")
    return "\n".join(lines)
