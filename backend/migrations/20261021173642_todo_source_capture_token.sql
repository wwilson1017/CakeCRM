-- #326: a capture through the TOKENED link (a token of minted strength) gets its own
-- source, `capture_token`, so the
-- #204 fence (which keys on `capture_web` alone) treats it as the owner's own text: the
-- person holding a secret capture URL is the owner, the same trust the todo web app's
-- token already carries. The bare `/capture` (no token configured) keeps `capture_web`.
--
-- Additive: the CHECK only widens, so a pre-#326 binary still reads every row (it never
-- writes 'capture_token'). No backfill — a row written before this says nothing about
-- whether a token was configured when it arrived, so it keeps the stricter treatment.
ALTER TABLE todos DROP CONSTRAINT IF EXISTS todos_source_check;
ALTER TABLE todos ADD CONSTRAINT todos_source_check
    CHECK (source IN ('capture_web', 'capture_token', 'telegram', 'agent', 'observer', 'ui'));
