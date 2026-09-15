-- Issue #188 — reminders are gone; todos are the one follow-up primitive.
--
-- Drops the table 20260724190731_heartbeat_reminders_notifications.sql created. That
-- file stays FROZEN (migrations are append-only); this is the forward migration that
-- removes the feature. Pending reminders are lost — accepted on the issue.
--
-- The three indexes (idx_reminders_due, idx_reminders_series, uq_reminders_series_due)
-- are dropped with the table. No table carries a FOREIGN KEY to reminders.
--
-- alerts.source = 'reminder' is a free-text tag, not an FK. Those rows are cleared here
-- because the code that could resolve them by source is gone, so an open one would sit
-- in the notification bell forever with no way to clear it.
DELETE FROM alerts WHERE source = 'reminder';

DROP TABLE IF EXISTS reminders;
