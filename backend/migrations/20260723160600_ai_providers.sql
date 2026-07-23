-- AI provider credentials, active selection, and model-tier configuration (issue #2).
--
-- Single-tenant, admin-global storage: the single-user login owns one global
-- provider configuration. Multi-user (seats) is future work that adds ownership
-- and authorization — NOT merely a user_id column.
--
-- api_key_enc holds a Fernet-encrypted value ("enc:v1:...") written by
-- core/encryption.encrypt_value — never plaintext. The CHECK below is
-- defense-in-depth against a future code path accidentally writing plaintext.

CREATE TABLE IF NOT EXISTS ai_providers (
    provider     TEXT PRIMARY KEY
                 CHECK (provider IN ('anthropic', 'openai', 'google', 'ollama', 'together')),
    auth_type    TEXT NOT NULL DEFAULT 'api_key'
                 CHECK (auth_type IN ('api_key', 'ollama_local')),
    api_key_enc  TEXT NOT NULL DEFAULT ''
                 CHECK (api_key_enc = '' OR api_key_enc LIKE 'enc:v1:%'),
    base_url     TEXT NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Ollama is the only keyless/local provider; the others are key-based.
    CHECK (
        (provider = 'ollama' AND auth_type = 'ollama_local')
        OR (provider <> 'ollama' AND auth_type = 'api_key')
    )
);

-- App-wide active provider/model (singleton row, same pattern as totp_config).
CREATE TABLE IF NOT EXISTS ai_settings (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    active_provider TEXT NOT NULL DEFAULT ''
                    CHECK (active_provider IN ('', 'anthropic', 'openai', 'google', 'ollama', 'together')),
    active_model    TEXT NOT NULL DEFAULT '',
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO ai_settings (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

-- Per-provider tier config: user overrides + live-fetch-inferred defaults, each
-- a subset of {"top": "...", "mid": "...", "light": "..."}. Ollama has no tiers,
-- so it is excluded — rows appear only via materialize_inference / PUT tiers.
CREATE TABLE IF NOT EXISTS ai_model_tiers (
    provider   TEXT PRIMARY KEY
               CHECK (provider IN ('anthropic', 'openai', 'google', 'together')),
    overrides  JSONB NOT NULL DEFAULT '{}'::jsonb,
    inferred   JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
