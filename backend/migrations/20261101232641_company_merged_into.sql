-- #334: keep absorbed spellings, following their immutable successor chain on writes.
ALTER TABLE companies ADD COLUMN IF NOT EXISTS
    merged_into_id INTEGER REFERENCES companies(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_companies_merged_into ON companies(merged_into_id);
DO $$ BEGIN
    ALTER TABLE companies ADD CONSTRAINT companies_merged_stays_archived
        CHECK (merged_into_id IS NULL OR status = 'archived');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Merge/delete take the gate exclusively; company-assigning writers take it shared
-- before locking a contact/deal. Keep the key in this one function.
CREATE OR REPLACE FUNCTION company_merge_gate_key() RETURNS BIGINT AS $$
    SELECT 334001::bigint
$$ LANGUAGE sql IMMUTABLE;

CREATE OR REPLACE FUNCTION company_merge_gate() RETURNS void AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(company_merge_gate_key());
END $$ LANGUAGE plpgsql;

-- NULL in, NULL out. A merged row cannot be merged again, so chains are acyclic.
-- No row locks: the shared gate keeps the chain stable until this transaction ends.
CREATE OR REPLACE FUNCTION company_follow_merge(cid INTEGER) RETURNS INTEGER AS $$
DECLARE m INTEGER;
BEGIN
    IF cid IS NULL THEN RETURN NULL; END IF;
    PERFORM pg_advisory_xact_lock_shared(company_merge_gate_key());
    LOOP
        SELECT merged_into_id INTO m FROM companies WHERE id = cid;
        EXIT WHEN m IS NULL; -- live end, or missing id (the FK rejects it)
        cid := m;
    END LOOP;
    RETURN cid;
END $$ LANGUAGE plpgsql;
