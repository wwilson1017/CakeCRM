-- Deal lifecycle columns (issue #22, Phase 1).
--
-- CakeCRM deals previously had no archived state and no lost-reason field: won/lost
-- were only *stages*. The assistant-parity work adds explicit lifecycle verbs
-- (crm_mark_deal_won / crm_mark_deal_lost / crm_archive_deal / crm_merge_deals),
-- which need somewhere to record "why we lost" and somewhere to put a merged-away
-- deal that is not a hard delete.
--
-- archived_at (NULL = live) is a SOFT archive: nothing is ever deleted, and the row
-- stays available for restore. Every deal-reading query in backend/crm/ filters on
-- `archived_at IS NULL` so an archived deal leaves the pipeline, the dashboard, the
-- analytics and the agent tools together.
--
-- lost_reason is free text, '' when unset. It is CLEARED whenever a deal leaves the
-- 'lost' stage (see service.update_deal / update_deal_stage / mark_deal_won) so a
-- reopened deal can never carry a stale reason into the timeline or #20's win/loss
-- reads — the behavior the cake_os blueprint had to fix after our snapshot.

ALTER TABLE deals ADD COLUMN IF NOT EXISTS lost_reason TEXT        NOT NULL DEFAULT '';
ALTER TABLE deals ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;

-- Partial index on the live rows only: every board/list/analytics query carries
-- `archived_at IS NULL`, and archived deals are the rare minority, so indexing the
-- live subset keeps the index small while still covering the hot predicate.
CREATE INDEX IF NOT EXISTS idx_deals_live ON deals(id) WHERE archived_at IS NULL;
