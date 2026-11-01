-- Issue #336 — pause and resume reminders without deleting their live row.
-- The tick selects only upcoming rows; paused series spawn nothing until resumed.
-- updated_at records the transition, with no separate paused timestamp.

ALTER TABLE reminders DROP CONSTRAINT IF EXISTS reminders_status_check;
ALTER TABLE reminders ADD CONSTRAINT reminders_status_check
    CHECK (status IN ('upcoming', 'paused', 'fired', 'voided'));
