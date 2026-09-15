-- Saved views (issue #181): team-visible snapshots of a collection surface's screen state —
-- every facet selection, the search text, the sort field/direction and the view mode — keyed
-- by that surface's `CollectionStorage.key` (crm_pipeline, crm_contacts, …).
--
-- This is the first server-side preference store in the repo (#124 shipped a per-device
-- localStorage toggle and explicitly declined to build one). It is deliberately ONE table
-- behind ONE REST surface, /api/saved-views, in backend/saved_views/ rather than backend/crm/:
-- a view belongs to a SURFACE, not to a CRM record. It is therefore in NEITHER branch of
-- crm.service._truncate_all — a demo-clear and a full clear_all both leave saved views intact.
--
-- That is deliberately one step further than crm_field_definitions, the nearest precedent,
-- which survives demo-clear but IS wiped by clear_all. The two differ in what they reference.
-- A field definition describes the SHAPE of CRM data, so surviving a full reset would leave
-- schema describing rows that no longer exist. A saved view describes a QUERY over stage keys,
-- date bounds, user ids and search text — none of which a CRM reset erases (it does not touch
-- the users table), so a view is still valid and still worth keeping afterwards. Losing every
-- teammate's views to reseed the demo data would be the surprising outcome, not the safe one.
--
-- `payload` is opaque to the server: the client coerces it on the way in exactly as it coerces
-- a restored sessionStorage envelope, so junk can never throw. It MAY reference record ids
-- (an owner-facet selection is a users.id, a stage facet a stage key) but declares no FK for
-- them; an id that no longer resolves renders as an orphan chip the filter bar keeps clickable,
-- so a stale selection is visible and removable rather than silent.
--
-- `version` is the surface's `CollectionStorage.version` at save time. The client refuses to
-- apply a view stamped with a different version — it lists it disabled with a reason — so an
-- incompatible change to a facet's key, value shape or option values cannot resurrect a stale
-- view as a silently-empty filter.
--
-- `created_by` records authorship, like crm_chatter.author_id — not ownership and not access
-- control. Every member may read and apply every view; only the creator or an admin may
-- rename, overwrite or delete one. That rule is enforced in saved_views.service inside the
-- write transaction, never here. ON DELETE SET NULL because a view outlives its author; NULL
-- then means admin-only editing.
--
-- The unique index normalises the way uq_users_email_ci does (LOWER + btrim of the six ASCII
-- whitespace bytes) and saved_views.service strips the same byte set in Python, so
-- "Q3 pipeline" and " q3 PIPELINE " are one name on both sides. btrim touches the ENDS only:
-- "Q3  pipeline" (two internal spaces) stays a distinct name, deliberately — collapsing
-- internal runs would be a second normalisation rule the users table does not have. The
-- index's leading column also serves the list-by-surface read, so no second index is created.
CREATE TABLE IF NOT EXISTS saved_views (
    id          SERIAL PRIMARY KEY,
    surface     TEXT NOT NULL CHECK (surface <> '' AND char_length(surface) <= 64),
    name        TEXT NOT NULL CHECK (btrim(name) <> '' AND char_length(name) <= 80),
    version     INTEGER NOT NULL CHECK (version >= 0),
    payload     JSONB NOT NULL,
    created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The payload size ceiling lives in saved_views.service._check_payload, NOT in a CHECK here:
-- a Python-side json.dumps and Postgres's payload::text serialise differently, so two limits
-- would disagree and a payload could pass the service only to trip an unhandled CHECK.
CREATE UNIQUE INDEX IF NOT EXISTS uq_saved_views_surface_name_ci
    ON saved_views (surface, LOWER(btrim(name, E' \t\n\r\f\x0b')));
