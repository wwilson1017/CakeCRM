-- Multi-user Phase A (issue #60): accounts, admin/member roles, record ownership.
--
-- This migration only builds the SCHEMA. Seeding the first admin is deliberately
-- NOT done here: an upgrading install's live credential is a bcrypt hash sitting in
-- auth_credential, and the admin's email comes from an env var — neither is
-- reachable from SQL. users.bootstrap.ensure_bootstrap_admin() runs in the lifespan
-- right after the migration runner and does both, atomically.

-- ── Accounts ────────────────────────────────────────────────────────────────
--
-- password_hash is NOT NULL and always bcrypt. It is never NULL and never a
-- plaintext fallback: users.verify_user_password() requires a $2a$/$2b$ prefix and
-- returns False for anything else, so a placeholder account written with a sentinel
-- like '!' (issue #61 imports rep names that have no seat yet) can never
-- authenticate — it fails closed rather than degrading to a comparison.
--
-- token_epoch is the per-user half of #78's session-invalidation scheme: it is
-- stamped into every JWT as pwd_epoch and bumped whenever that user's password
-- changes, so their other sessions stop working at their next request. It is
-- per-user precisely so one person's password change cannot sign out the team.
CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    email         TEXT NOT NULL,
    name          TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('admin', 'member')),
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    token_epoch   INTEGER NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Case-insensitive uniqueness on the SAME normalization the service applies in
-- Python: LOWER + btrim of the six ASCII whitespace bytes (space, tab, LF, CR, FF,
-- VT). A fixed byte set, so it is locale-/libc-independent, and Python's
-- str.strip(_WS) with that same set agrees byte for byte. Identical idiom to
-- uq_companies_name_ci — see the comment in 20260724062314_companies.sql.
--
-- Deliberately NOT Python's Unicode-aware str.strip(): cake_os shipped a real bug
-- where an NBSP pasted from a spreadsheet made owner filters silently match zero
-- rows, because SQL btrim() strips ASCII spaces only while str.strip() strips all
-- Unicode whitespace. Keeping both sides on the fixed byte set means a pasted NBSP
-- is preserved verbatim on both sides and simply reads as part of the address.
CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email_ci
    ON users (LOWER(btrim(email, E' \t\n\r\f\x0b')));

-- ── Two-factor: re-key from a singleton to per-user ─────────────────────────
--
-- totp_config was pinned to one row (id INTEGER PRIMARY KEY CHECK (id = 1)) and
-- trusted_devices had no user column at all, so with real accounts every user
-- would share one authenticator secret and one trust list.
--
-- The legacy row (if this install had 2FA on) is left with user_id IS NULL here and
-- CLAIMED by ensure_bootstrap_admin() in the same transaction that creates the
-- admin. Until it is claimed it is invisible: every auth_2fa read filters
-- WHERE user_id = %s, and NULL never matches. That is the safe direction for
-- trusted_devices (an unclaimed device is trusted by nobody), and the claim is
-- inside the bootstrap transaction so a totp_config row cannot be stranded.
--
-- Constraint names are Postgres defaults; DROP ... IF EXISTS regardless, so an
-- install whose constraints were named differently still migrates.
ALTER TABLE totp_config ADD COLUMN IF NOT EXISTS user_id INTEGER
    REFERENCES users(id) ON DELETE CASCADE;
ALTER TABLE totp_config DROP CONSTRAINT IF EXISTS totp_config_id_check;
ALTER TABLE totp_config DROP CONSTRAINT IF EXISTS totp_config_pkey;
ALTER TABLE totp_config DROP COLUMN IF EXISTS id;
-- One config per user. A unique index rather than a PK because the legacy row sits
-- at NULL until the bootstrap claims it, and Postgres allows multiple NULLs in a
-- unique index (there is at most one such row in any case).
CREATE UNIQUE INDEX IF NOT EXISTS uq_totp_config_user ON totp_config(user_id);

ALTER TABLE trusted_devices ADD COLUMN IF NOT EXISTS user_id INTEGER
    REFERENCES users(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_trusted_devices_user ON trusted_devices(user_id);

-- ── Ownership and attribution ───────────────────────────────────────────────
--
-- Two DIFFERENT dimensions, deliberately not one column:
--
--   owner_id  — who the record BELONGS to. An assignment, a filter and an
--               analytics dimension. It is NOT access control: any member can read,
--               edit, delete and reassign any record (no per-object ACLs, per the
--               gate decision on #60).
--   actor_id  — who DID this particular thing. cake_os #1454/#1532 exists because
--               per-rep activity was computed from ownership and from a table
--               almost nobody writes, and reported 0 for every rep.
--
-- All nullable, forever. NULL means unassigned/unattributed, which is a real and
-- supported state: the Gmail touch scan, the assistant's background turn and #61's
-- importer all legitimately produce ownerless rows, and defaulting them to the
-- admin would fabricate ownership rather than record it.
--
-- ON DELETE SET NULL, not CASCADE: deleting a user must never delete their deals.
-- (The app has no user-delete route at all — deactivation only — but the FK must
-- still express the right intent for anyone operating the database directly.)
ALTER TABLE contacts     ADD COLUMN IF NOT EXISTS owner_id  INTEGER REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE companies    ADD COLUMN IF NOT EXISTS owner_id  INTEGER REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE deals        ADD COLUMN IF NOT EXISTS owner_id  INTEGER REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE tasks        ADD COLUMN IF NOT EXISTS owner_id  INTEGER REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE activity_log ADD COLUMN IF NOT EXISTS actor_id  INTEGER REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE crm_chatter  ADD COLUMN IF NOT EXISTS author_id INTEGER REFERENCES users(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_contacts_owner  ON contacts(owner_id);
CREATE INDEX IF NOT EXISTS idx_companies_owner ON companies(owner_id);
CREATE INDEX IF NOT EXISTS idx_deals_owner     ON deals(owner_id);
CREATE INDEX IF NOT EXISTS idx_tasks_owner     ON tasks(owner_id);
-- The per-rep analytics query filters by window and groups by actor, so the
-- timestamp rides the index.
CREATE INDEX IF NOT EXISTS idx_activity_actor  ON activity_log(actor_id, created_at);
CREATE INDEX IF NOT EXISTS idx_chatter_author  ON crm_chatter(author_id, created_at);
