-- Issue #283 — reminders, phase 1: one-time and repeating reminders as data.
--
-- A fresh table. #188's 20260915000157_drop_reminders.sql dropped the #6-era
-- `reminders` (a different shape, no recipients); this is not that table come back.
--
-- A row is who (owner_id + people), what (note) and when (due_date, optional due_time,
-- and due_at — the instant the service computed from the two in the install timezone,
-- so the tick compares one indexed column against now()). Rows are never deleted: they
-- move upcoming → fired, or upcoming → voided with a reason. A repeating row that
-- fires inserts its successor in the same transaction; every row of a series carries
-- the root's id in series_id and the series' first date in anchor_date, and each
-- occurrence is computed from that anchor so month-end clamping never drifts.
--
-- people is an INTEGER[] of users.id rather than a join table: users are deactivated,
-- never deleted, so there is no cascade to want, and every reader needs the whole list.
-- The service checks each id is an active user on every write.

CREATE TABLE IF NOT EXISTS reminders (
    id           BIGSERIAL PRIMARY KEY,
    owner_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    note         TEXT NOT NULL CHECK (length(note) BETWEEN 1 AND 2000),
    people       INTEGER[] NOT NULL CHECK (cardinality(people) BETWEEN 1 AND 50),
    due_date     DATE NOT NULL,
    due_time     TIME,
    due_at       TIMESTAMPTZ NOT NULL,
    repeat       TEXT CHECK (repeat IN ('daily', 'weekly', 'monthly', 'yearly')),
    anchor_date  DATE NOT NULL,
    series_id    BIGINT REFERENCES reminders(id) ON DELETE SET NULL,
    status       TEXT NOT NULL DEFAULT 'upcoming'
                 CHECK (status IN ('upcoming', 'fired', 'voided')),
    void_reason  TEXT NOT NULL DEFAULT '' CHECK (length(void_reason) <= 500),
    fired_at     TIMESTAMPTZ,
    voided_at    TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT reminders_voided_needs_reason CHECK ((status = 'voided') = (void_reason <> ''))
);

-- The tick's read: due upcoming rows, oldest first.
CREATE INDEX IF NOT EXISTS idx_reminders_due_upcoming
    ON reminders (due_at, id) WHERE status = 'upcoming';
-- The Today panel's read.
CREATE INDEX IF NOT EXISTS idx_reminders_due_date ON reminders (due_date, id);
-- A series spawns at most one row per date. The claim already makes a double spawn
-- impossible; this is the row-level backstop.
CREATE UNIQUE INDEX IF NOT EXISTS uq_reminders_series_due
    ON reminders (series_id, due_date) WHERE series_id IS NOT NULL;
