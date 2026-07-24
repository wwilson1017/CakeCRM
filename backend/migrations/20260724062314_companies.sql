-- Companies as first-class entities (issue #13). Promotes the free-text
-- contacts.company column to a real `companies` table with contacts/deals
-- linking to it, and a company detail page rolling up both.
--
-- Adapted from cake_os/backend/apps/crm/company_service.py's crm_companies
-- schema, translated to CakeCRM conventions: unprefixed table name; single-user
-- (no owner_email/created_by_email/odoo_* columns); the five cake_os address_*
-- columns collapsed to one `address` TEXT (contacts have no structured address
-- either); text columns are NOT NULL DEFAULT '' like contacts.
--
-- Runs exactly once (tracked in _migrations_applied), whole file in one
-- transaction. The backfill below is a ONE-SHOT data migration — it must never
-- run at startup repeatedly, or it would clobber company links a user later
-- sets intentionally.

CREATE TABLE IF NOT EXISTS companies (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    domain      TEXT NOT NULL DEFAULT '',
    industry    TEXT NOT NULL DEFAULT '',
    phone       TEXT NOT NULL DEFAULT '',
    address     TEXT NOT NULL DEFAULT '',
    notes       TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'active',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Uniqueness rule: one company per case-insensitive, whitespace-trimmed name.
-- regexp_replace(x, '^\s+|\s+$', '', 'g') is the SINGLE normalization used
-- everywhere in this migration; Postgres \s = [ \t\n\r\f\v], matching Python
-- str.strip() (the service stores name.strip()) so backfill + service writes
-- can never disagree on what counts as a duplicate.
CREATE UNIQUE INDEX IF NOT EXISTS uq_companies_name_ci
    ON companies (LOWER(regexp_replace(name, '^\s+|\s+$', '', 'g')));
CREATE INDEX IF NOT EXISTS idx_companies_name   ON companies(name);
CREATE INDEX IF NOT EXISTS idx_companies_status ON companies(status);

-- Nullable FKs: a contact/deal may have no company. ON DELETE SET NULL so
-- deleting a company unlinks its contacts/deals rather than deleting them.
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS
    company_id INTEGER REFERENCES companies(id) ON DELETE SET NULL;
ALTER TABLE deals ADD COLUMN IF NOT EXISTS
    company_id INTEGER REFERENCES companies(id) ON DELETE SET NULL;
-- idx_contacts_company_id keeps the _id suffix (idx_contacts_company already
-- exists on the free-text company column); idx_deals_company drops it to match
-- the crm_core convention (idx_deals_contact, idx_tasks_contact, …).
CREATE INDEX IF NOT EXISTS idx_contacts_company_id ON contacts(company_id);
CREATE INDEX IF NOT EXISTS idx_deals_company       ON deals(company_id);

-- ── One-shot backfill ────────────────────────────────────────────────────────
-- 1) One company per distinct non-empty normalized company name. The display
--    spelling is taken from the lowest-id contact bearing that name (DISTINCT ON
--    keeps the first row per group after ORDER BY). No ON CONFLICT clause: the
--    table is brand-new and DISTINCT ON already emits one row per unique-index
--    key, so any conflict here signals real corruption and should fail loudly.
INSERT INTO companies (name)
SELECT DISTINCT ON (LOWER(regexp_replace(company, '^\s+|\s+$', '', 'g')))
       regexp_replace(company, '^\s+|\s+$', '', 'g')
FROM contacts
WHERE regexp_replace(company, '^\s+|\s+$', '', 'g') != ''
ORDER BY LOWER(regexp_replace(company, '^\s+|\s+$', '', 'g')), id;

-- 2) Link each contact to its company (only where not already linked).
--    Deliberately does NOT bump contacts.updated_at (a backfill is not an edit).
UPDATE contacts SET company_id = co.id
FROM companies co
WHERE contacts.company_id IS NULL
  AND regexp_replace(contacts.company, '^\s+|\s+$', '', 'g') != ''
  AND LOWER(regexp_replace(contacts.company, '^\s+|\s+$', '', 'g'))
    = LOWER(regexp_replace(co.name, '^\s+|\s+$', '', 'g'));

-- 3) Deals inherit their contact's company (runs after step 2 so company_id is
--    populated). One-shot: later user re-links to a different company stick.
UPDATE deals SET company_id = c.company_id
FROM contacts c
WHERE deals.company_id IS NULL
  AND deals.contact_id = c.id
  AND c.company_id IS NOT NULL;
