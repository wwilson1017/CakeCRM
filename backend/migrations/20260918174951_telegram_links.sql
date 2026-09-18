-- Per-user Telegram links (issue #193, multi-user Phase B / B4).
--
-- Telegram stops being a singleton binding. `telegram_settings` keeps the INSTALL-WIDE
-- bot config it always owned — `bot_token_enc`, `bot_username`, `poll_offset` — and is
-- still admin-connected with ONE poller. Everything PERSONAL moves here, one row per
-- seat: its own link code, chat binding, Telegram identity, assistant conversation and
-- pending-confirmation batch. One bot serves N chats; the singleton was CakeCRM's schema,
-- not a Telegram limit.
--
-- NO CLAIM UPDATE, deliberately. The legacy singleton's `linked_user_id` holds a
-- *Telegram* user id, not a `users.id`, so there is nothing to derive an owner from and
-- guessing one could hand a member's Telegram to the admin. The previously linked human
-- re-links via their own code (README upgrade note). Fresh install and upgrade are
-- therefore identical: an empty table.
--
-- The singleton's `linked_chat_id`, `linked_user_id`, `linked_name`, `link_code`,
-- `conversation_id` and `pending_msg_id` columns become VESTIGIAL from this release —
-- written by nothing, read by nothing. They are left in place rather than dropped so this
-- migration stays purely additive (the `auth_credential` precedent); a later release
-- drops them.
--
-- NOT CRM data: this table joins neither the `_truncate_all` sweep nor `is_crm_empty`.
-- It carries real FKs to `users` and `assistant_conversations`, so the no-FK-less-tables
-- rule is satisfied.

CREATE TABLE IF NOT EXISTS telegram_links (
    id               BIGSERIAL PRIMARY KEY,
    -- ON DELETE CASCADE: a chat binding is personal data and follows its person (the
    -- `totp_config` / `assistant_conversations.user_id` idiom, not `owner_id`'s SET NULL).
    -- Deleting a seat must kill its Telegram, never orphan a live binding.
    user_id          INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    link_code        TEXT NOT NULL DEFAULT '',
    chat_id          TEXT NOT NULL DEFAULT '',
    telegram_user_id TEXT NOT NULL DEFAULT '',
    telegram_name    TEXT NOT NULL DEFAULT '',
    conversation_id  TEXT REFERENCES assistant_conversations(id) ON DELETE SET NULL,
    pending_msg_id   TEXT NOT NULL DEFAULT '',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The dispatch invariant: one private chat resolves to AT MOST one seat, so an inbound
-- update is never ambiguous. Partial, because '' is the unbound state every fresh and
-- every unlinked row sits in.
CREATE UNIQUE INDEX IF NOT EXISTS uq_telegram_links_chat
    ON telegram_links (chat_id) WHERE chat_id <> '';

-- "The row holding this code" is provably singular, so `claim_link` can consume by value.
-- Codes are 128-bit CSPRNG (`secrets.token_urlsafe(16)`), so this never fires in practice
-- — it is the database stating the invariant the claim path depends on.
CREATE UNIQUE INDEX IF NOT EXISTS uq_telegram_links_code
    ON telegram_links (link_code) WHERE link_code <> '';
