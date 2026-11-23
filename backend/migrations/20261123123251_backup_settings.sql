-- Install configuration, intentionally preserved by every CRM data reset.
CREATE TABLE backup_settings (
    id INT PRIMARY KEY CHECK (id = 1),
    endpoint TEXT,
    bucket TEXT,
    region TEXT NOT NULL DEFAULT 'us-east-1',
    prefix TEXT NOT NULL DEFAULT '',
    access_key_id TEXT,
    secret_key_enc TEXT,
    passphrase_enc TEXT,
    retention INT NOT NULL DEFAULT 30 CHECK (retention BETWEEN 1 AND 365),
    config_version INT NOT NULL DEFAULT 0,
    configured_at TIMESTAMPTZ,
    last_attempt_at TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    last_success_key TEXT,
    last_success_bytes BIGINT,
    last_error TEXT,
    last_error_at TIMESTAMPTZ
);
INSERT INTO backup_settings (id) VALUES (1) ON CONFLICT DO NOTHING;
