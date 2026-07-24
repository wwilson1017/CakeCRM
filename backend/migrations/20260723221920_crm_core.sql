-- CRM core schema (issue #3): promote the CRM from an optional integration to
-- always-on app core. Ported from chatty's SQLite crm_lite (contacts, deals,
-- tasks, activity_log) and translated to Postgres, folding chatty's incremental
-- _apply_migrations ALTERs into the base CREATEs so this is one clean
-- forward-state schema.
--
-- Query-shape idioms follow the matching cake_os crm services so later ports
-- diff cleanly: SERIAL PKs, REAL money, INTEGER 0/1 flags, TIMESTAMPTZ audit
-- columns. Dates the user picks (due_date, expected_close_date) stay TEXT
-- YYYY-MM-DD strings, matching both blueprints.

CREATE TABLE IF NOT EXISTS contacts (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    email       TEXT NOT NULL DEFAULT '',
    phone       TEXT NOT NULL DEFAULT '',
    company     TEXT NOT NULL DEFAULT '',
    title       TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'active',
    tags        TEXT NOT NULL DEFAULT '',
    notes       TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_contacts_name    ON contacts(name);
CREATE INDEX IF NOT EXISTS idx_contacts_company ON contacts(company);
CREATE INDEX IF NOT EXISTS idx_contacts_status  ON contacts(status);
CREATE INDEX IF NOT EXISTS idx_contacts_email   ON contacts(email);

CREATE TABLE IF NOT EXISTS deals (
    id                  SERIAL PRIMARY KEY,
    contact_id          INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    title               TEXT NOT NULL,
    stage               TEXT NOT NULL DEFAULT 'lead',
    value               REAL NOT NULL DEFAULT 0,
    expected_close_date TEXT NOT NULL DEFAULT '',
    probability         INTEGER NOT NULL DEFAULT 0,
    currency            TEXT NOT NULL DEFAULT 'USD',
    notes               TEXT NOT NULL DEFAULT '',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_deals_stage          ON deals(stage);
CREATE INDEX IF NOT EXISTS idx_deals_contact        ON deals(contact_id);
CREATE INDEX IF NOT EXISTS idx_deals_expected_close ON deals(expected_close_date);

CREATE TABLE IF NOT EXISTS tasks (
    id          SERIAL PRIMARY KEY,
    contact_id  INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    deal_id     INTEGER REFERENCES deals(id) ON DELETE SET NULL,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    due_date    TEXT NOT NULL DEFAULT '',
    completed   INTEGER NOT NULL DEFAULT 0,
    priority    TEXT NOT NULL DEFAULT 'medium',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_tasks_due       ON tasks(due_date);
CREATE INDEX IF NOT EXISTS idx_tasks_contact   ON tasks(contact_id);
CREATE INDEX IF NOT EXISTS idx_tasks_completed ON tasks(completed);

CREATE TABLE IF NOT EXISTS activity_log (
    id          SERIAL PRIMARY KEY,
    contact_id  INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    deal_id     INTEGER REFERENCES deals(id) ON DELETE SET NULL,
    activity    TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Not present in chatty's SQLite schema; added because delete_contact and the
-- activity feed filter activity_log by these foreign keys.
CREATE INDEX IF NOT EXISTS idx_activity_contact ON activity_log(contact_id);
CREATE INDEX IF NOT EXISTS idx_activity_deal    ON activity_log(deal_id);

-- First-run / sample-data state. Replaces chatty's per-integration
-- enabled/demo_mode credential flags (banned here: the CRM is always-on core).
-- Singleton row, same pattern as ai_settings.
CREATE TABLE IF NOT EXISTS crm_meta (
    id                   INTEGER PRIMARY KEY CHECK (id = 1),
    sample_data_loaded   BOOLEAN NOT NULL DEFAULT FALSE,
    onboarding_dismissed BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO crm_meta (id) VALUES (1) ON CONFLICT (id) DO NOTHING;
