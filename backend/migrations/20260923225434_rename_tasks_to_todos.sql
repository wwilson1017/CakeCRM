-- Tasks → Todos (#169): the follow-up feature is called Todos everywhere, so the
-- database says so too.
--
-- METADATA-ONLY AND ATOMIC. Every statement here is a catalog rename or a one-column
-- UPDATE on a small settings table; no row of `tasks`/`task_projects` is rewritten, so
-- this costs the same on an empty install and a full one. core/postgres.py's
-- run_migrations() executes this file as ONE cursor.execute() and commits once after
-- recording it in _migrations_applied — a failure anywhere below rolls the whole file
-- back and the app refuses to start, which is the intended failure mode.
--
-- Postgres renames NOTHING implicitly except the table itself. Each auto-named object
-- below keeps `tasks_…` forever unless renamed here; the names are the deterministic
-- `{table}_pkey`, `{table}_{column}_{check|fkey}`, `{table}_{col}_seq` forms and were
-- read from a database built from this exact (frozen) migration chain.
--
-- Values are untouched: `todo_mode` is still 'normal' | 'gtd' with DEFAULT 'gtd', the
-- completed/status CHECK is unchanged, and the `todo_capture_token` / `todo_web_*`
-- columns beside it already said todo.

-- ── Tables ─────────────────────────────────────────────────────────────────────
ALTER TABLE task_projects RENAME TO todo_projects;
ALTER TABLE tasks         RENAME TO todos;

-- ── Sequences (the id DEFAULTs reference the sequence by OID, so they follow) ──
ALTER SEQUENCE tasks_id_seq         RENAME TO todos_id_seq;
ALTER SEQUENCE task_projects_id_seq RENAME TO todo_projects_id_seq;

-- ── Primary keys (renaming the constraint renames its backing index too) ──────
ALTER TABLE todos         RENAME CONSTRAINT tasks_pkey         TO todos_pkey;
ALTER TABLE todo_projects RENAME CONSTRAINT task_projects_pkey TO todo_projects_pkey;

-- ── CHECK constraints ──────────────────────────────────────────────────────────
ALTER TABLE todos RENAME CONSTRAINT tasks_completed_check           TO todos_completed_check;
ALTER TABLE todos RENAME CONSTRAINT tasks_status_check              TO todos_status_check;
ALTER TABLE todos RENAME CONSTRAINT tasks_repeat_check              TO todos_repeat_check;
ALTER TABLE todos RENAME CONSTRAINT tasks_source_check              TO todos_source_check;
ALTER TABLE todos RENAME CONSTRAINT tasks_completed_status_coherent TO todos_completed_status_coherent;
ALTER TABLE todo_projects RENAME CONSTRAINT task_projects_status_check TO todo_projects_status_check;

-- ── Foreign keys ───────────────────────────────────────────────────────────────
ALTER TABLE todos RENAME CONSTRAINT tasks_contact_id_fkey TO todos_contact_id_fkey;
ALTER TABLE todos RENAME CONSTRAINT tasks_deal_id_fkey    TO todos_deal_id_fkey;
ALTER TABLE todos RENAME CONSTRAINT tasks_owner_id_fkey   TO todos_owner_id_fkey;
ALTER TABLE todos RENAME CONSTRAINT tasks_project_id_fkey TO todos_project_id_fkey;

-- ── Indexes ────────────────────────────────────────────────────────────────────
ALTER INDEX idx_tasks_due          RENAME TO idx_todos_due;
ALTER INDEX idx_tasks_contact      RENAME TO idx_todos_contact;
ALTER INDEX idx_tasks_completed    RENAME TO idx_todos_completed;
ALTER INDEX idx_tasks_owner        RENAME TO idx_todos_owner;
ALTER INDEX idx_tasks_status       RENAME TO idx_todos_status;
ALTER INDEX idx_tasks_project      RENAME TO idx_todos_project;
ALTER INDEX idx_task_projects_name RENAME TO idx_todo_projects_name;

-- ── The mode setting on the crm_meta singleton ─────────────────────────────────
ALTER TABLE crm_meta RENAME COLUMN task_mode TO todo_mode;
ALTER TABLE crm_meta RENAME CONSTRAINT crm_meta_task_mode_check TO crm_meta_todo_mode_check;

-- ── Saved views (#181) ─────────────────────────────────────────────────────────
-- `surface` is the frontend's CollectionStorage.key, renamed crm_tasks → crm_todos in
-- the same PR. Without this rewrite every saved Todos view would stay in the table and
-- never be listed again. The unique index is (surface, lower(btrim(name))): the shipped
-- UI never wrote 'crm_todos', so a collision needs a hand-crafted API call, and if one
-- exists this migration fails and the app refuses to start — the correct outcome, not a
-- case to skip past. `version` stays 1: the persisted payload shape (facet and sort
-- keys) is unchanged.
UPDATE saved_views SET surface = 'crm_todos' WHERE surface = 'crm_tasks';
