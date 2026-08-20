-- Proactive heartbeat: daily pipeline digest + stale/untouched nudges (issue #22, Phase 3).
--
-- Two pieces of state, both deliberately small:
--
-- 1. Digest/nudge bookkeeping rides the EXISTING heartbeat_state singleton rather than
--    a new table. It is the same kind of fact the row already holds (when did this
--    periodic job last run, and how did it go), and keeping it there means the whole
--    heartbeat's state stays readable in one row.
--
-- 2. proactive_nudges is the re-nudge guard: one row per (entity, kind) recording when
--    we last nagged about it, so a neglected deal is mentioned once a week rather than
--    every four hours. It is POLYMORPHIC (entity_type = 'deal' | 'contact'), so like
--    crm_field_values it carries NO foreign key — which means it must be swept
--    explicitly at every CRM-reset site (service._truncate_all), since no CASCADE will
--    do it for us. A stale row for a deleted entity is harmless (it can only suppress a
--    nudge for an id that no longer exists) but the reset must still clear it or a
--    reseeded CRM would inherit the old cooldowns.

ALTER TABLE heartbeat_state
    ADD COLUMN IF NOT EXISTS proactive_enabled  BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS last_digest_at     TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_digest_status TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS last_nudge_at      TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS proactive_nudges (
    id           SERIAL PRIMARY KEY,
    entity_type  TEXT NOT NULL,
    entity_id    INTEGER NOT NULL,
    kind         TEXT NOT NULL,
    last_sent_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (entity_type, entity_id, kind)
);

-- The cooldown read is "which of these entities were nudged since <cutoff>".
CREATE INDEX IF NOT EXISTS idx_proactive_nudges_sent ON proactive_nudges(last_sent_at);
