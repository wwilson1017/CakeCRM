-- Issue #261: a todo can be told "bring this back on <date>". Until that day it is
-- hidden from the working lists; from that day it shows on Today. NULL = no date.
--
-- DATE, not the TEXT NOT NULL DEFAULT '' that `due_date` uses: this column is new, so
-- it can say "absent" the honest way, and a DATE cannot hold an impossible calendar day.
-- Written ONLY through crm.service._apply_todo_update_cur (the one todo write funnel).
-- Metadata-only: a nullable column with no default rewrites no rows.
ALTER TABLE todos ADD COLUMN IF NOT EXISTS bring_back_on DATE;
