-- CRM chatter / notes threads (issue #15): threaded free-text notes on deals and
-- contacts, rendered alongside the activity_log timeline on each entity's detail
-- view, plus assistant tools to read/append them. Ported from the manual-notes
-- half of cake_os/backend/apps/crm/chatter_service.py.
--
-- Departures from the blueprint, each forced by a CakeCRM rule:
--   * No user_email / author-only permission checks — single-tenant v1 (no user_id
--     anywhere; author-only checks can never fire with one user).
--   * No touch_count_service recompute hook — that service is a later port.
--   * The blueprint's audit-event columns (event_type/field_name/old_value/
--     new_value) are dropped: CakeCRM's activity_log already owns the audit lane,
--     and keeping them would only be dead weight (message is NOT NULL here).
--
-- Polymorphic (entity_type, entity_id) — matches the blueprint + the assistant-tool
-- signatures and makes a future 'company' entity (issue #13) a zero-migration add.
-- No FK on entity_id (polymorphism can't express one); referential integrity is
-- kept by (a) validating the target exists in the service before insert and (b)
-- clearing chatter in delete_contact / the CRM truncate paths (service.py), so a
-- reused SERIAL id can never inherit an old entity's notes.
CREATE TABLE IF NOT EXISTS crm_chatter (
    id          SERIAL PRIMARY KEY,
    entity_type TEXT NOT NULL,               -- 'deal' | 'contact' (| 'company' later)
    entity_id   INTEGER NOT NULL,
    message     TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ,                 -- set on edit; NULL means never edited
    archived    INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1))
);
-- Composite index serves the only read shape: notes for one entity, newest
-- first. Includes the id DESC tiebreaker so it matches get_chatter's exact
-- ORDER BY (created_at DESC, id DESC).
CREATE INDEX IF NOT EXISTS idx_crm_chatter_entity ON crm_chatter(entity_type, entity_id, created_at DESC, id DESC);
