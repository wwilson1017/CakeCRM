"""CRM — field provenance service (issue #16).

Tracks which standard CRM fields the AI assistant populated, so the UI can show an "AI"
badge until a human confirms (or overwrites) the value.

Provenance is tied to a SPECIFIC value, not just a field name: each row stores a
``value_snapshot``. A row is the live badge state only while it is unconfirmed AND its
snapshot still equals the field's current value. A human editing the field through the
normal CRM update path changes the live value, so the snapshot no longer matches and the
badge auto-disappears (the row is "stale"). The lifecycle contract is:
``badge == (unconfirmed AND not stale)``.

One row per (entity_type, entity_id, field_name); field_name is a standard column name
(e.g. 'phone'). Custom fields (issue #19) will later add a 'cf:<field_key>' namespace —
this module is deliberately decoupled from that (local entity/field maps, no field_service
import), so standard-column provenance ships without EAV.

Single-tenant v1: no populated_by / confirmed_by attribution (there is no user_id yet).
The producer is ``record_fields`` (called from the assistant's write-tool executors in
crm/tools.py); human edits go through crm/router.py, which never records provenance — so
"AI-written vs human-written" falls out of which code path wrote the field.
"""

from datetime import datetime, timezone

from core.postgres import (
    get_connection,
    pg_execute,
    pg_fetchall,
    pg_fetchone,
    row_to_dict,
)

