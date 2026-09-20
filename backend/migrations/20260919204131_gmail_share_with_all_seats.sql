-- Multi-user Phase B / B5 (issue #194): the install setting that opens the Gmail
-- tool gate to every seat.
--
-- Default FALSE — the gate is admin-only unless an install deliberately runs one
-- shared inbox (Will's decision on #98, 2026-09-14). The flag lives on the Gmail
-- connection singleton rather than crm_meta because it is Gmail install policy and
-- the gate already reads this row; it is CONNECTION-SCOPED, so every path that
-- installs a new connection identity (clear_connection, save_app_credentials,
-- save_tokens) resets it to FALSE. A new grant starts private.
--
-- Purely additive: the column defaults on the existing singleton row, so a fresh
-- install and an upgrade land in exactly the same state.

ALTER TABLE gmail_connection
    ADD COLUMN IF NOT EXISTS share_with_all_seats BOOLEAN NOT NULL DEFAULT FALSE;
