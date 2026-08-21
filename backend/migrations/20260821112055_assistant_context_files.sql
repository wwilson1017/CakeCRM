-- Assistant context files (issue #72 Phase 1) — Baker's markdown knowledge store.
--
-- Ports chatty's core/agents/context_manager.py, whose unit is a *.md file on disk
-- synced to GCS. Railway filesystems are ephemeral and CakeCRM runs one database, so
-- the file becomes a row: `filename` is the key, `content` is the body. Chatty's
-- `_load-order.json` sidecar, atomic_write and GCS sync have no target here and are
-- not ported; ordering is an ORDER BY, and the write is an UPDATE.
--
-- Chatty's namespace is FLAT (its _safe_filename rejects '/'), with topics and daily
-- notes living in separate directories reached by different methods. One table needs
-- one namespace, so the directory becomes a filename prefix: `topics/<slug>.md` and
-- `daily/YYYY-MM-DD.md`, with `soul.md` and `MEMORY.md` unprefixed.
--
-- `kind` and `is_protected` are GENERATED columns rather than values the application
-- sets. Deriving them "in code only" is a convention a later migration, a manual repair
-- or a new write path can silently break, leaving rows like ('soul.md', kind='topic')
-- that the prompt builder would then mis-file. Generated from `filename` alone, the
-- invariant is enforced by Postgres and cannot drift. Both expressions are IMMUTABLE
-- (CASE over constants), which generated columns require.
--
-- The two protected files are seeded with EMPTY content on purpose. Their default text
-- lives in Python (`assistant.identity.DEFAULT_SOUL`), used as the fallback whenever the
-- stored content is blank — the same blank-means-default pattern as
-- assistant_identity.personality. Seeding the text instead would mean a later boot could
-- overwrite an identity Baker or the user had already rewritten, and would hide the
-- default from tests/test_prompt_genericization.py, which scans Python constants.

CREATE TABLE IF NOT EXISTS assistant_context_files (
    id           BIGSERIAL PRIMARY KEY,
    filename     TEXT NOT NULL UNIQUE,
    content      TEXT NOT NULL DEFAULT '',
    -- Short summary derived from the body on every write (see _first_headline); the
    -- manifests read this column instead of re-parsing every file each turn.
    headline     TEXT NOT NULL DEFAULT '',
    -- 'assistant' | 'user' | 'system'. Set server-side at every write site, never by
    -- the model, so "who last edited soul.md" stays trustworthy in the Memory UI.
    written_by   TEXT NOT NULL DEFAULT 'system',
    -- Soft-archive, NULL = live. Nothing sets this yet; issue #72 Phase 4 extends
    -- dreaming to score and archive dormant files. Every read carries the live
    -- predicate from day one so that phase is purely additive.
    archived_at  TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    kind TEXT GENERATED ALWAYS AS (
        CASE
            WHEN filename = 'soul.md'            THEN 'soul'
            WHEN filename = 'MEMORY.md'          THEN 'memory'
            WHEN filename LIKE 'daily/%'         THEN 'daily'
            ELSE 'topic'
        END
    ) STORED,

    -- soul.md and MEMORY.md are never deletable and never archivable, and a write to
    -- either always requires human confirmation (see context_files/tools.py).
    is_protected BOOLEAN GENERATED ALWAYS AS (
        filename IN ('soul.md', 'MEMORY.md')
    ) STORED,

    -- Mirrors memory_facts exactly: two-arg to_tsvector is IMMUTABLE (the one-arg form
    -- is only STABLE and is rejected here), 'simple' keeps proper nouns intact, and the
    -- translate() copy splits punctuation so 'topics/pricing.md' is also findable as
    -- 'topics pricing md' — a plain-word query can never reproduce the compound lexeme
    -- the parser emits for a slashed/dotted filename.
    search_tsv tsvector GENERATED ALWAYS AS (
        to_tsvector('simple',
            filename || ' ' || content || ' ' ||
            translate(filename || ' ' || content, '@./:-', '     ')
        )
    ) STORED
);

CREATE INDEX IF NOT EXISTS idx_context_files_tsv  ON assistant_context_files USING GIN (search_tsv);
-- Manifest reads are "live files of one kind, newest first".
CREATE INDEX IF NOT EXISTS idx_context_files_kind ON assistant_context_files (kind, updated_at DESC)
    WHERE archived_at IS NULL;

INSERT INTO assistant_context_files (filename, content, written_by) VALUES
    ('soul.md',   '', 'system'),
    ('MEMORY.md', '', 'system')
    ON CONFLICT (filename) DO NOTHING;
