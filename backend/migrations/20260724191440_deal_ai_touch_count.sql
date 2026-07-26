-- AI-inferred touch count per deal (issue #16). A "touch" is one meaningful
-- prospect interaction; the count is an LLM estimate over the deal's recent
-- notes + activities, surfaced as a pipeline-card nudge for the 12-touches sales
-- philosophy (most deals close between touch 5 and 12). Cheap light-tier work,
-- computed by an in-process background worker — see crm/touch_count_service.py.
--
--   ai_touch_count          NULL = never computed (renders no pill; also the
--                           scope=null backfill candidate filter). A real 0 is a
--                           computed value, distinct from NULL.
--   ai_touch_count_at       newest evidence timestamp the stored count was
--                           computed from — the first half of the stale-write
--                           guard key (never a wall-clock "computed at").
--   ai_touch_evidence_count how many evidence rows fed that count — the second
--                           half of the guard key (an import adding HISTORICAL
--                           notes changes the evidence set without advancing the
--                           timestamp, so the count alone isn't enough).
ALTER TABLE deals ADD COLUMN IF NOT EXISTS ai_touch_count          INTEGER;
ALTER TABLE deals ADD COLUMN IF NOT EXISTS ai_touch_count_at       TIMESTAMPTZ;
ALTER TABLE deals ADD COLUMN IF NOT EXISTS ai_touch_evidence_count INTEGER;
