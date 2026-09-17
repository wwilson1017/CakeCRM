-- Multi-user Phase B / B3 (issue #192): notifications gain a RECIPIENT.
--
-- Both columns are nullable and NULL means BROADCAST — the exact mirror of
-- `owner_id NULL` = unassigned that Phase A established (#98 Decision 6a). Alerts are
-- deliberately NOT given a recipient: all five creators are system/install-level and a
-- member is often the right responder (the gmail-scan "create contact?" alert), so
-- alerts stay install-wide.
--
-- NEITHER column is claimed, and the two reasons are different:
--
--   notifications      — no claim is needed. Every existing row really was delivered to
--                        everyone, and NULL = broadcast preserves that meaning exactly.
--                        Claiming them for the admin would RETROACTIVELY hide the
--                        install's history from every member.
--
--   push_subscriptions — no claim, deliberately, and this one is a safety decision
--                        rather than a no-op. A legacy endpoint row records a browser,
--                        not a person: between Phase A and here, any seat's browser
--                        could have created it. Claiming those for the admin would push
--                        the ADMIN's targeted notifications to whichever MEMBER's
--                        browser made the subscription — a real leak. NULL is instead
--                        the safe degraded state: `list_subscriptions(user_id=…)`
--                        matches only stamped rows, so an unclaimed endpoint receives
--                        broadcasts and nothing else. It self-heals with no user action
--                        because the subscribe upsert re-stamps
--                        `user_id = EXCLUDED.user_id` and the CRM shell silently
--                        re-POSTs an existing subscription on every authenticated load.
--
-- ON DELETE CASCADE on both (like totp_config / trusted_devices, NOT the SET NULL that
-- owner_id uses): a deleted seat's targeted notifications must DIE, never widen into
-- broadcasts that then become visible to the whole install, and their browser
-- subscription must stop receiving anything.
--
-- Fresh install and upgrade are the same statements: both tables exist from
-- 20260724190731_heartbeat_reminders_notifications.sql, `users` exists from
-- 20260821100126_multi_user.sql, and every statement is IF NOT EXISTS.
--
-- Reminders are absent on purpose. Plan v1 §6 M2 also owned reminders, but #188
-- removes reminders entirely, so there is no owner to record.

ALTER TABLE notifications
    ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE CASCADE;

ALTER TABLE push_subscriptions
    ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE CASCADE;

-- Every bell read is "mine OR broadcast" and every targeted push fan-out is
-- "WHERE user_id = %s", so both columns are filter columns on every hot path.
CREATE INDEX IF NOT EXISTS idx_notifications_user      ON notifications (user_id);
CREATE INDEX IF NOT EXISTS idx_push_subscriptions_user ON push_subscriptions (user_id);
