-- Issue #6 — heartbeat, reminders, notifications, alerts.
--
-- Seeds the background half of the assistant: a scheduler tick, reminders that
-- fire, notifications delivered via Web Push (VAPID) + a Telegram seam (#7), and
-- system-level alerts. Ported/adapted from Chatty's core/agents/{reminders,
-- notifications,alerts} — translated to Postgres, single-tenant (agent-slug
-- columns dropped), and reshaped per the plan's Codex-review revisions.
--
-- Reminders are deliberately NOT crm tasks: a task (crm.tasks) is a user to-do
-- with a date-only due_date; a reminder is an agent-facing, recurring, auto-firing
-- object processed by the heartbeat, whose due_at is an INSTANT (TIMESTAMPTZ).

-- ── Reminders ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS reminders (
    id              TEXT PRIMARY KEY,                      -- uuid4 string
    message         TEXT NOT NULL,
    context         TEXT NOT NULL DEFAULT '',
    due_at          TIMESTAMPTZ NOT NULL,                  -- an instant, not a date
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','fired','cancelled')),
    recurrence_rule JSONB,                                 -- NULL = one-shot; {"type": "daily"|"interval"|"weekly"|"monthly"|"cron", ...} (comes back parsed — do NOT json.loads)
    series_id       TEXT,                                  -- groups recurring occurrences; = first id of the series
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    fired_at        TIMESTAMPTZ,
    result          TEXT
);
CREATE INDEX IF NOT EXISTS idx_reminders_due    ON reminders (status, due_at);
CREATE INDEX IF NOT EXISTS idx_reminders_series ON reminders (series_id);
-- A recurring series may create at most one successor per (series_id, due_at) —
-- prevents a double-claim from spawning duplicate future occurrences (R5).
CREATE UNIQUE INDEX IF NOT EXISTS uq_reminders_series_due
    ON reminders (series_id, due_at) WHERE series_id IS NOT NULL;

-- ── Notifications (in-app log; the always-on delivery channel) ───────────────
CREATE TABLE IF NOT EXISTS notifications (
    id            TEXT PRIMARY KEY,                        -- uuid4 string
    title         TEXT NOT NULL,
    message       TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','dismissed')),
    channels_sent JSONB NOT NULL DEFAULT '[]'::jsonb,      -- e.g. ["web_push","telegram"]
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    dismissed_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_notifications_status ON notifications (status, created_at DESC);

-- ── Web Push subscriptions ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS push_subscriptions (
    id         TEXT PRIMARY KEY,                           -- uuid4 string
    endpoint   TEXT NOT NULL UNIQUE,
    p256dh     TEXT NOT NULL,
    auth       TEXT NOT NULL,
    user_agent TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Alerts (system-level: repeated heartbeat failures, reminder failures) ────
CREATE TABLE IF NOT EXISTS alerts (
    id              TEXT PRIMARY KEY,                      -- uuid4 string
    source          TEXT NOT NULL DEFAULT 'heartbeat',     -- 'heartbeat' | 'reminder'
    source_id       TEXT,                                  -- dedup key within source
    title           TEXT NOT NULL,
    message         TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','acknowledged','resolved')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    acknowledged_at TIMESTAMPTZ,
    resolved_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts (status, created_at DESC);
-- At most one ACTIVE alert per (source, source_id) — dedup via ON CONFLICT (R10).
CREATE UNIQUE INDEX IF NOT EXISTS uq_alerts_active_source
    ON alerts (source, source_id) WHERE status = 'active';

-- ── VAPID keys: generated ONCE, persisted forever ───────────────────────────
-- Regenerating invalidates every browser push subscription. Chatty stored these
-- in a JSON file which does not survive Railway's ephemeral fs; here they live in
-- a singleton Postgres row with the private key Fernet-encrypted (core/encryption).
CREATE TABLE IF NOT EXISTS vapid_keys (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    public_key      TEXT NOT NULL,
    private_key_enc TEXT NOT NULL CHECK (private_key_enc LIKE 'enc:v1:%'),  -- must be encrypted at rest (R9)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- NOT pre-seeded: lazily generated + persisted on first use (notifications/vapid.py).

-- ── Heartbeat run/state singleton ───────────────────────────────────────────
-- Replaces Chatty's per-action columns on the deferred scheduled_actions table.
-- Holds the system heartbeat-turn cadence claim + consecutive-failure alert state.
CREATE TABLE IF NOT EXISTS heartbeat_state (
    id                    INTEGER PRIMARY KEY CHECK (id = 1),
    last_tick_at          TIMESTAMPTZ,
    last_turn_at          TIMESTAMPTZ,
    last_turn_status      TEXT NOT NULL DEFAULT '',        -- '' | 'ok' | 'error' | 'skipped_no_provider' | 'skipped_disabled'
    last_turn_result      TEXT NOT NULL DEFAULT '',
    consecutive_errors    INTEGER NOT NULL DEFAULT 0,
    last_failure_alert_at TIMESTAMPTZ
);
INSERT INTO heartbeat_state (id) VALUES (1) ON CONFLICT (id) DO NOTHING;
