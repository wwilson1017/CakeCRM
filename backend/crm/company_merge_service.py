"""Preview and atomically merge duplicate companies, preserving their audit history."""

from core.postgres import get_connection
from crm import chatter_service, scoring_service, service as crm

_FILL_FIELDS = ("domain", "industry", "phone", "address", "source")
_COLUMNS = ("id", "name", "status", "merged_into_id", "notes") + _FILL_FIELDS
_NORM = r"LOWER(btrim(%s, E' \t\n\r\f\x0b'))"


def _blank(value) -> bool:
    return value is None or not str(value).strip()


def _count(cur, sql, params) -> int:
    cur.execute(sql, params)
    return cur.fetchone()[0]


def _pair(cur, survivor_id, absorbed_id, *, lock=False):
    if survivor_id == absorbed_id:
        raise ValueError("Cannot merge a company into itself")
    cur.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM companies WHERE id IN (%s, %s) "
        "ORDER BY id" + (" FOR UPDATE" if lock else ""),
        (survivor_id, absorbed_id),
    )
    rows = {row[0]: dict(zip(_COLUMNS, row)) for row in cur.fetchall()}
    missing = sorted({survivor_id, absorbed_id} - rows.keys())
    if missing:
        raise LookupError(f"Company not found: {', '.join(map(str, missing))}")
    survivor, absorbed = rows[survivor_id], rows[absorbed_id]
    if survivor["merged_into_id"] is not None:
        raise ValueError(
            f"Company #{survivor_id} was merged into #{survivor['merged_into_id']}; "
            "merge into that company instead"
        )
    if survivor["status"] == "archived":
        raise ValueError(f"Cannot merge: company #{survivor_id} is archived — restore it first")
    if absorbed["merged_into_id"] is not None:
        raise ValueError(f"Company #{absorbed_id} was already merged into #{absorbed['merged_into_id']}")
    return survivor, absorbed


def _rename_target(cur, survivor, rename_to):
    name = (rename_to or "").strip(crm._WS)
    if not name:
        return None
    cur.execute(f"SELECT {_NORM % '%s'} = {_NORM % '%s'}", (name, survivor["name"]))
    if cur.fetchone()[0]:
        return None
    cur.execute(
        f"SELECT id FROM companies WHERE {_NORM % 'name'} = {_NORM % '%s'} AND id <> %s",
        (name, survivor["id"]),
    )
    row = cur.fetchone()
    if row:
        raise ValueError(f"Cannot rename: company #{row[0]} already has that name")
    return name


def _plan(cur, survivor, absorbed, rename_to):
    s, a = survivor["id"], absorbed["id"]
    moves = {
        "contacts": _count(cur, "SELECT count(*) FROM contacts WHERE company_id = %s", (a,)),
        "deals": _count(cur, "SELECT count(*) FROM deals WHERE company_id = %s", (a,)),
        "archived_deals": _count(
            cur, "SELECT count(*) FROM deals WHERE company_id = %s AND archived_at IS NOT NULL", (a,),
        ),
        "todos": _count(
            cur, f"""SELECT count(*) FROM todos WHERE completed = 0
                AND {crm.NOT_DROPPED_TODO} AND {crm.LIVE_TODO_PREDICATE}
                AND (contact_id IN (SELECT id FROM contacts WHERE company_id = %s)
                     OR deal_id IN (SELECT id FROM deals WHERE company_id = %s))""", (a, a),
        ),
        "notes": _count(
            cur, "SELECT count(*) FROM crm_chatter WHERE entity_type = 'company' AND entity_id = %s", (a,),
        ),
        "custom_fields": _count(
            cur, """SELECT count(*) FROM crm_field_values s
                WHERE s.entity_type = 'company' AND s.entity_id = %s
                  AND s.value IS NOT NULL AND s.value <> ''
                  AND NOT EXISTS (SELECT 1 FROM crm_field_values w
                      WHERE w.entity_type = 'company' AND w.entity_id = %s
                        AND w.field_id = s.field_id AND w.value IS NOT NULL AND w.value <> '')""",
            (a, s),
        ),
    }
    fields = [{"field": f, "old": survivor[f], "new": absorbed[f]}
              for f in _FILL_FIELDS if _blank(survivor[f]) and not _blank(absorbed[f])]
    if not _blank(absorbed["notes"]) and absorbed["notes"].strip() != (survivor["notes"] or "").strip():
        joined = f"From {absorbed['name']}: {absorbed['notes'].strip()}"
        if not _blank(survivor["notes"]):
            joined = f"{survivor['notes'].rstrip()}\n\n{joined}"
        fields.append({"field": "notes", "old": survivor["notes"], "new": joined})
    rename = _rename_target(cur, survivor, rename_to)
    if rename:
        fields.insert(0, {"field": "name", "old": survivor["name"], "new": rename})
    return {
        "survivor": {"id": s, "name": rename or survivor["name"]},
        "absorbed": {"id": a, "name": absorbed["name"], "status": absorbed["status"]},
        "moves": moves, "fields": fields,
    }


