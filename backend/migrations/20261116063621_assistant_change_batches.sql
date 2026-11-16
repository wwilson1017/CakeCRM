-- One row per Baker change list (#371): the rows its approval card shows, staged when
-- the call is proposed, then the same rows with what apply and Undo did to each.
-- Owner-only through user_id (the conversation's seat); the conversation FK deletes it.
-- It holds deal and todo titles and values, so it joins both CRM reset branches.
-- The id is a random token, not a serial: a reset must not let an old card's id name a
-- newer batch.
CREATE TABLE IF NOT EXISTS assistant_change_batches (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    conversation_id TEXT NOT NULL REFERENCES assistant_conversations(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'applied', 'undone')),
    reason TEXT NOT NULL DEFAULT '',
    rows JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
