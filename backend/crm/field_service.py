"""
CakeCRM — custom field definitions + values (issue #19).

Ported from cake_os ``apps/crm/field_service.py``. Two-table EAV:
``crm_field_definitions`` (the user-authored schema) and ``crm_field_values`` (one
TEXT row per entity+field). Follows the CakeCRM service conventions in
``service.py``: ``%s`` placeholders, ``INSERT ... RETURNING id`` then re-select,
plain-dict returns, ``ValueError`` raised in the service (router/tools translate).

Deliberate divergences from the blueprint (all documented in the PR):
  * No field-change audit. The source logs a ``chatter_service.log_event`` on every
    changed value; CakeCRM has no such function (its chatter is manual notes on
    deal/contact only, and ``log_activity`` cannot address companies). Per-value
    attribution survives via ``updated_by_email``/``updated_at`` on the value row.
  * ``set_field_values`` runs the whole write in ONE ``get_connection()``
    transaction: it locks the entity row ``FOR UPDATE`` (serializing against
    ``delete_contact``/``delete_company``, which take the same lock — this is what
    makes the polymorphic-value cleanup in those deletes airtight) and bulk-fetches
    the referenced definitions ``FOR SHARE`` (blocking a concurrent
    ``delete_field_definition``, which would otherwise FK-violate mid-write). A
    validation error rolls the whole batch back (all-or-nothing for validated writes).
  * Empty string clears a value and bypasses type validation, fixing a source bug
    where clearing a number/select field raised (``float("")`` / not-in-options).

``is_required`` is stored + surfaced in the UI but NEVER enforced server-side
(advisory only) — matching the blueprint.
"""

import json
import logging
import math
import re
from datetime import date, datetime, timezone

from core.postgres import get_connection, pg_execute, pg_fetchall, pg_fetchone

logger = logging.getLogger(__name__)

VALID_ENTITY_TYPES = {"contact", "company", "deal"}
# Polymorphic value rows carry entity_type + entity_id but no FK to these tables.
ENTITY_TABLE_MAP = {"contact": "contacts", "company": "companies", "deal": "deals"}
FIELD_TYPES = {"text", "number", "boolean", "date", "select"}

# Guard against an oversized request holding the entity lock while doing N upserts
# (mirrors chatter_service._MAX_LIMIT). No real field schema approaches this.
_MAX_FIELDS_PER_WRITE = 200

# Bounds on user/assistant-supplied strings. The value cap mirrors chatter's
# MAX_MESSAGE_LEN; without these an authenticated caller (including the assistant via
# crm_set_*_fields) could write unbounded TEXT that then flows back through the read
# tools into the model's context. Enforced in the service (the single source of truth,
# since the assistant tools bypass the router's Pydantic layer).
_MAX_NAME_LEN = 100
_MAX_KEY_LEN = 64
_MAX_OPTION_LEN = 200
_MAX_OPTIONS = 100
_MAX_VALUE_LEN = 10000


def _now() -> str:
    """UTC ISO-8601 timestamp (matches service._now())."""
    return datetime.now(timezone.utc).isoformat()


def _slugify_key(name: str) -> str:
    """Derive a field_key from a display name — the Python mirror of the frontend
    formula: lowercase, whitespace runs to '_', strip anything but [a-z0-9_]."""
    s = re.sub(r"\s+", "_", (name or "").lower())
    return re.sub(r"[^a-z0-9_]", "", s)


def _parse_dropdown(row: dict | None) -> dict | None:
    """Decode the dropdown_options JSON string on a definition row into a list."""
    if not row:
        return row
    if row.get("dropdown_options"):
        try:
            row["dropdown_options"] = json.loads(row["dropdown_options"])
        except (json.JSONDecodeError, TypeError):
            row["dropdown_options"] = []
    return row


def _options_json(options) -> str | None:
    """Validate + JSON-encode dropdown options (bounded, unique, non-blank), or None."""
    if not options:
        return None
    if len(options) > _MAX_OPTIONS:
        raise ValueError(f"Too many dropdown options (max {_MAX_OPTIONS})")
    cleaned = [str(o).strip() for o in options]
    if any(not o for o in cleaned):
        raise ValueError("Dropdown options cannot be blank")
    if any(len(o) > _MAX_OPTION_LEN for o in cleaned):
        raise ValueError(f"Dropdown option too long (max {_MAX_OPTION_LEN} chars)")
    if len(set(cleaned)) != len(cleaned):
        raise ValueError("Dropdown options must be unique")
    return json.dumps(cleaned)


