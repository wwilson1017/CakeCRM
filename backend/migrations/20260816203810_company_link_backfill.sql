-- Company link coherence (issue #35): a second one-shot backfill.
--
-- #13's backfill (20260724062314_companies.sql) ran exactly once. Contacts
-- ingested AFTER it — CSV import, smart import, the agent tools — wrote only the
-- free-text company and never a company_id, so on affected installs the
-- Companies page and every rollup are silently empty. From #35 on, the service
-- resolves and auto-creates company links at write time
-- (crm.service.resolve_company_ids); this migration repairs the rows written in
-- between, which is the only way an already-affected install ever recovers.
--
-- Same normalization as #13 throughout: LOWER + btrim of the six ASCII
-- whitespace bytes (space, tab, LF, CR, FF, VT) — a fixed byte set, so it is
-- locale-/libc-independent. See the uq_companies_name_ci comment in the
-- companies migration.
--
-- Only rows still unlinked (company_id IS NULL) are touched, so links a user set
-- deliberately are never clobbered, and contacts.updated_at is never bumped —
-- a backfill is not an edit.
--
-- Known, accepted edge (documented in the PR): a contact whose link was
-- deliberately REMOVED while its free text still names an existing company gets
-- re-linked once. The alternative — leaving every pre-#35 import permanently
-- invisible to the Companies feature — is worse, and #13 already set the
-- precedent that unlinked + matching text implies a link.

-- 1) One company per distinct non-empty normalized name among unlinked
--    contacts. Unlike #13, the table already holds rows, so names that already
--    exist must no-op. The conflict target is the normalized-name expression
--    index specifically -- a bare ON CONFLICT would also swallow a primary-key
--    conflict and silently skip an insert, stranding the contact unlinked.
INSERT INTO companies (name)
SELECT DISTINCT ON (LOWER(btrim(company, E' \t\n\r\f\x0b')))
       btrim(company, E' \t\n\r\f\x0b')
FROM contacts
WHERE company_id IS NULL
  AND btrim(company, E' \t\n\r\f\x0b') != ''
ORDER BY LOWER(btrim(company, E' \t\n\r\f\x0b')), id
ON CONFLICT (LOWER(btrim(name, E' \t\n\r\f\x0b'))) DO NOTHING;

-- 2) Link each still-unlinked contact to its company.
UPDATE contacts SET company_id = co.id
FROM companies co
WHERE contacts.company_id IS NULL
  AND btrim(contacts.company, E' \t\n\r\f\x0b') != ''
  AND LOWER(btrim(contacts.company, E' \t\n\r\f\x0b'))
    = LOWER(btrim(co.name, E' \t\n\r\f\x0b'));

-- NO deal-inheritance step, deliberately — this is where this backfill departs
-- from #13's.
--
-- #13 could safely set deals.company_id for every deal with a NULL company
-- because company_id had just been ADDED to the table: every deal was NULL, so
-- "NULL" unambiguously meant "never set". That is no longer true. A user can
-- now clear a deal's company on its own (DealForm sends an explicit null), and
-- nothing in the schema distinguishes "never linked" from "deliberately
-- unlinked" — so inheriting would silently overwrite a deliberate choice.
--
-- The upside would have been small anyway: no ingestion path creates deals
-- (CSV/vCard/smart import create contacts only), so a deal's company is always
-- something a human or the assistant set explicitly on the deal itself.
-- Contact links — the actual reason the Companies page was empty — are
-- repaired above.
