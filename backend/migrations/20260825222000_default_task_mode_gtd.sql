-- Default task mode → GTD (#102)
--
-- #70 shipped Todo-GTD as the opt-in and normal tasks as the default. Will's product
-- call on #102 inverts that: GTD is the task experience CakeCRM leads with, and normal
-- becomes the opt-out. The Settings → Tasks card still switches either way, instantly
-- and losslessly — GTD is a view over the same `tasks` rows, so nothing here migrates
-- task data and nothing needs to.
--
-- ── Why the UPDATE is the operative half, and the SET DEFAULT is not ─────────────
-- `crm_meta` is a singleton whose row is inserted by 20260723221920_crm_core.sql —
-- long BEFORE 20260821100625 adds `task_mode`. So `ADD COLUMN ... NOT NULL DEFAULT
-- 'normal'` backfilled that already-present row once, and nothing ever inserts
-- `crm_meta` again: every other writer UPDATEs it, and the CRM-reset TRUNCATE sweeps
-- deliberately exclude it. The column default is therefore consumed exactly once, at
-- ADD COLUMN time, and never consulted again — on a fresh install either, since a
-- fresh install replays this same migration sequence.
--
-- The consequence worth writing down: flipping ONLY the column default would change
-- nothing anywhere, on any install. The row UPDATE below is not a policy choice about
-- existing installs layered on top of a default change — it is the entire mechanism.
-- Do not "simplify" it away later.
--
-- The SET DEFAULT stays anyway, because a schema whose declared default contradicts
-- the product default is a trap for the next person to add an inserter.

ALTER TABLE crm_meta ALTER COLUMN task_mode SET DEFAULT 'gtd';

-- Rows already at 'gtd' are untouched by the WHERE clause.
--
-- A row still at 'normal' is ambiguous: it is the DDL default, not evidence of a
-- choice. #70 shipped four days before this migration, so the overwhelming majority
-- are untouched defaults. Will's decision on #102 accepts the trade explicitly —
-- anyone who deliberately toggled GTD→normal in that window is flipped back ONCE and
-- re-toggles in Settings. That is a one-click, no-data-loss reversal, and the toggle
-- itself is unchanged by this migration.
UPDATE crm_meta SET task_mode = 'gtd', updated_at = now() WHERE task_mode = 'normal';
