-- Telegram integration (issue #7) — single-assistant, single linked user.
--
-- One singleton row (id = 1), mirroring ai_settings / crm_meta / assistant_identity.
-- The bot token is Fernet-encrypted at rest exactly like ai_providers.api_key_enc
-- (enc:v1: prefix, written via core.encryption.encrypt_value). There is no per-user
-- or per-agent fan-out: CakeCRM has exactly one assistant and one linked Telegram
-- user for v1.
--
-- conversation_id is a nullable FK into assistant_conversations with ON DELETE SET
-- NULL, so deleting the thread from the assistant API cleanly resets it (the Telegram
-- side recreates one on the next message) rather than leaving a dangling id.
--
-- pending_msg_id names the assistant_messages row whose write(s) are currently
-- awaiting inline-button confirmation; it is the durable anchor for the "continue
-- only once every write in the batch is resolved" gate (no in-memory state).
--
-- poll_offset is the persisted getUpdates cursor so a redeploy does not replay the
-- whole unacknowledged backlog. link_code is a single-use ephemeral bearer credential
-- shown in Settings as a t.me deep link; it is cleared on link and regenerated on
-- (re)connect. There is deliberately NO `enabled` flag: connected ⇔ bot_token_enc <> ''.

CREATE TABLE IF NOT EXISTS telegram_settings (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    bot_token_enc   TEXT NOT NULL DEFAULT ''
                    CHECK (bot_token_enc = '' OR bot_token_enc LIKE 'enc:v1:%'),
    bot_username    TEXT NOT NULL DEFAULT '',
    linked_chat_id  TEXT NOT NULL DEFAULT '',
    linked_user_id  TEXT NOT NULL DEFAULT '',
    linked_name     TEXT NOT NULL DEFAULT '',
    link_code       TEXT NOT NULL DEFAULT '',
    conversation_id TEXT REFERENCES assistant_conversations(id) ON DELETE SET NULL,
    pending_msg_id  TEXT NOT NULL DEFAULT '',
    poll_offset     BIGINT NOT NULL DEFAULT 0,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO telegram_settings (id) VALUES (1) ON CONFLICT (id) DO NOTHING;
