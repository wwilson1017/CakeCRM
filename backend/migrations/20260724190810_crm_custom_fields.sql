-- Custom fields (issue #19) — ported from cake_os apps/crm/field_service.py tables.
--
-- Two-table EAV: crm_field_definitions holds the user-authored field schema; crm_field_values
-- holds one TEXT row per (entity, field). Field values are stringified — booleans as '0'/'1',
-- numbers as their string form, dates as raw strings — matching the blueprint's semantics.
--
-- CHECK constraints on entity_type/field_type/is_required are a CakeCRM hardening convention
-- (cf. the 0/1 flag + enum CHECKs elsewhere in the schema); the blueprint relied on the DB
-- CHECK for field_type only. Both tables ship EMPTY — cake_os's pre-seeded field definitions are
-- TN Cheesecake business data and are deliberately NOT ported.
--
-- ⚠ crm_field_values is POLYMORPHIC: entity_type + entity_id reference contacts/companies/deals
-- but carry NO foreign key to those tables (only to crm_field_definitions). Exactly like
-- crm_chatter, a reused SERIAL id after TRUNCATE ... RESTART IDENTITY would let a new entity
-- inherit a deleted one's values, so EVERY entity-delete path (delete_contact, delete_company)
-- and _truncate_all MUST clean crm_field_values explicitly. See backend/crm/service.py.

CREATE TABLE IF NOT EXISTS crm_field_definitions (
    id               SERIAL PRIMARY KEY,
    entity_type      TEXT NOT NULL CHECK (entity_type IN ('contact', 'company', 'deal')),
    name             TEXT NOT NULL,
    field_key        TEXT NOT NULL,
    field_type       TEXT NOT NULL CHECK (field_type IN ('text', 'number', 'boolean', 'date', 'select')),
    dropdown_options TEXT,                                   -- JSON-string array; NULL unless field_type = 'select'
    is_required      INTEGER NOT NULL DEFAULT 0 CHECK (is_required IN (0, 1)),  -- advisory only (never enforced server-side)
    display_order    INTEGER NOT NULL DEFAULT 0,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (entity_type, field_key),
    UNIQUE (entity_type, name)
);

CREATE TABLE IF NOT EXISTS crm_field_values (
    id               SERIAL PRIMARY KEY,
    entity_type      TEXT NOT NULL CHECK (entity_type IN ('contact', 'company', 'deal')),
    entity_id        INTEGER NOT NULL,                       -- polymorphic — NO FK to contacts/companies/deals (see header)
    field_id         INTEGER NOT NULL REFERENCES crm_field_definitions(id) ON DELETE CASCADE,
    value            TEXT,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by_email TEXT,
    UNIQUE (entity_type, entity_id, field_id)
);

CREATE INDEX IF NOT EXISTS idx_crm_field_values_entity ON crm_field_values(entity_type, entity_id);
