-- #282: a Baker turn runs as a detached task and writes its SSE frames here, so a browser
-- that dropped can replay from a seq and tail live. These are TRANSPORT rows, not the
-- record: the record of a turn is still the assistant_messages rows the engine saves. A
-- turn and its frames are pruned 24h after it ends (assistant/turns.py, run by
-- heartbeat.service.maintenance_tick). No FK to assistant_conversations on purpose: a
-- turn exists before its conversation_id frame. user_id is not an FK to users either: a
-- deleted seat's transport rows age out within a day and must not hold up the delete.
CREATE TABLE IF NOT EXISTS chat_turns (
    id                  TEXT PRIMARY KEY,            -- client-minted UUID (canonicalised)
    user_id             INTEGER NOT NULL,            -- the seat that started it: the one authz rule
    conversation_id     TEXT,                        -- NULL until the turn's conversation_id frame
    status              TEXT NOT NULL DEFAULT 'running'
                        CHECK (status IN ('running', 'finished', 'stopped', 'dead')),
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    heartbeat_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at         TIMESTAMPTZ,
    last_seq            INTEGER NOT NULL DEFAULT -1, -- highest seq COMMITTED to chat_turn_events
    cancel_requested_at TIMESTAMPTZ,
    cancel_reason       TEXT
);
CREATE INDEX IF NOT EXISTS idx_chat_turns_running
    ON chat_turns (conversation_id, started_at) WHERE status = 'running';
CREATE INDEX IF NOT EXISTS idx_chat_turns_finished_at
    ON chat_turns (finished_at) WHERE finished_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS chat_turn_events (
    turn_id    TEXT NOT NULL REFERENCES chat_turns(id) ON DELETE CASCADE,
    seq        INTEGER NOT NULL,
    event      JSONB NOT NULL,                       -- the SSE payload dict, `seq` included
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (turn_id, seq)
);
