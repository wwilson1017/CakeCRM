-- #321, follow-up to #319/#293: nothing reads this column since #319.
ALTER TABLE crm_meta DROP COLUMN IF EXISTS todo_web_token;
