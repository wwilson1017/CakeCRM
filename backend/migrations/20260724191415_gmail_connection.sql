-- Gmail connection (issue #8): single-user BYO Google OAuth app + tokens.
--
-- Read + create-draft ONLY. There is no email-send path anywhere in the
-- codebase — the guarantee is enforced at the tool layer (the registry exposes
-- only gmail_search / gmail_read_thread / gmail_create_draft), because Google's
-- OAuth scopes cannot express "draft but never send" (gmail.compose permits
-- sending at the API level). See SECURITY.md and backend/tests/test_gmail_guard.py.
--
-- Single-tenant, admin-global: one row (id = 1), same singleton idiom as
-- ai_settings / crm_meta. Multi-user (seats) is future work — ownership and
-- authz, not merely a user_id column.
--
-- Secrets (client_secret, access/refresh tokens) hold Fernet-encrypted values
-- ("enc:v1:...") written by core/encryption.encrypt_value — never plaintext. The
-- CHECKs are defense-in-depth against a future code path writing plaintext.
-- The OAuth CSRF state is stored ONLY as a SHA-256 hash (oauth_state_hash), never
-- the raw value.

CREATE TABLE IF NOT EXISTS gmail_connection (
    id                      INTEGER PRIMARY KEY CHECK (id = 1),
    -- BYO Google OAuth app credentials (entered in-app, not env vars).
    client_id               TEXT NOT NULL DEFAULT '',
    client_secret_enc       TEXT NOT NULL DEFAULT ''
                            CHECK (client_secret_enc = '' OR client_secret_enc LIKE 'enc:v1:%'),
    -- The connected Google account address (from users.getProfile). Not a secret.
    email                   TEXT NOT NULL DEFAULT '',
    -- OAuth tokens minted for that account.
    access_token_enc        TEXT NOT NULL DEFAULT ''
                            CHECK (access_token_enc = '' OR access_token_enc LIKE 'enc:v1:%'),
    refresh_token_enc       TEXT NOT NULL DEFAULT ''
                            CHECK (refresh_token_enc = '' OR refresh_token_enc LIKE 'enc:v1:%'),
    token_expires_at        TIMESTAMPTZ,
    -- Space-separated granted scopes (validated to include both Gmail scopes).
    scopes                  TEXT NOT NULL DEFAULT '',
    connection_status       TEXT NOT NULL DEFAULT 'disconnected'
                            CHECK (connection_status IN ('disconnected', 'ok', 'broken')),
    -- Single-use CSRF state for the in-flight OAuth redirect (SHA-256 hash of the
    -- value handed to Google; TTL-bounded and cleared on claim / disconnect).
    oauth_state_hash        TEXT NOT NULL DEFAULT '',
    oauth_state_created_at  TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO gmail_connection (id) VALUES (1) ON CONFLICT (id) DO NOTHING;
