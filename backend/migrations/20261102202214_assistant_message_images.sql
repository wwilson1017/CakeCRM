-- Photos live in Postgres: one store, one transaction, one pg_dump, like chatter
-- attachments (20260827030407). The FK chain deletes bytes with the message/conversation.
-- Assistant history survives CRM demo resets, so this table stays out of _truncate_all.
-- No hash or thumbnail columns: neither has a reader.
CREATE TABLE IF NOT EXISTS assistant_message_images (
    id SERIAL PRIMARY KEY,
    message_id TEXT NOT NULL REFERENCES assistant_messages(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    filename TEXT NOT NULL CHECK (btrim(filename) <> ''),
    mime_type TEXT NOT NULL CHECK (mime_type IN ('image/jpeg', 'image/png', 'image/webp')),
    byte_size INTEGER NOT NULL CHECK (byte_size > 0),
    data BYTEA NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (message_id, ordinal)
);
