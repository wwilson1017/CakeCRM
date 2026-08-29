-- Chatter note attachments (issue #57) — files and images on deal/contact/company notes.
--
-- WHERE THE BYTES LIVE, AND WHY (the gate question on #57 asked this be settled before
-- any code landed, so it is settled here, in the schema that encodes the answer):
--
-- The issue said "files on local disk next to the DB, no object store". That would in
-- fact have worked on BOTH deploy targets, which is worth writing down because the repo
-- has never stated the distinction in prose and one migration header (the
-- assistant_context_files one) reads like a contradiction:
--
--   * `python run.py` locally — `backend/data/` is an ordinary directory and persists.
--   * Railway — the container filesystem IS ephemeral and is replaced on every redeploy,
--     EXCEPT the volume that `railway.json` requires at `/app/backend/data`
--     (`requiredMountPath`; the Dockerfile's `WORKDIR /app` + `COPY backend/ ./backend/`
--     put `backend/data` exactly there, and a deploy without that volume does not start).
--     That volume is why the branding logo and the encryption-key fallback survive today.
--
-- So both options were live, and this one takes Postgres anyway:
--
--   1. ONE store, one transaction, one backup. A file+row design is two consistency
--      domains: a crash between the file write and the row insert, orphaned bytes after
--      every DELETE and TRUNCATE, and a split backup — the README's documented rollback
--      is "restore a pg_dump", which contains no files. This repo has no orphan-sweep
--      machinery and attachments are not a good reason to build the first one.
--   2. The CRM-reset invariant stays airtight for free. `_truncate_all` and the
--      `delete_contact`/`delete_company` cascades are this codebase's hardest-won
--      invariant; with the bytes in the row, TRUNCATE and DELETE take them along
--      atomically. With files, every one of those sites leaks bytes.
--   3. It is already this repo's instinct — `20260821112055_assistant_context_files.sql`
--      says "CakeCRM runs one database, so the file becomes a row" — and it is the native
--      shape of the thumbnail pipeline being ported (the blueprint's gallery, which this
--      borrows `core/thumbnails.py` from, stores its images in Postgres too).
--
-- The honest cost, stated rather than hidden: attachments GROW the database and every
-- `pg_dump` taken from it, `core/postgres.py` has no streaming primitive so a full-size
-- read materializes in the worker, and a hard DELETE does not immediately shrink the
-- TOAST files on disk. That is bounded by a 10 MB per-attachment cap, a 10-per-note cap,
-- list views that only ever fetch the <=28 KB thumbnail, and revalidated (never
-- `immutable`) caching. It is NOT bounded at install level: nothing stops a member
-- writing many notes. That is accepted for a self-hosted CRM whose members can already
-- delete every record in it; an install-level quota is the upgrade path if a real deploy
-- ever needs one.
--
-- Two more limits, stated because the per-request caps above can read as stronger than
-- they are. (1) PEAK MEMORY is per-request x concurrency, not 10 MB: FastAPI serves sync
-- handlers from a threadpool (~40 threads by default) on one gunicorn worker, and each
-- in-flight download holds the row's bytes plus the `bytes()` copy psycopg2's memoryview
-- requires, so the worst case is a few hundred MB, not 10. What keeps it far below that in
-- practice is that list views fetch only thumbnails, originals load on an explicit open,
-- the ETag/304 path re-reads nothing, and the client aborts a download it navigated away
-- from. A weighted admission gate held until the response is transmitted is the upgrade
-- path; it is not built, because nothing has measured a need for it. (2) An oversized
-- multipart body is spooled by Starlette BEFORE any route code runs, so the per-route cap
-- cannot prevent it — `main.MAX_REQUEST_BYTES` is the middleware backstop that can.
--
-- Idempotency is keyed on CONTENT, `(note_id, sha256)`, which also means the same bytes
-- cannot be attached twice to one note under two different names: the second upload
-- returns the first row. That is the deliberate trade — a retry after a lost response is
-- common and must not duplicate, while attaching one identical file twice to one note
-- under two names is not a thing users ask for. A client-generated request key would
-- separate the two if it ever became one.
--
-- A REAL FK to crm_chatter, unlike the polymorphic CRM tables around it. The parent here
-- is a single table with a single id, so there is nothing polymorphic to model, and the
-- FK buys the whole lifecycle: ON DELETE CASCADE means `delete_contact`/`delete_company`
-- clear attachments through their existing `DELETE FROM crm_chatter` with no new code,
-- and no id-reuse orphan is representable. The obligation it creates is the same one
-- `deal_stage_events` and `task_projects` carry: Postgres REFUSES to truncate a
-- referenced table on its own, so this table MUST appear in BOTH `_truncate_all`
-- statements in crm/service.py, in the same statement as crm_chatter.
--
-- `filename` is NOT NULL and normalized by the service before it ever arrives
-- (`attachment_service.normalize_filename`): it is echoed into a Content-Disposition
-- header, so an unbounded or path-bearing name is a header problem, and a NULL one is a
-- crash in the encoder. "attachment" is the fallback for a nameless upload.
--
-- `mime_type` is derived from MAGIC BYTES server-side and is never the client's declared
-- type: only four image types and PDF keep their real type; everything else — SVG very
-- much included — is stored and served as an inert application/octet-stream download.
-- These bytes are served from the app's own origin, where a stored XSS would reach the
-- session token, and the branding-logo work already learned this lesson twice.
--
-- The thumb pair mirrors the blueprint's `crm_images_thumb_pair`: either both thumbnail
-- columns are set or neither is, and the mime is one of the two the encoder emits. NULL
-- is a legal terminal state — it means "no thumbnail", either because the attachment is
-- not an image or because the decompression-bomb ceilings refused it — and the UI treats
-- such a row as a download-only chip. Note the 28 KB size cap is an APPLICATION
-- convention (attachment_service.THUMB_MAX_BYTES), not a database invariant; the CHECK
-- validates pairing and mime only.
--
-- Deliberately NOT built: `thumb_attempted_at` and a read-path healer (the blueprint's
-- gallery needs them because it had legacy rows to backfill; this table is brand new and
-- generates at upload time, so a healer would be machinery with nothing to heal — it is
-- the named upgrade path if transient decode-slot losses ever matter); void/audit columns
-- (this repo hard-deletes chatter on entity delete and has no audit chain to record a
-- void reason into); and width/height/duration metadata (the blueprint stores what its
-- client measured; here the server generates a real thumbnail and needs neither).

CREATE TABLE IF NOT EXISTS crm_chatter_attachments (
    id          SERIAL PRIMARY KEY,
    note_id     INTEGER NOT NULL REFERENCES crm_chatter(id) ON DELETE CASCADE,
    filename    TEXT NOT NULL CHECK (btrim(filename) <> ''),
    -- An ALLOW-LIST in the schema, not just in the service. `mime_type` becomes the
    -- Content-Type of a response served from the app's own origin, so "only these six
    -- values are storable" is a security invariant, and this repo's own rule for security
    -- invariants is that a code-only convention drifts while a database constraint cannot
    -- (the same reasoning behind assistant_context_files' GENERATED columns). Today
    -- `attachment_service.create_attachment` is the only writer; this is the backstop for
    -- the next one — an import script, a manual repair, a later refactor that trusts a
    -- different caller. Adding a supported type is deliberately a migration.
    mime_type   TEXT NOT NULL CHECK (mime_type IN (
                    'image/jpeg', 'image/png', 'image/gif', 'image/webp',
                    'application/pdf', 'application/octet-stream'
                )),
    byte_size   INTEGER NOT NULL CHECK (byte_size > 0),
    sha256      TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    data        BYTEA NOT NULL,
    thumb_data  BYTEA,
    thumb_mime  TEXT,
    -- Who UPLOADED it, which is authorship, not ownership — the same distinction
    -- crm_chatter.author_id draws. A real FK is safe here where it is not against the CRM
    -- entity tables: `users` is never truncated by any reset path.
    uploaded_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT crm_chatter_attachments_thumb_pair CHECK (
        (thumb_data IS NULL AND thumb_mime IS NULL)
        OR (thumb_data IS NOT NULL
            AND thumb_mime IS NOT NULL
            AND thumb_mime IN ('image/webp', 'image/jpeg'))
    )
);

-- Retry idempotency, enforced by the database rather than by a check-then-insert: a
-- client that retries after a lost response re-sends identical bytes and must not create
-- a second row. Doubles as the note-scoped lookup index (at most 10 rows per note, so no
-- separate ordering index is warranted).
CREATE UNIQUE INDEX IF NOT EXISTS crm_chatter_attachments_dedupe
    ON crm_chatter_attachments (note_id, sha256);
