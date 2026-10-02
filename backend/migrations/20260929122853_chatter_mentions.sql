-- Chatter note @-mentions (issue #235, port of cake_os #2934).
--
-- A note can mention seats. Each row says "this person was mentioned on that note" and
-- is written in the SAME transaction as the note (or the edit) that carries it. The
-- notification itself is sent post-commit by the router; the row is routing metadata
-- for a notification that already fired, and the note text is the record.
--
-- Real foreign keys, unlike upstream. cake_os keys mentions on (surface, note_id) with
-- no FK because its notes live in five stores; CakeCRM has exactly one, crm_chatter,
-- so the attachments precedent (#57) applies instead: ON DELETE CASCADE means
-- delete_contact/delete_company need no new code, and the FK means this table MUST ride
-- both _truncate_all statements (Postgres refuses to truncate crm_chatter alone). A
-- deleted user takes their mention rows with them; users are deactivated, not deleted,
-- in practice, so the cascade is a backstop.
--
-- display_name is frozen at post time from users.name (or email), never from client
-- text: the composer inserts "@<that name>" into the message, and the thread highlights
-- exactly that token, so a later rename must not break the highlight on old notes.
--
-- UNIQUE (note_id, user_id): mentioning one person twice on one note is one mention and
-- one notification, and it is what the edit path's ON CONFLICT DO NOTHING keys on.
CREATE TABLE IF NOT EXISTS crm_chatter_mentions (
    id           SERIAL PRIMARY KEY,
    note_id      INTEGER NOT NULL REFERENCES crm_chatter(id) ON DELETE CASCADE,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    display_name TEXT NOT NULL CHECK (btrim(display_name) <> ''),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT crm_chatter_mentions_note_user_key UNIQUE (note_id, user_id)
);

-- A notification may point somewhere in the app (issue #235: a mention links to the
-- record it was made on). Nullable: every existing sender stays link-less. Only a
-- same-origin path is ever stored — notifications.delivery validates it.
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS link TEXT;
