-- Field provenance (issue #16): which standard CRM fields the AI assistant wrote,
-- so the UI can show an "AI" badge until a human confirms (or overwrites) the value.
--
-- The badge is a COMPUTED contract, not a stored flag: it shows only while the row
-- is unconfirmed (confirmed_at IS NULL) AND still "live" — value_snapshot equals the
-- field's current value. A human editing the field through the normal CRM path
-- changes the live value, so the snapshot no longer matches and the badge
-- auto-disappears. See crm/provenance_service.py for the lifecycle.
--
-- One row per (entity_type, entity_id, field_name). field_name is a standard column
-- name (e.g. 'phone'); issue #19 (custom fields) will later extend it with a
-- 'cf:<field_key>' namespace — this table needs no change for that.
--
-- Polymorphic (entity_type, entity_id) like crm_chatter — no FK (polymorphism can't
-- express one). Referential integrity is kept in the service layer: record_fields
-- locks the entity row FOR UPDATE and skips a vanished entity, and delete_contact +
-- every CRM-truncate path clear rows here, so a reused SERIAL id can never inherit
-- a deleted entity's badges.
--
-- Single-tenant v1: no populated_by / confirmed_by attribution columns (there is no
-- user_id anywhere yet). source_detail / confidence are nullable, reserved for a
-- future web-enrichment producer; today the only producer is the assistant.
CREATE TABLE IF NOT EXISTS crm_field_provenance (
    id             SERIAL PRIMARY KEY,
    entity_type    TEXT NOT NULL,            -- 'deal' | 'contact'
    entity_id      INTEGER NOT NULL,
    field_name     TEXT NOT NULL,            -- standard column name, e.g. 'phone'
    value_snapshot TEXT,                     -- _norm() of the value as the AI wrote it
    source         TEXT NOT NULL,            -- 'assistant' (VALID_SOURCES)
    source_detail  TEXT,                     -- future enrichment detail (nullable)
    confidence     DOUBLE PRECISION,         -- future enrichment confidence (nullable)
    populated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    confirmed_at   TIMESTAMPTZ,              -- NULL = unconfirmed (badge shows)
    UNIQUE (entity_type, entity_id, field_name)  -- also the lookup index + ON CONFLICT target
);
