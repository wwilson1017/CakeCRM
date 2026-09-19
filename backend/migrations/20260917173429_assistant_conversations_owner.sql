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

-- Claim pre-existing rows for the earliest admin. This covers the ORDINARY upgrade — an
-- install already running multi-user, where `users` is populated and
-- ensure_bootstrap_admin() returns early — and it is a no-op on a fresh install, where
-- the subquery is NULL but assistant_conversations is empty too, so 0 rows match.
--
-- It is deliberately NOT the only claim. An install upgrading straight from a
-- PRE-multi-user release applies 20260821100126_multi_user.sql and this file in one
-- startup, and the bootstrap admin is only seeded afterwards — so here the subquery
-- reads an empty users table, matches nothing, and this migration never runs again.
-- users/bootstrap.py claims those rows inside the same transaction that creates the
-- admin, exactly as it already does for the legacy totp_config and trusted_devices
-- rows. Both claims are WHERE user_id IS NULL, so they are idempotent and only one of
-- them can ever find rows.
--
-- This is NOT the owner_id-fabrication case: pre-Phase-A there was genuinely one seat,
-- and between A and B every conversation was readable install-wide, so the admin gains
-- nothing here they could not already read.
--
-- `AND is_active` is load-bearing, not defensive. A deactivated user cannot authenticate
-- (core/auth.py rejects them and their token epoch was bumped at deactivation), and this
-- issue gives conversations owner-only access with NO admin override — so claiming the
-- history for a deactivated lowest-id admin would hand every pre-existing thread to a
-- seat nobody can log into, with no in-app way to get it back. `is_active` is BOOLEAN NOT
-- NULL, and `WHERE role = 'admin' AND is_active` is the same expression the last-active-
-- admin guard in users/service.update_user() already uses for "an admin who counts".
--
-- When no ACTIVE admin exists the subquery is NULL, so the SET is a no-op on rows that
-- are already NULL and the migration succeeds rather than failing the boot. Those rows
-- stay unowned, which is the fail-safe direction (a NULL owner is invisible to every
-- seat, never readable by the wrong one).
-- simplification: reactivating an admin later does NOT re-run this claim, so such an
-- install keeps its legacy threads invisible. Recovering them is a deliberate act, not
-- something a migration should guess at; a future release can add an admin-only "adopt
-- unowned conversations" action if any install ever needs it.
UPDATE assistant_conversations
   SET user_id = (SELECT MIN(id) FROM users WHERE role = 'admin' AND is_active)
 WHERE user_id IS NULL;

-- Covers the sidebar list, which is the one hot read: filter on user_id, order by
-- updated_at DESC.
CREATE INDEX IF NOT EXISTS idx_assistant_conv_user_updated
    ON assistant_conversations (user_id, updated_at DESC);
