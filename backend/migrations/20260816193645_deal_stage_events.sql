-- Deal stage-change history (issue #22, Phase 1).
--
-- CakeCRM records no stage transitions today: #15 deliberately dropped chatter's
-- audit-event columns, and #20's analytics therefore had to drop every
-- stage-duration metric (conversion rates, time-in-stage) for lack of history.
--
-- This table starts accumulating that history NOW, so the metrics are answerable
-- whenever they are built rather than launching against an empty log. Rows are
-- written in the SAME transaction as the stage change itself (service.py), so the
-- log can never disagree with deals.stage.
--
-- Zero UI. Append-only: nothing updates or deletes a row except the CRM-reset
-- truncate sweeps.
--
-- deal_id carries a real FK (deals is truncated in the same TRUNCATE statement, so
-- unlike the gmail_scan audit ledgers there is no reset hazard) and cascades, so a
-- future hard delete of a deal cannot orphan its history.

CREATE TABLE IF NOT EXISTS deal_stage_events (
    id         SERIAL PRIMARY KEY,
    deal_id    INTEGER NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
    old_stage  TEXT NOT NULL DEFAULT '',
    new_stage  TEXT NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- "How long has this deal been in its current stage" reads the newest row per deal.
CREATE INDEX IF NOT EXISTS idx_deal_stage_events_deal
    ON deal_stage_events(deal_id, changed_at DESC);
