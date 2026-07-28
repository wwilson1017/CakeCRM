-- Gmail touch-scan heartbeat job (issue #17): a read-only background scan of the
-- connected inbox that logs inbound email touches to the CRM timeline so #16's
-- touch counts see exchanges nobody logged. NEW capability (no CAKE OS blueprint).
--
-- Idempotency is a LEDGER, not an activity_log constraint. Unmatched senders have
-- no activity_log row to dedup on, so a per-message ledger gives uniform once-only
-- semantics for BOTH the logged touch AND the unmatched-correspondent counter.
-- activity_log itself is deliberately left untouched (it is a shared table edited by
-- sibling issues, and its reset TRUNCATE string is pinned by tests); provenance
-- lives here instead, via the ledger's activity_id column.
--
-- The ledger's contact_id / deal_id / activity_id are AUDIT columns with NO foreign
-- keys ON PURPOSE (the crm_field_values polymorphic-no-FK precedent). An FK into
-- contacts / deals / activity_log would make Postgres reject the CRM reset paths
-- (crm/service.py TRUNCATE ... contacts, deals, activity_log RESTART IDENTITY —
-- TRUNCATE refuses a table referenced by an inbound FK). Orphan ids in an
-- append-only audit ledger are harmless.

-- Cadence claim + last-run summary singleton. The rowcount-UPDATE claim on
-- last_scan_at is the due-guard AND the won-the-claim guard in one atomic
-- statement, mirroring heartbeat_state.last_turn_at (heartbeat/service.py).
CREATE TABLE IF NOT EXISTS gmail_scan_state (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    last_scan_at        TIMESTAMPTZ,                  -- claim timestamp (the due-guard column)
    last_finished_at    TIMESTAMPTZ,
    last_status         TEXT NOT NULL DEFAULT '',     -- '' | 'running' | 'ok' | 'error'
    last_error          TEXT NOT NULL DEFAULT '',
    last_messages_seen  INTEGER NOT NULL DEFAULT 0,   -- messages returned by the Gmail query
    last_messages_new   INTEGER NOT NULL DEFAULT 0,   -- first-sight ledger claims this pass
    last_touches_logged INTEGER NOT NULL DEFAULT 0    -- activity_log rows inserted this pass
);
INSERT INTO gmail_scan_state (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

-- One row per Gmail message ever examined: the idempotency ledger + audit trail.
-- message_id is Gmail's internal per-account id (stable, always present). See the
-- header note on why contact_id / deal_id / activity_id carry no FK.
CREATE TABLE IF NOT EXISTS gmail_scanned_messages (
    message_id   TEXT PRIMARY KEY,
    sender_email TEXT NOT NULL DEFAULT '',            -- lowercased parsed sender
    outcome      TEXT NOT NULL DEFAULT 'seen',        -- seen|logged|unmatched|skipped_self|skipped_invalid
    contact_id   INTEGER,                             -- audit only (no FK — see header)
    deal_id      INTEGER,                             -- audit only (no FK — see header)
    activity_id  INTEGER,                             -- audit only (no FK — see header)
    scanned_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Unmatched-but-frequent correspondents -> "create contact?" suggestions. One row
-- per lowercased sender; alerted_at makes the suggestion fire at most once.
CREATE TABLE IF NOT EXISTS gmail_unmatched_correspondents (
    email         TEXT PRIMARY KEY,                   -- lowercased sender address
    message_count INTEGER NOT NULL DEFAULT 1,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    alerted_at    TIMESTAMPTZ
);

-- Exact case-insensitive contact-by-email lookup for sender matching. The existing
-- idx_contacts_email is case-sensitive and cannot serve lower(email) = %s.
CREATE INDEX IF NOT EXISTS idx_contacts_email_lower ON contacts (lower(email));
