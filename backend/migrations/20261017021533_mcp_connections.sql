-- #322: connect your own AI. A person's ChatGPT, Claude, Claude Code, Codex, Gemini CLI,
-- Cursor or VS Code signs in to /mcp as that person (OAuth 2.1, CakeCRM is its own
-- authorization server) or with a personal token pasted into the tool.
--
-- Credential rows (connections, credentials, OAuth clients, pending sign-ins, codes) hold
-- SHA-256 hashes or opaque ids only, never a readable secret. They stay OUTSIDE
-- crm.service._truncate_all: a CRM reset must not disconnect anyone's tools, exactly like
-- todo_web_sessions. mcp_audit is the per-call log and joins both reset branches.
-- Timestamps are bound from Python (mcp_endpoint/store.py), never DEFAULT now(), so every
-- expiry and cap decision runs on one injectable clock.

-- A connection is the thing a person sees, names and revokes: one personal token, or one
-- OAuth sign-in from one client. token_epoch is the seat's users.token_epoch when it was
-- made; a password change, admin reset or deactivation bumps the seat's epoch and every
-- connection made before it is dead on its next request.
CREATE TABLE IF NOT EXISTS mcp_connections (
    id            BIGSERIAL PRIMARY KEY,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL CHECK (kind IN ('token', 'oauth')),
    name          TEXT NOT NULL,
    client_id     TEXT,
    read_only     BOOLEAN NOT NULL DEFAULT FALSE,
    token_epoch   INTEGER NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL,
    last_used_at  TIMESTAMPTZ NOT NULL,
    expires_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_mcp_connections_seat
    ON mcp_connections (user_id, created_at DESC, id DESC);

-- Every bearer secret, hashed: a personal token, an OAuth access token, an OAuth refresh
-- token. /mcp does ONE lookup here for kind IN ('personal', 'access').
CREATE TABLE IF NOT EXISTS mcp_credentials (
    id             BIGSERIAL PRIMARY KEY,
    connection_id  BIGINT NOT NULL REFERENCES mcp_connections(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL CHECK (kind IN ('personal', 'access', 'refresh')),
    token_hash     TEXT NOT NULL UNIQUE,
    scopes         TEXT NOT NULL DEFAULT '',
    expires_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_mcp_credentials_connection
    ON mcp_credentials (connection_id, kind);

-- OAuth clients: dynamic registrations (public clients only, no secret) and cached
-- client-ID metadata documents.
CREATE TABLE IF NOT EXISTS mcp_oauth_clients (
    client_id   TEXT PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN ('dcr', 'cimd')),
    metadata    JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL,
    fetched_at  TIMESTAMPTZ NOT NULL
);

-- A validated /authorize request parked for the consent page. Single use, ten minutes.
CREATE TABLE IF NOT EXISTS mcp_oauth_requests (
    id          TEXT PRIMARY KEY,
    client_id   TEXT NOT NULL,
    params      JSONB NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL
);

-- An approved consent, not yet exchanged. Single use, five minutes. Carries the seat's
-- epoch at approval, so a code approved before a password change cannot be redeemed after.
CREATE TABLE IF NOT EXISTS mcp_oauth_codes (
    code_hash    TEXT PRIMARY KEY,
    client_id    TEXT NOT NULL,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_epoch  INTEGER NOT NULL,
    client_name  TEXT NOT NULL,
    read_only    BOOLEAN NOT NULL,
    scopes       TEXT NOT NULL,
    params       JSONB NOT NULL,
    expires_at   TIMESTAMPTZ NOT NULL
);

-- One row per MCP tool call, reads included: which connection, which tool, which record
-- (or a row count), whether it worked, how long it took. Never arguments, never results.
-- No FK to mcp_connections: the log outlives a revoked connection until the 90-day prune.
CREATE TABLE IF NOT EXISTS mcp_audit (
    id             BIGSERIAL PRIMARY KEY,
    connection_id  BIGINT NOT NULL,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tool           TEXT NOT NULL,
    detail         TEXT NOT NULL DEFAULT '',
    ok             BOOLEAN NOT NULL,
    refused        TEXT,
    duration_ms    INTEGER NOT NULL,
    at             TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mcp_audit_connection ON mcp_audit (connection_id, at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_mcp_audit_at ON mcp_audit (at);

-- Admin switch "Members may connect their own AI tools". Off by default; admins always may.
ALTER TABLE crm_meta ADD COLUMN IF NOT EXISTS mcp_members_enabled BOOLEAN NOT NULL DEFAULT FALSE;

-- A todo written through a connected AI tool says so.
ALTER TABLE todos DROP CONSTRAINT IF EXISTS todos_source_check;
ALTER TABLE todos ADD CONSTRAINT todos_source_check
    CHECK (source IN ('capture_web', 'telegram', 'agent', 'observer', 'ui', 'mcp'));
