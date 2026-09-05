-- Conversation compaction (issue #72 Phase 3).
--
-- A long thread used to grow without bound: the assembler rebuilt every row of a
-- conversation each turn and applied only a PER-ROW oversized guard, so nothing
-- capped the total until the provider rejected the request outright. Compaction
-- replaces the aged MIDDLE of a thread with an AI-written gist while keeping the
-- opening exchange and the recent tail verbatim.
--
-- Rows are NEVER deleted. These columns only change what the assembler BUILDS, so
-- the conversation UI still shows everything and setting compaction_summary /
-- compaction_first_kept_seq back to NULL restores the full context.
--
--   compaction_summary        the gist standing in for every row between the head
--                             and compaction_first_kept_seq. NULL = never compacted.
--   compaction_first_kept_seq the first assistant_messages.seq kept verbatim. The
--                             boundary only ever moves FORWARD (CAS in
--                             history.set_compaction), so two racing turns cannot
--                             rewind it.
--   compaction_tainted        TRUE once a compacted-away span carried untrusted
--                             content (an uploaded file, or an external read such as
--                             Gmail). The engine decides the power->normal write
--                             downgrade by scanning the ASSEMBLED context for those
--                             fences; compaction removes rows, so without this flag
--                             the first compaction that aged out a Gmail read would
--                             silently switch that mitigation off. The taint is
--                             monotone -- content that entered a thread never becomes
--                             trustworthy -- so this only ever flips FALSE -> TRUE.
--   last_context_tokens       the most recent turn's cache-inclusive input token count
--                             (input + cache_creation + cache_read), written in the
--                             same transaction that saves the assistant iteration. It
--                             is the accurate fullness meter that lets compaction skip
--                             the row scan on the common path. NULL when the provider
--                             reports no usage (today: everything except Anthropic),
--                             where a chars/4 estimate is the only available signal.

ALTER TABLE assistant_conversations
    ADD COLUMN IF NOT EXISTS compaction_summary        TEXT,
    ADD COLUMN IF NOT EXISTS compaction_first_kept_seq INTEGER,
    ADD COLUMN IF NOT EXISTS compaction_tainted        BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS last_context_tokens       INTEGER;
