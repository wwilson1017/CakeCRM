-- CSV/XLSX bytes follow their owner-only message/conversation, like photos (#370).
-- Assistant history survives CRM resets, so stored files do too.
CREATE TABLE IF NOT EXISTS assistant_message_files (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL REFERENCES assistant_messages(id) ON DELETE CASCADE,
    filename TEXT NOT NULL CHECK (btrim(filename) <> ''),
    data BYTEA NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_assistant_message_files_message
    ON assistant_message_files (message_id);

-- Seat-owned scratch data: consumed in the import transaction, swept on preview.
-- Parsed lead data joins both CRM reset branches; stored conversation files do not.
CREATE TABLE IF NOT EXISTS assistant_lead_import_previews (
    batch_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);