def entity_exists(entity_type: str, entity_id: int) -> bool:
    """True if the given contact/company/deal row exists. Kept in the service layer
    so callers (router, tools) don't hand-roll raw entity-table SQL."""
    table = ENTITY_TABLE_MAP.get(entity_type)
    if not table:
        return False
    return pg_fetchone(f"SELECT id FROM {table} WHERE id = %s", (entity_id,)) is not None


# ── Definitions ───────────────────────────────────────────────────────────────

def list_field_definitions(entity_type: str | None = None) -> list[dict]:
    """All field definitions, or those for one entity type, ordered for display.

    Raises ValueError on an invalid *provided* entity_type (the source silently
    returned []); the ValueError-in-service convention lets the router 400 cleanly.
    """
    if entity_type is not None:
        if entity_type not in VALID_ENTITY_TYPES:
            raise ValueError(f"Invalid entity_type: {entity_type}")
        rows = pg_fetchall(
            "SELECT * FROM crm_field_definitions WHERE entity_type = %s "
            "ORDER BY display_order ASC, id ASC",
            (entity_type,),
        )
    else:
        rows = pg_fetchall(
            "SELECT * FROM crm_field_definitions "
            "ORDER BY entity_type ASC, display_order ASC, id ASC"
        )
    return [_parse_dropdown(r) for r in rows]


def create_field_definition(data: dict) -> dict:
    """Create a field definition. entity_type/field_type/name are validated in the
    service; field_key is slugified server-side (never trusted from the client);
    display_order is assigned server-side as max+10 within the entity type."""
    entity_type = data.get("entity_type")
    if entity_type not in VALID_ENTITY_TYPES:
        raise ValueError(f"Invalid entity_type: {entity_type}")
    field_type = data.get("field_type")
    if field_type not in FIELD_TYPES:
        raise ValueError(f"Invalid field_type: {field_type}")

    name = (data.get("name") or "").strip()
    if not name:
        raise ValueError("Field name is required")
    if len(name) > _MAX_NAME_LEN:
        raise ValueError(f"Field name too long (max {_MAX_NAME_LEN} chars)")

    # Always slugify — a client-supplied key is normalized, never trusted verbatim.
    field_key = _slugify_key((data.get("field_key") or "").strip() or name)
    if not field_key:
        raise ValueError("Could not derive a field key from the name")
    if len(field_key) > _MAX_KEY_LEN:
        raise ValueError(f"Field key too long (max {_MAX_KEY_LEN} chars)")

    options_json = _options_json(data.get("dropdown_options"))
    if field_type == "select" and not options_json:
        # A select with no options would otherwise accept ANY value (validate skips
        # the membership check when the list is empty) — require at least one.
        raise ValueError("A select field requires at least one option")
    now = _now()

    # Single statement: the display_order (server-assigned max+10 within the entity
    # type) is computed inline and the full row is returned atomically, so a
    # concurrent clear_all can't delete the row between insert and re-select (which
    # would return None), and there is no separate MAX round-trip.
    row = pg_fetchone(
        """INSERT INTO crm_field_definitions
           (entity_type, name, field_key, field_type, dropdown_options,
            is_required, display_order, created_at, updated_at)
           VALUES (%s, %s, %s, %s, %s, %s,
                   COALESCE((SELECT MAX(display_order) FROM crm_field_definitions
                             WHERE entity_type = %s), 0) + 10,
                   %s, %s)
           RETURNING *""",
        (
            entity_type, name, field_key, field_type, options_json,
            int(bool(data.get("is_required", False))), entity_type, now, now,
        ),
    )
    return _parse_dropdown(row)


