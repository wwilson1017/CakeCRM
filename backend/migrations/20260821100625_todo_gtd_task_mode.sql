-- Todo-GTD task mode (#70).
--
-- ONE STORE: the GTD experience is a mode over the EXISTING `tasks` rows, not a
-- second table. Widening `tasks` keeps the dashboard counts, contact/deal task
-- rollups, the stale-deal "has an open follow-up" check, the heartbeat nudge and
-- the CRM reset all reading one table, and gives GTD todos contact/deal links for
-- free. The cost is that `completed` (INTEGER 0/1, which the whole existing CRM
-- reads) and the new 7-value `status` must never disagree — enforced below by a
-- CHECK constraint rather than by app-code discipline.
--
-- Ported from cake_os `apps/todo_gtd` (itself a Postgres port of chatty's
-- `core/todo`), minus the per-user owner column (CakeCRM is single-user in v1) and
-- minus the Projects/CRM card-link columns (no Projects app here; `tasks` already
-- carries contact_id/deal_id).

-- ── Projects ──────────────────────────────────────────────────────────────────
-- A GTD project is an outcome needing more than one action. Deliberately NOT the
-- CRM's `deals` — a project groups the user's own work, a deal is a sales record.
CREATE TABLE IF NOT EXISTS task_projects (
    id         SERIAL PRIMARY KEY,
    name       TEXT NOT NULL,
    notes      TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL DEFAULT 'active'
               CHECK (status IN ('active', 'someday', 'completed', 'dropped')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Case-insensitive uniqueness: `#Groceries` and `#groceries` in quick-add must
-- resolve to ONE project. A functional unique index (not a UNIQUE constraint) so
-- the resolve-or-create helper can use ON CONFLICT (lower(name)).
CREATE UNIQUE INDEX IF NOT EXISTS idx_task_projects_name ON task_projects (lower(name));

-- ── Widen `tasks` with the GTD columns ────────────────────────────────────────
-- Every column is additive with a default, so existing rows and every existing
-- INSERT that names its columns keep working unchanged.
ALTER TABLE tasks
    -- 'next_action' (not 'inbox') is the default ON PURPOSE: a task created by the
    -- normal-mode form or by crm_create_task is already clarified work with a title
    -- and often a due date. Dumping it into the inbox would force fake re-triage.
    -- New GTD *captures* pass status='inbox' explicitly.
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'next_action'
        CHECK (status IN ('inbox', 'next_action', 'waiting_for', 'delegated',
                          'someday_maybe', 'done', 'dropped')),
    ADD COLUMN IF NOT EXISTS star BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS context TEXT NOT NULL DEFAULT '',
    -- JSONB, deliberately unlike `contacts.tags` (TEXT): the GTD tag facet filters
    -- with jsonb_exists() and unions tags with jsonb_array_elements_text(), neither
    -- of which a comma-joined string supports.
    ADD COLUMN IF NOT EXISTS tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- '' = no repeat. The enum plus the 'every:N' form (N days, 1-9999) that
    -- gtd_common._EVERY_RE validates in the service layer.
    ADD COLUMN IF NOT EXISTS repeat TEXT NOT NULL DEFAULT ''
        CHECK (repeat IN ('', 'daily', 'weekdays', 'weekly', 'monthly', 'yearly')
               OR repeat ~ '^every:[1-9][0-9]{0,3}$'),
    ADD COLUMN IF NOT EXISTS auto_star_on_due BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS project_id INTEGER REFERENCES task_projects(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'ui'
        CHECK (source IN ('capture_web', 'telegram', 'agent', 'ui'));

-- ── Backfill BEFORE the coherence constraint ──────────────────────────────────
-- Order is load-bearing: every pre-existing completed row is status='next_action'
-- at this point, so adding the CHECK first would reject the whole table.
UPDATE tasks SET status = 'done', completed_at = updated_at WHERE completed = 1;

-- ── The invariant that makes one store safe ───────────────────────────────────
-- `completed` and `status` are two views of the same fact. A constraint (not app
-- discipline) makes drift impossible: any writer that sets one without the other
-- fails loudly at the boundary instead of silently desyncing the CRM's counts from
-- the GTD lists. service._apply_task_update_cur is the single funnel that keeps
-- both in step; this is the backstop that proves it.
ALTER TABLE tasks
    ADD CONSTRAINT tasks_completed_status_coherent
    CHECK (completed = CASE WHEN status = 'done' THEN 1 ELSE 0 END);

CREATE INDEX IF NOT EXISTS idx_tasks_status  ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);

-- ── Mode + public-surface settings on the existing singleton ──────────────────
-- Same pattern as ai_key_prompt_dismissed: settings live on the crm_meta singleton,
-- not in a JSON file (chatty's admin-settings store has no analogue here).
ALTER TABLE crm_meta
    ADD COLUMN IF NOT EXISTS task_mode TEXT NOT NULL DEFAULT 'normal'
        CHECK (task_mode IN ('normal', 'gtd')),
    -- Public quick-capture: WRITE-ONLY. While this is '' the bare /capture path is
    -- public; setting a token moves the surface to /capture/{token} and makes the
    -- bare path 404.
    ADD COLUMN IF NOT EXISTS todo_capture_token TEXT NOT NULL DEFAULT '',
    -- The no-login todo WEB APP is full read/write, so it is OFF until explicitly
    -- enabled — unlike capture, which is harmless while unconfigured.
    ADD COLUMN IF NOT EXISTS todo_web_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS todo_web_token TEXT NOT NULL DEFAULT '';
