-- Two-factor authentication: TOTP config (single row) + trusted devices.

CREATE TABLE IF NOT EXISTS totp_config (
    id           INTEGER PRIMARY KEY CHECK (id = 1),
    enabled      BOOLEAN NOT NULL DEFAULT FALSE,
    secret_enc   TEXT NOT NULL DEFAULT '',
    backup_codes TEXT NOT NULL DEFAULT '[]',
    last_used_at TEXT NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS trusted_devices (
    token_hash  TEXT PRIMARY KEY,
    label       TEXT NOT NULL DEFAULT '',
    expires_at  TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