def plan_merge(survivor_id, absorbed_id, *, rename_to=None) -> dict:
    """Read-only current estimate; merge recomputes it under the exclusive gate."""
    with get_connection() as conn:
        cur = conn.cursor()
        survivor, absorbed = _pair(cur, survivor_id, absorbed_id)
        plan = _plan(cur, survivor, absorbed, rename_to)
        conn.rollback()
        return plan


def merge_companies(survivor_id, absorbed_id, *, actor_id: int | None,
                    reason: str | None, rename_to=None) -> dict:
    # Reuse the archive validator and audit-note helper so company lifecycle rules stay shared.
    text = crm._archive_reason(reason, "a company")
    now = crm._now()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT company_merge_gate()")
        survivor, absorbed = _pair(cur, survivor_id, absorbed_id, lock=True)
        plan = _plan(cur, survivor, absorbed, rename_to)
        s, a = survivor_id, absorbed_id
        final_name = plan["survivor"]["name"]
        if plan["fields"]:
            # Column names come only from _plan's fixed standard-field list, never input.
            assignments = ", ".join(f"{f['field']} = %s" for f in plan["fields"])
            cur.execute(
                f"UPDATE companies SET {assignments}, updated_at = %s WHERE id = %s",
                [f["new"] for f in plan["fields"]] + [now, s],
            )
        if final_name != survivor["name"]:
            cur.execute("UPDATE contacts SET company = %s WHERE company_id = %s AND company = %s",
                        (final_name, s, survivor["name"]))
        # Relinking is not an edit/touch: no updated_at bump on contacts or deals.
        cur.execute("UPDATE contacts SET company_id = %s, company = %s WHERE company_id = %s RETURNING id",
                    (s, final_name, a))
        contact_ids = [r[0] for r in cur.fetchall()]
        # Deliberate _write_deal_update exception: company_id is user-writable, but this
        # relink has no stage/closed_on/archive effects and must stay in ONE transaction.
        cur.execute("UPDATE deals SET company_id = %s WHERE company_id = %s RETURNING id", (s, a))
        deal_ids = [r[0] for r in cur.fetchall()]
        cur.execute(
            """INSERT INTO crm_chatter (entity_type, entity_id, message, created_at, archived, system_note)
               SELECT 'company', %s, left(%s || message, %s), created_at, archived, system_note
                 FROM crm_chatter WHERE entity_type = 'company' AND entity_id = %s""",
            (s, f"[Merged from company #{a}] ", chatter_service.MAX_MESSAGE_LEN, a),
        )
        # Copies deliberately exclude attachments and mentions; the source retains them.
        cur.execute(
            """INSERT INTO crm_field_values
                   (entity_type, entity_id, field_id, value, updated_at, updated_by_email)
               SELECT 'company', %s, s.field_id, s.value, s.updated_at, s.updated_by_email
                 FROM crm_field_values s
                WHERE s.entity_type = 'company' AND s.entity_id = %s
                  AND s.value IS NOT NULL AND s.value <> ''
               ON CONFLICT (entity_type, entity_id, field_id)
               DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at,
                             updated_by_email = EXCLUDED.updated_by_email
                WHERE crm_field_values.value IS NULL OR crm_field_values.value = ''""", (s, a),
        )
        cur.execute(
            "INSERT INTO crm_chatter (entity_type, entity_id, message, created_at, author_id, system_note) "
            "VALUES ('company', %s, %s, %s, %s, true)",
            (s, f'Merged company #{a} ("{absorbed["name"]}") into this company — {text}', now, actor_id),
        )
        cur.execute(
            "UPDATE companies SET status = 'archived', merged_into_id = %s, updated_at = %s WHERE id = %s",
            (s, now, a),
        )
        # AFTER copying: this merge's archive audit belongs only on the absorbed row,
        # even when that company was already archived by hand.
        crm._insert_housekeeping_note_cur(
            cur, "company", a, scoring_service.ARCHIVE_NOTE_PREFIX + f"Merged into company #{s}: {text}",
            actor_id, now,
        )
    scoring_service.score_on_event(deal_ids=deal_ids, contact_ids=contact_ids)
    return plan
