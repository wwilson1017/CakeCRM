-- #293 (port of todo-gtd b55cacb / cake_os #3764): the no-login todo app at /todo
-- opens by per-device browser sessions instead of a secret link.
--
-- Hashes only: the cookie and the sign-in link carry the plaintext, these tables hold
-- its SHA-256. Timestamps are bound by crm/todo_sessions.py, never DEFAULT now(), so
-- every expiry and cap decision runs on one injectable clock.
--
-- Credential rows, deliberately OUTSIDE crm.service._truncate_all (like saved_views):
-- a CRM data reset must not sign phones out.
--
-- crm_meta.todo_web_token is NOT dropped: the previous revision still reads it during a
-- rolling deploy. Nothing reads it from this revision on; dropping it is a follow-up.

CREATE TABLE IF NOT EXISTS todo_web_sessions (
    id            BIGSERIAL PRIMARY KEY,
    token_hash    TEXT NOT NULL UNIQUE,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at    TIMESTAMPTZ NOT NULL,
    last_used_at  TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_todo_web_sessions_seat
    ON todo_web_sessions (user_id, last_used_at);

CREATE TABLE IF NOT EXISTS todo_web_signin_tokens (
    id          BIGSERIAL PRIMARY KEY,
    token_hash  TEXT NOT NULL UNIQUE,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at  TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_todo_web_signin_tokens_seat
    ON todo_web_signin_tokens (user_id, expires_at);
