-- Issue #72 Phase 4 — the observer (automatic fact + commitment capture) and
-- file-dreaming (the first writer of assistant_context_files.archived_at).
--
-- Four additive sections, no new tables.
--
-- The ADD COLUMN statements carry IF NOT EXISTS as cheap insurance for a
-- hand-repaired database. The BACKFILL in section 1 is deliberately NOT
-- idempotent and must never be re-run: it keys on `observed_through_seq IS
-- NULL`, which is also exactly what a conversation created AFTER this migration
-- looks like, so a second execution would advance those conversations to their
-- current max(seq) and silently consume messages the observer has not read.
-- Once-only execution is guaranteed by the runner, not by this file:
-- core/postgres.run_migrations records every applied filename in
-- `_migrations_applied` and skips it forever after. Do not copy this backfill
-- into a later migration without re-deriving that guarantee.

-- 1. Observer watermark (per conversation): the highest `seq` the observer has
--    already read. The backfill sets it to the CURRENT max(seq) so an upgrade
--    starts observing from now and never replays months of history as one AI
--    call per historical conversation. Stays NULLable: a conversation created
--    after this migration reads NULL, which the candidate query treats as -1
--    ("nothing observed yet"), and a conversation with no messages gets -1.
ALTER TABLE assistant_conversations ADD COLUMN IF NOT EXISTS observed_through_seq INTEGER;

UPDATE assistant_conversations c
   SET observed_through_seq = COALESCE(
         (SELECT max(seq) FROM assistant_messages m WHERE m.conversation_id = c.id), -1)
 WHERE observed_through_seq IS NULL;

-- No index: the candidate query drives off assistant_conversations.updated_at and a
-- correlated aggregate over assistant_messages (conversation_id, seq), both already
-- indexed, and a single-user install has few conversations.

-- 2. Observer run claim (singleton), mirroring heartbeat_state.last_turn_at. The
--    claim is a rowcount UPDATE, so two app instances can never run the pass at once
--    without an advisory lock.
ALTER TABLE heartbeat_state ADD COLUMN IF NOT EXISTS last_observer_run_at TIMESTAMPTZ;

-- 3. Context-file read signal, mirroring memory_facts.retrieval_count /
--    last_retrieved_at exactly. Only ON-DEMAND reads the assistant chose to make bump
--    these (the three read tool executors) — never the per-turn knowledge block, the
--    manifests or the Memory page. An unconditional load count would be a constant and
--    would keep every file alive forever, which is the defect dreaming/scorer.py's
--    docstring already names for facts.
ALTER TABLE assistant_context_files
    ADD COLUMN IF NOT EXISTS read_count   INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_read_at TIMESTAMPTZ;

-- 4. The dreaming audit gains the file unit. `details` is JSONB and gains
--    file_scores / archived_files keys with no schema change.
ALTER TABLE dreaming_runs
    ADD COLUMN IF NOT EXISTS files_scored   INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS files_archived INTEGER NOT NULL DEFAULT 0;
