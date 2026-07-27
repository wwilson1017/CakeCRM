-- Lead scoring (issue #18) — a pure-algorithmic 0-100 "how alive is this?" score
-- for deals and contacts, computed from CRM-native signals (stage, engagement,
-- value, linkage, recency, age). System-owned and derived: NULL means "never
-- scored yet" (distinct from a real score of 0), so a DESC NULLS LAST sort sinks
-- unscored rows. Deliberately a DEDICATED column, NOT deals.probability — that
-- field is a live, user/assistant-editable, provenance-tracked "win probability"
-- and an auto-recompute would clobber it (same reasoning #16 used for ai_touch_count).
-- lead_score is never user/tool/assistant-writable.
ALTER TABLE deals    ADD COLUMN IF NOT EXISTS lead_score    INTEGER CHECK (lead_score BETWEEN 0 AND 100);
ALTER TABLE deals    ADD COLUMN IF NOT EXISTS lead_score_at TIMESTAMPTZ;
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS lead_score    INTEGER CHECK (lead_score BETWEEN 0 AND 100);
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS lead_score_at TIMESTAMPTZ;

-- Daily time-decay refresh bookkeeping (singleton crm_meta, id = 1).
ALTER TABLE crm_meta ADD COLUMN IF NOT EXISTS scores_refreshed_at TIMESTAMPTZ;

-- Full composite index matching the ONE backend score sort (contacts list):
-- ORDER BY lead_score DESC NULLS LAST, updated_at DESC, id DESC. NOT partial — the query
-- returns scored AND unscored contacts, so a `WHERE lead_score IS NOT NULL` partial index
-- couldn't satisfy the full ordering and Postgres would fall back to a sort. (Deals sort
-- client-side, so no deal score index is needed.)
CREATE INDEX IF NOT EXISTS idx_contacts_lead_score
    ON contacts (lead_score DESC NULLS LAST, updated_at DESC, id DESC);

-- Indexes for the daily time-decay refresh's stale-row scan
-- (WHERE lead_score_at IS NULL OR lead_score_at < cutoff ORDER BY lead_score_at ASC NULLS FIRST):
-- lets the bounded LIMIT drain read the oldest rows as an index scan rather than sort the table.
CREATE INDEX IF NOT EXISTS idx_deals_lead_score_at    ON deals    (lead_score_at ASC NULLS FIRST);
CREATE INDEX IF NOT EXISTS idx_contacts_lead_score_at ON contacts (lead_score_at ASC NULLS FIRST);