def update_field_definition(field_id: int, data: dict) -> dict | None:
    """Partial update of a definition. Only name/dropdown_options/is_required/
    display_order are mutable (entity_type/field_key/field_type are immutable).
    Returns None when the field does not exist."""
    existing = pg_fetchone(
        "SELECT field_type FROM crm_field_definitions WHERE id = %s", (field_id,)
    )
    if not existing:
        return None

    fields: list[str] = []
    params: list = []
    if "name" in data and data["name"] is not None:
        name = data["name"].strip()
        if not name:
            raise ValueError("Field name cannot be blank")
        if len(name) > _MAX_NAME_LEN:
            raise ValueError(f"Field name too long (max {_MAX_NAME_LEN} chars)")
        fields.append("name = %s")
        params.append(name)
    if "dropdown_options" in data:
        opts_json = _options_json(data["dropdown_options"])
        # Preserve the same invariant the create path enforces: a select must keep
        # >=1 option (an empty list would make validate accept ANY value), and only
        # a select may carry options at all.
        if existing["field_type"] == "select":
            if not opts_json:
                raise ValueError("A select field requires at least one option")
        elif opts_json is not None:
            raise ValueError("Only select fields can have dropdown options")
        fields.append("dropdown_options = %s")
        params.append(opts_json)
    if "is_required" in data and data["is_required"] is not None:
        fields.append("is_required = %s")
        params.append(int(bool(data["is_required"])))
    if "display_order" in data and data["display_order"] is not None:
        fields.append("display_order = %s")
        params.append(int(data["display_order"]))

    if not fields:
        return _parse_dropdown(
            pg_fetchone("SELECT * FROM crm_field_definitions WHERE id = %s", (field_id,))
        )

    fields.append("updated_at = %s")
    params.append(_now())
    params.append(field_id)
    pg_execute(f"UPDATE crm_field_definitions SET {', '.join(fields)} WHERE id = %s", params)
    return _parse_dropdown(
        pg_fetchone("SELECT * FROM crm_field_definitions WHERE id = %s", (field_id,))
    )


def delete_field_definition(field_id: int) -> bool:
    """Hard-delete a definition; its crm_field_values rows cascade via the FK. Returns
    the DELETE's own rowcount (matching delete_task/delete_activity), so a racing
    double-delete reports 404 rather than a misleading 200 from a stale pre-check."""
    return pg_execute("DELETE FROM crm_field_definitions WHERE id = %s", (field_id,)) > 0


# ── Values ────────────────────────────────────────────────────────────────────

def get_field_values(entity_type: str, entity_id: int) -> list[dict]:
    """Every definition for the entity type LEFT JOINed to this entity's values, so
    unset fields appear with value=None. Ordered for display."""
    if entity_type not in VALID_ENTITY_TYPES:
        raise ValueError(f"Invalid entity_type: {entity_type}")

    rows = pg_fetchall(
        """SELECT d.id AS field_id, d.name, d.field_key, d.field_type,
                  d.dropdown_options, d.is_required,
                  v.value, v.updated_at AS value_updated_at, v.updated_by_email
           FROM crm_field_definitions d
           LEFT JOIN crm_field_values v
             ON v.field_id = d.id AND v.entity_type = %s AND v.entity_id = %s
           WHERE d.entity_type = %s
           ORDER BY d.display_order ASC, d.id ASC""",
        (entity_type, entity_id, entity_type),
    )
    return [_parse_dropdown(r) for r in rows]


def get_field_values_batch(entity_type: str, entity_ids: list[int]) -> dict[int, dict]:
    """Bulk {entity_id: {field_key: value}} for multiple entities (non-empty values
    only). For future list views — no v1 UI consumer, ported for parity."""
    if entity_type not in VALID_ENTITY_TYPES or not entity_ids:
        return {}

    placeholders = ",".join("%s" for _ in entity_ids)
    rows = pg_fetchall(
        f"""SELECT v.entity_id, d.field_key, v.value
            FROM crm_field_values v
            JOIN crm_field_definitions d ON d.id = v.field_id
            WHERE v.entity_type = %s AND v.entity_id IN ({placeholders})
              AND v.value IS NOT NULL AND v.value != ''""",
        [entity_type, *entity_ids],
    )
    result: dict[int, dict] = {}
    for row in rows:
        result.setdefault(row["entity_id"], {})[row["field_key"]] = row["value"]
    return result


