-- Issue #5 — assistant long-term memory (temporal facts) + dreaming audit.
--
-- Reimplements chatty's core/agents/memory/db.py *facts* table on Postgres;
-- chatty's SQLite FTS5 virtual table becomes a generated `tsvector` column + GIN
-- index. Chatty's file-based "dreaming" (rename dormant *.md context files) has
-- no target here — CakeCRM's assistant has no context-file store — so dreaming is
-- adapted to soft-archive dormant *facts* (archived_at). No separate usage-events
-- table: retrieval signals live on the fact row (retrieval_count / last_retrieved_at),
-- exactly as chatty tracks fact usage. Everything here is pure-algorithmic — no AI.
--
-- FTS config is `simple` (not `english`): facts are entity triples of proper nouns
-- (names, companies, dates); english stemming + stopwords would make names such as
-- "Will", "US", "IT" unsearchable. `simple` lowercase-folds without stemming.

CREATE TABLE IF NOT EXISTS memory_facts (
    id                BIGSERIAL PRIMARY KEY,
    subject           TEXT NOT NULL,
    predicate         TEXT NOT NULL,
    object            TEXT NOT NULL,
    valid_from        DATE NOT NULL DEFAULT CURRENT_DATE,   -- informational metadata
    valid_to          DATE,                                 -- set by invalidate_fact; NULL = live
    created_by        TEXT NOT NULL DEFAULT 'assistant',
    source            TEXT NOT NULL DEFAULT '',
    confidence        DOUBLE PRECISION NOT NULL DEFAULT 1.0
                      CHECK (confidence >= 0.0 AND confidence <= 1.0),
    memory_type       TEXT,                                 -- validated in code (memory/types.py); no CHECK so the taxonomy can evolve
    retrieval_count   INTEGER NOT NULL DEFAULT 0,           -- dreaming usage signal
    last_retrieved_at TIMESTAMPTZ,                          -- dreaming usage signal
    archived_at       TIMESTAMPTZ,                          -- dreaming soft-archive marker; NULL = not archived
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Two-arg to_tsvector(regconfig, text) is IMMUTABLE (legal in a generated
    -- column); the one-arg form is only STABLE and Postgres rejects it here.
    search_tsv        tsvector GENERATED ALWAYS AS (
        to_tsvector('simple', subject || ' ' || predicate || ' ' || object)
    ) STORED,
    CONSTRAINT memory_facts_valid_window CHECK (valid_to IS NULL OR valid_to >= valid_from)
);

CREATE INDEX IF NOT EXISTS idx_memory_facts_tsv     ON memory_facts USING GIN (search_tsv);
CREATE INDEX IF NOT EXISTS idx_memory_facts_subject ON memory_facts (subject);
CREATE INDEX IF NOT EXISTS idx_memory_facts_type    ON memory_facts (memory_type)
    WHERE memory_type IS NOT NULL;
-- Hot path: live (non-expired, non-archived) facts ordered for context injection
-- and query_facts' default listing.
CREATE INDEX IF NOT EXISTS idx_memory_facts_live    ON memory_facts (confidence DESC, created_at DESC)
    WHERE valid_to IS NULL AND archived_at IS NULL;

-- Audit trail so "the dreaming job runs on schedule" is observable, and the
-- last-successful-run guard for the scheduler. One row per cycle, even no-ops.
-- status='ok' rows gate the due check; status='error' rows record failures
-- (a failed cycle must NOT suppress the next attempt).
CREATE TABLE IF NOT EXISTS dreaming_runs (
    id             BIGSERIAL PRIMARY KEY,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at    TIMESTAMPTZ,
    status         TEXT NOT NULL DEFAULT 'ok',              -- 'ok' | 'error'
    facts_scored   INTEGER NOT NULL DEFAULT 0,
    facts_archived INTEGER NOT NULL DEFAULT 0,
    details        JSONB,                                   -- {"scores": top-10, "archived": [ids]}
    error          TEXT,
    duration_ms    INTEGER NOT NULL DEFAULT 0
);

-- Due-guard reads the latest successful run; index that access path.
CREATE INDEX IF NOT EXISTS idx_dreaming_runs_ok_finished
    ON dreaming_runs (finished_at DESC) WHERE status = 'ok';
