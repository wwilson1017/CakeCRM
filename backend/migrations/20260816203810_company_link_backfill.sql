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

-- 2) Link each still-unlinked contact to its company, and 3) let that contact's
--    still-unlinked deals inherit the company.
--
--    These are ONE statement on purpose. #13's step 3 inherited for every deal
--    with company_id IS NULL, which was safe only because company_id had just
--    been added and no deliberate unlink could exist yet. Today a user can
--    unlink a deal on its own (DealForm sends an explicit null), so repeating
--    that broad pattern would silently re-link deals somebody deliberately
--    detached. The CTE narrows inheritance to deals whose contact was linked by
--    THIS migration.
WITH linked AS (
    UPDATE contacts SET company_id = co.id
    FROM companies co
    WHERE contacts.company_id IS NULL
      AND btrim(contacts.company, E' \t\n\r\f\x0b') != ''
      AND LOWER(btrim(contacts.company, E' \t\n\r\f\x0b'))
        = LOWER(btrim(co.name, E' \t\n\r\f\x0b'))
    RETURNING contacts.id AS contact_id, contacts.company_id AS company_id
)
UPDATE deals SET company_id = linked.company_id
FROM linked
WHERE deals.company_id IS NULL
  AND deals.contact_id = linked.contact_id;
