-- Gmail connection generation (issue #43): an optimistic-lock version number on
-- the singleton gmail_connection row.
--
-- Every mutation that changes WHICH Google connection is live — saving new BYO
-- app credentials, disconnecting, and successfully persisting a fresh grant —
-- bumps this counter. Deferred actions that start under one connection and
-- finish later (the OAuth callback, whose token persist happens seconds after
-- the CSRF state is claimed; and a pending gmail_create_draft confirmation,
-- approved minutes after it was proposed) capture the generation at start and
-- compare-and-swap on it at completion, so they can never complete against a
-- connection that changed underneath them.
--
-- Deliberately NOT bumped by token refresh (same account, fresher tokens) or by
-- mark_broken (a status change, not an identity change) — bumping there would
-- invalidate in-flight reconnects and every pending draft on every hourly
-- refresh. Those two guard credential material with their own compare-and-swap
-- on the refresh-token ciphertext instead.
--
-- Still single-user v1: this is a version number on the one row (id = 1), NOT an
-- account key. Multi-account remains future work.

ALTER TABLE gmail_connection
    ADD COLUMN IF NOT EXISTS connection_generation BIGINT NOT NULL DEFAULT 0;