VALID_ENTITY_TYPES = {"deal", "contact"}
ENTITY_TABLE_MAP = {"deal": "deals", "contact": "contacts"}
# Standard columns only for v1 — must mirror service.update_contact / update_deal's
# `allowed` sets exactly, so a badge can only attach to a field the assistant can write
# and the UI can render. Issue #19 will add the 'cf:' namespace.
PROVENANCE_FIELDS = {
    "contact": {"name", "email", "phone", "company", "title", "source", "status",
                "tags", "notes", "company_id"},
    "deal": {"title", "stage", "value", "notes", "expected_close_date",
             "probability", "currency", "contact_id", "company_id"},
}
VALID_SOURCES = {"assistant"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(v) -> str:
    return "" if v is None else str(v)


def record(
    entity_type: str,
    entity_id: int,
    field_name: str,
    value_snapshot: "str | None",
    source: str,
    source_detail: "str | None" = None,
    confidence: "float | None" = None,
    cur=None,
) -> "dict | None":
    """Upsert a provenance row tying the badge to ``value_snapshot``.

    EVERY AI (re)write resets confirmation: the freshly written value is unverified until a
    human confirms it. We deliberately do NOT carry a prior confirmation forward even for an
    identical value — doing so could mask a case where the assistant re-writes a value a
    human had since edited away (the old confirmation would suppress the badge on the
    overwrite). Re-confirming is the safe default. Pass ``cur`` to run inside a caller's
    transaction (used by record_fields)."""
    if entity_type not in VALID_ENTITY_TYPES:
        raise ValueError(f"Invalid entity_type: {entity_type}")
    if source not in VALID_SOURCES:
        raise ValueError(f"Invalid source: {source} (expected one of {sorted(VALID_SOURCES)})")
    if field_name not in PROVENANCE_FIELDS[entity_type]:
        raise ValueError(f"Invalid field_name for {entity_type}: {field_name}")

    now = _now()

    def _run(c):
        c.execute(
            """INSERT INTO crm_field_provenance
                   (entity_type, entity_id, field_name, value_snapshot, source,
                    source_detail, confidence, populated_at, confirmed_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NULL)
               ON CONFLICT (entity_type, entity_id, field_name)
               DO UPDATE SET value_snapshot = EXCLUDED.value_snapshot,
                             source         = EXCLUDED.source,
                             source_detail  = EXCLUDED.source_detail,
                             confidence     = EXCLUDED.confidence,
                             populated_at   = EXCLUDED.populated_at,
                             confirmed_at   = NULL""",
            (entity_type, entity_id, field_name, value_snapshot, source,
             source_detail, confidence, now),
        )

    if cur is not None:
        _run(cur)
        return None  # caller owns the transaction / fetch
    with get_connection() as conn:
        _run(conn.cursor())
    return get_one(entity_type, entity_id, field_name)


def record_fields(entity_type: str, entity_id: int, values: dict) -> None:
    """Producer API: record 'assistant' provenance for a batch of fields, atomically.

    Opens ONE transaction, locks the entity row FOR UPDATE, and:
      - if the entity vanished between the value write and here → records nothing (no
        orphan row that a reused SERIAL id could later inherit);
      - records only fields whose CURRENT locked value still equals the passed snapshot —
        if a human edited the field in the gap between the write's commit and this call,
        the human's value wins and no badge is minted.

    ``values`` maps field_name → the POST-write value (from the tool's returned entity
    dict), so normalization (tags coercion, etc.) can't produce an instantly-stale badge.
    """
    if entity_type not in VALID_ENTITY_TYPES:
        raise ValueError(f"Invalid entity_type: {entity_type}")
    allowed = PROVENANCE_FIELDS[entity_type]
    fields = {k: v for k, v in values.items() if k in allowed}
    if not fields:
        return
    table = ENTITY_TABLE_MAP[entity_type]
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM {table} WHERE id = %s FOR UPDATE", (entity_id,))
        row = cur.fetchone()
        if row is None:
            return  # entity gone — skip, so no orphan provenance is written
        current = row_to_dict(cur, row)
        for field_name, snapshot in fields.items():
            if _norm(current.get(field_name)) != _norm(snapshot):
                continue  # value changed since the AI wrote it — the human owns it now
            record(entity_type, entity_id, field_name, _norm(snapshot), "assistant", cur=cur)


def get_one(entity_type: str, entity_id: int, field_name: str) -> "dict | None":
    return pg_fetchone(
        """SELECT * FROM crm_field_provenance
           WHERE entity_type = %s AND entity_id = %s AND field_name = %s""",
        (entity_type, entity_id, field_name),
    )


def _load_current_values(entity_type: str, entity_id: int) -> dict:
    """Map every field_name → its live value from the entity row (standard columns only)."""
    table = ENTITY_TABLE_MAP.get(entity_type)
    if not table:
        raise ValueError(f"Invalid entity_type: {entity_type}")
    # table is from the fixed local map — never interpolating user input.
    row = pg_fetchone(f"SELECT * FROM {table} WHERE id = %s", (entity_id,))
    return dict(row) if row else {}


def get_provenance(
    entity_type: str,
    entity_id: int,
    include_confirmed: bool = False,
    include_stale: bool = False,
) -> list:
    """Provenance rows for an entity, each annotated with ``stale`` (snapshot no longer
    matches the live value). By default returns only the live badge state: unconfirmed AND
    not stale. Pass include_confirmed/include_stale for history."""
    if entity_type not in VALID_ENTITY_TYPES:
        raise ValueError(f"Invalid entity_type: {entity_type}")
    rows = pg_fetchall(
        """SELECT * FROM crm_field_provenance
           WHERE entity_type = %s AND entity_id = %s
           ORDER BY populated_at DESC""",
        (entity_type, entity_id),
    )
    if not rows:
        return []
    current = _load_current_values(entity_type, entity_id)
    out = []
    for r in rows:
        r["stale"] = _norm(current.get(r["field_name"])) != _norm(r["value_snapshot"])
        if not include_confirmed and r.get("confirmed_at") is not None:
            continue
        if not include_stale and r["stale"]:
            continue
        out.append(r)
    return out


def confirm(entity_type: str, entity_id: int, field_name: str) -> "dict | None":
    """Mark an AI-populated value human-confirmed (clears the badge). Returns the updated
    row; ``{"stale": True}`` if the live value no longer matches what was written (a human
    already changed it — nothing to confirm); None if no provenance row.

    One transaction: lock the entity row FOR UPDATE (consistent order with record_fields /
    delete_contact), then lock the provenance row FOR UPDATE and re-read its snapshot in the
    same snapshot, so a concurrent AI rewrite can't slip between the read and the confirm."""
    if entity_type not in VALID_ENTITY_TYPES:
        raise ValueError(f"Invalid entity_type: {entity_type}")
    table = ENTITY_TABLE_MAP[entity_type]
    now = _now()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM {table} WHERE id = %s FOR UPDATE", (entity_id,))
        entity_row = cur.fetchone()
        cur.execute(
            """SELECT value_snapshot FROM crm_field_provenance
               WHERE entity_type = %s AND entity_id = %s AND field_name = %s
               FOR UPDATE""",
            (entity_type, entity_id, field_name),
        )
        prov = cur.fetchone()
        if prov is None:
            return None
        snapshot = prov[0]
        live = row_to_dict(cur, entity_row).get(field_name) if entity_row else None
        if _norm(live) != _norm(snapshot):
            return {"stale": True}  # value changed under us — nothing to confirm
        cur.execute(
            """UPDATE crm_field_provenance SET confirmed_at = %s
               WHERE entity_type = %s AND entity_id = %s AND field_name = %s
               RETURNING *""",
            (now, entity_type, entity_id, field_name),
        )
        updated = row_to_dict(cur, cur.fetchone())
        # Audit note, adapted to CakeCRM's message-only crm_chatter (the blueprint's
        # event_type column doesn't exist). Direct insert inside this locked transaction —
        # deliberately NOT chatter_service.add_note: the entity row is already locked here,
        # and a confirm note is CRM housekeeping that must not trigger a touch recompute.
        cur.execute(
            """INSERT INTO crm_chatter (entity_type, entity_id, message, created_at)
               VALUES (%s, %s, %s, %s)""",
            (entity_type, entity_id,
             f"Confirmed AI-populated value for '{field_name}'.", now),
        )
    return updated


def clear(entity_type: str, entity_id: int, field_name: str) -> bool:
    """Drop a single provenance row. Returns True if a row was deleted."""
    return pg_execute(
        """DELETE FROM crm_field_provenance
           WHERE entity_type = %s AND entity_id = %s AND field_name = %s""",
        (entity_type, entity_id, field_name),
    ) > 0
