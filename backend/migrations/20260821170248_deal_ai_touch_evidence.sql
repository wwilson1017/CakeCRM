-- Issue #56: the per-event AI verdicts behind the #16 touch-count pill.
--
-- ONE JSONB snapshot row per deal, replaced wholesale on every successful
-- recompute IN THE SAME TRANSACTION as the guarded deals count UPDATE and gated
-- on that UPDATE's rowcount -- so the pill and its explanation commit together or
-- not at all, and a recompute that LOSES the stale-write guard leaves no orphaned
-- explanation behind. See crm/touch_count_service.py.
--
-- Deliberately NOT a column on deals: get_pipeline() selects deal rows for every
-- card of every board load, and a multi-KB blob would ride along on all of them.
--
-- Deliberately NOT a normalized per-event table: verdicts are recomputed
-- wholesale and never queried across deals, the synthetic "[deal notes field]"
-- evidence line has no source row at all, and a single upsert keeps the
-- rowcount-gated transaction trivial.
--
-- deal_id carries NO foreign key, on purpose (the crm_field_values /
-- proactive_nudges / gmail_scanned_messages convention): Postgres refuses to
-- TRUNCATE a referenced table unless the referencing table rides the SAME
-- statement, and the CRM-reset TRUNCATE string is pinned by tests. The cost of
-- no FK is that nothing cascades: this table MUST stay in _truncate_all's sweep,
-- or after RESTART IDENTITY a new deal inherits a deleted deal's explanation.
--
-- verdicts payload shape (v1):
--   {"v": 1,
--    "count": <int>,            -- echoed so a reader detects a count written
--                               -- WITHOUT these verdicts (count-only fallback)
--    "watermark": <str>,        -- the ai_touch_count_at this was built on
--    "evidence_count": <int>,
--    "items": [{"source": "note"|"activity"|"deal_notes",
--               "source_id": <int|null>, "touch": <bool>, "reason": <str>,
--               "h": <str>}],   -- digest of the evidence line AS JUDGED
--    "skipped": [{"source": "note", "source_id": <int>, "why": "empty_note"}]}
--
-- items deliberately store NO line text: the reader renders the LIVE line from
-- crm_chatter/activity_log. A note edit rewrites `message` without touching
-- `created_at` or the row count, so both stale-write guard keys stay put -- and
-- storing the text would pin a pre-edit line beside a verdict about it.
--
-- "h" is how the reader detects that edit anyway: it compares the digest of the
-- line the model actually judged against the live line. A timestamp cannot do
-- this job -- activity_log has no updated_at column at all, deals.notes changes
-- without one, and comparing a note's updated_at against computed_at misses an
-- edit made WHILE the model was running. A row whose digest is missing or not a
-- string is treated as unverifiable and drops the snapshot off "current".
CREATE TABLE IF NOT EXISTS deal_ai_touch_evidence (
    deal_id     INTEGER PRIMARY KEY,
    verdicts    JSONB NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
