-- Heartbeat: user-tunable settings and a notification gap (issue #360).
-- All gates are read inside the periodic claim UPDATE. A NULL interval uses the
-- operator's default; quiet hours are HH:MM in the install's local timezone.
-- Announced records use proactive_nudges kind='heartbeat', already swept by CRM resets.
ALTER TABLE heartbeat_state
    ADD COLUMN IF NOT EXISTS turn_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS interval_minutes INTEGER
        CHECK (interval_minutes BETWEEN 30 AND 1440),
    ADD COLUMN IF NOT EXISTS quiet_start TEXT
        CHECK (quiet_start ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'),
    ADD COLUMN IF NOT EXISTS quiet_end TEXT
        CHECK (quiet_end ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'),
    ADD COLUMN IF NOT EXISTS paused_until TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS notify_min_gap_hours INTEGER NOT NULL DEFAULT 4
        CHECK (notify_min_gap_hours BETWEEN 0 AND 24),
    ADD COLUMN IF NOT EXISTS last_turn_notify_at TIMESTAMPTZ;
