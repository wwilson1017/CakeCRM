-- Issue #4 — Seed the built-in assistant: conversation history + identity.
--
-- Reimplements Chatty's SQLite chat-history schema (conversations + messages)
-- on Postgres, plus a single-row identity singleton that replaces Chatty's
-- multi-agent roster / training-mode personality system. Full-text search
-- (Chatty's FTS5) is deliberately deferred; add a tsvector column by a later
-- migration if conversation search lands.

CREATE TABLE IF NOT EXISTS assistant_conversations (
    id                   TEXT PRIMARY KEY,                 -- uuid4 string
    title                TEXT NOT NULL DEFAULT 'New conversation',
    title_edited_by_user BOOLEAN NOT NULL DEFAULT FALSE,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_assistant_conv_updated
    ON assistant_conversations (updated_at DESC);

-- One row per model iteration of a turn. tool_calls holds this iteration's
-- calls; tool_results holds the (uncapped) results the engine reconstructs
-- context from. Both are JSONB and come back from psycopg2 already parsed as
-- Python lists -- consumers must NOT json.loads them.
CREATE TABLE IF NOT EXISTS assistant_messages (
    id              TEXT PRIMARY KEY,                      -- uuid4 string
    conversation_id TEXT NOT NULL
                    REFERENCES assistant_conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content         TEXT NOT NULL DEFAULT '',
    seq             INTEGER NOT NULL,
    tool_calls      JSONB,          -- [{tool, tool_use_id, args}] or NULL
    tool_results    JSONB,          -- [{tool_use_id, tool_name, content}] or NULL
    model           TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_assistant_msg_conv
    ON assistant_messages (conversation_id, seq);

-- Single built-in assistant identity (singleton, same pattern as
-- ai_settings / crm_meta). personality = '' means "use the built-in default
-- text" (resolved in assistant/identity.py) so the default prompt can improve
-- without a migration.
CREATE TABLE IF NOT EXISTS assistant_identity (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    name        TEXT NOT NULL DEFAULT 'Baker',
    personality TEXT NOT NULL DEFAULT '',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO assistant_identity (id) VALUES (1) ON CONFLICT (id) DO NOTHING;
