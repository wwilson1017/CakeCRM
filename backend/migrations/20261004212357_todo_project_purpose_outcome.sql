-- #262: a GTD project's one-line purpose (why it exists) and successful outcome (what
-- done looks like), edited on the project page and shown on the triage card.
--
-- NOT NULL DEFAULT '' rather than nullable, matching `notes` beside them: the service
-- and the UI both treat "unset" as an empty string, so a NULL would be a second spelling
-- of the same state. A constant default is a metadata-only ADD COLUMN on Postgres 11+.
ALTER TABLE todo_projects ADD COLUMN IF NOT EXISTS purpose TEXT NOT NULL DEFAULT '';
ALTER TABLE todo_projects ADD COLUMN IF NOT EXISTS outcome TEXT NOT NULL DEFAULT '';