def set_field_values(
    entity_type: str, entity_id: int, values: dict[str, str], user_email: str
) -> dict:
    """Upsert custom-field values for one entity, atomically.

    ``values`` maps field-id strings → values. The whole write runs in one
    transaction: the entity row is locked FOR UPDATE (serializing against
    delete_contact/delete_company) and the referenced definitions are bulk-fetched
    FOR SHARE (blocking a concurrent delete_field_definition). Unknown field ids are
    collected into ``errors`` without raising; a type-validation error rolls the whole
    batch back. Returns {"ok": True, "updated": N, "errors": [...]}."""
    if entity_type not in VALID_ENTITY_TYPES:
        raise ValueError(f"Invalid entity_type: {entity_type}")
    if len(values) > _MAX_FIELDS_PER_WRITE:
        raise ValueError(f"Too many fields in one request (max {_MAX_FIELDS_PER_WRITE})")

    table = ENTITY_TABLE_MAP[entity_type]
    now = _now()
    errors: list[str] = []

    # Parse the field-id keys up front; non-integer keys are reported, not fatal.
    id_to_raw: dict[int, str] = {}
    for field_id_str in values:
        try:
            fid = int(field_id_str)
        except (ValueError, TypeError):
            errors.append(f"Invalid field id: {field_id_str!r}")
            continue
        # "1"/"01"/"+1" normalize to the same id — surface the collision instead of
        # letting one value silently overwrite the other.
        if fid in id_to_raw:
            errors.append(f"Duplicate field id: {field_id_str!r}")
            continue
        id_to_raw[fid] = field_id_str

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT id FROM {table} WHERE id = %s FOR UPDATE", (entity_id,))
        if cur.fetchone() is None:
            raise ValueError(f"{entity_type} with id {entity_id} not found")

        defs_by_id: dict[int, dict] = {}
        if id_to_raw:
            placeholders = ",".join("%s" for _ in id_to_raw)
            cur.execute(
                f"""SELECT id, name, field_type, dropdown_options
                    FROM crm_field_definitions
                    WHERE entity_type = %s AND id IN ({placeholders})
                    ORDER BY id ASC FOR SHARE""",
                [entity_type, *id_to_raw.keys()],
            )
            for r in cur.fetchall():
                defs_by_id[r[0]] = {"name": r[1], "field_type": r[2], "dropdown_options": r[3]}

        # Validate everything first (a raise here rolls back the whole txn), then upsert.
        writes: list[tuple[int, str]] = []
        for field_id, field_id_str in id_to_raw.items():
            field_def = defs_by_id.get(field_id)
            if field_def is None:
                errors.append(f"Field {field_id} not found for {entity_type}")
                continue
            value = values[field_id_str]
            validate_field_value(field_def, value)
            writes.append((field_id, value))

        for field_id, value in writes:
            cur.execute(
                """INSERT INTO crm_field_values
                       (entity_type, entity_id, field_id, value, updated_at, updated_by_email)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (entity_type, entity_id, field_id)
                   DO UPDATE SET value = EXCLUDED.value,
                                 updated_at = EXCLUDED.updated_at,
                                 updated_by_email = EXCLUDED.updated_by_email""",
                (entity_type, entity_id, field_id, value, now, user_email),
            )

    return {"ok": True, "updated": len(writes), "errors": errors}


def validate_field_value(field_def: dict, value: str) -> None:
    """Type-validate a single value. Empty string / None means "clear" and skips
    validation (fixes a source bug where clearing a number/select field raised)."""
    if value is None or value == "":
        return
    if len(value) > _MAX_VALUE_LEN:
        raise ValueError(f"Field '{field_def['name']}' value too long (max {_MAX_VALUE_LEN} chars)")
    field_type = field_def["field_type"]
    if field_type == "number":
        # Pin the wire form to what <input type="number"> produces: reject surrounding
        # whitespace and underscore grouping that float() would otherwise accept but
        # the UI can't render.
        if value.strip() != value or "_" in value:
            raise ValueError(f"Field '{field_def['name']}' requires a plain number, got '{value}'")
        try:
            parsed = float(value)
        except (ValueError, TypeError):
            raise ValueError(f"Field '{field_def['name']}' requires a number, got '{value}'") from None
        # float() also accepts 'nan'/'inf' — reject those; a field can't hold them.
        if not math.isfinite(parsed):
            raise ValueError(f"Field '{field_def['name']}' requires a finite number, got '{value}'")
    elif field_type == "boolean":
        if value not in ("0", "1"):
            raise ValueError(f"Field '{field_def['name']}' requires '0' or '1', got '{value}'")
    elif field_type == "date":
        # Pin to strict YYYY-MM-DD (what <input type="date"> produces) — date.fromisoformat
        # alone accepts basic-format/ISO-week forms that vary by Python version and the
        # date input can't render.
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError(
                f"Field '{field_def['name']}' requires an ISO date (YYYY-MM-DD), got '{value}'"
            )
        try:
            date.fromisoformat(value)
        except ValueError:
            raise ValueError(f"Field '{field_def['name']}' is not a valid date: '{value}'") from None
    elif field_type == "select":
        options = json.loads(field_def["dropdown_options"]) if field_def.get("dropdown_options") else []
        if options and value not in options:
            raise ValueError(
                f"Field '{field_def['name']}' value '{value}' not in options: {options}"
            )
