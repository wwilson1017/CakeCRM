-- Issue #191 (Phase B / B2): assistant conversations get an owner.
--
-- Unlike CRM records — where "ownership is not access control" (Phase A) — a
-- conversation is PERSONAL: it carries drafts, half-thoughts and private asks. From
-- here on every access path is owner-only with no admin override, and a conversation
-- belonging to another seat is indistinguishable from one that does not exist.
--
-- The column is NULLABLE on purpose. Every code path stamps it, so a NULL row can only
-- come from a legacy install with no admin at all — and a NULL row is invisible to every
-- seat, which is the fail-safe direction.
--
-- ON DELETE CASCADE, not SET NULL: personal data follows its person (the totp_config
-- idiom, not owner_id's). telegram_settings.conversation_id already references
-- assistant_conversations ON DELETE SET NULL, so a cascaded delete degrades that
-- singleton to "no conversation yet" rather than blocking the user delete.
ALTER TABLE assistant_conversations
    ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE CASCADE;

-- Claim pre-existing rows for the earliest admin. Correct in BOTH directions, which is
-- why it lives here and needs no users/bootstrap.py change:
--   * fresh install — migrations run before the bootstrap admin exists, so the subquery
--     is NULL; but assistant_conversations is empty too, so the UPDATE matches 0 rows.
--   * upgrade — users is already populated, ensure_bootstrap_admin returns early as
--     designed, and this claim has already run.
-- This is NOT the owner_id-fabrication case: pre-Phase-A there was genuinely one seat,
-- and between A and B every conversation was readable install-wide, so the admin gains
-- nothing here they could not already read.
UPDATE assistant_conversations
   SET user_id = (SELECT MIN(id) FROM users WHERE role = 'admin')
 WHERE user_id IS NULL;

-- Covers the sidebar list, which is the one hot read: filter on user_id, order by
-- updated_at DESC.
CREATE INDEX IF NOT EXISTS idx_assistant_conv_user_updated
    ON assistant_conversations (user_id, updated_at DESC);
