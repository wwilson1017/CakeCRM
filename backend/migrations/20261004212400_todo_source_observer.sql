-- #260: the observer gets its own todo source, so the GTD UI can say "Observer"
-- rather than "Baker" on an inbox item nobody asked for.
--
-- Additive: the CHECK only widens, so a pre-#260 binary still reads every row (it
-- never writes 'observer'). No backfill — an observer todo written before this keeps
-- source='agent' and reads "Baker", which is still true; the only marker that could
-- tell the two apart is free text in its notes, which a user may have edited.
ALTER TABLE todos DROP CONSTRAINT IF EXISTS todos_source_check;
ALTER TABLE todos ADD CONSTRAINT todos_source_check
    CHECK (source IN ('capture_web', 'telegram', 'agent', 'observer', 'ui'));
