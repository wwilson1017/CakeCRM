-- #239: who archived a deal and why, read by the deal sheet's archived banner.
--
-- Nullable on purpose: a deal archived before this migration has no answer (no
-- backfill is possible), an unattended assistant turn has no seat, and a deleted
-- user is SET NULL. Written by crm.service.archive_deal / merge_deals only and
-- cleared by a restore. Contacts and companies get no columns — their record of
-- who and why is the "Archived — <reason>" note in their notes thread.
ALTER TABLE deals ADD COLUMN IF NOT EXISTS archived_by INTEGER REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE deals ADD COLUMN IF NOT EXISTS archived_reason TEXT;
