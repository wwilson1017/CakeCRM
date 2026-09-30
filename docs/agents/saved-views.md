# Custom date ranges and saved views

> Topic doc split out of `AGENTS.md` (former Product Rules). `AGENTS.md` keeps the enforceable
> invariants; this file is the full implementation record, moved verbatim. Add new
> implementation notes ("landed #N as …", design reasoning, divergences) HERE, not in
> `AGENTS.md`. Phrases like "the CRM bullet above" or "see the X bullet" refer to the
> rule bullets this file opens with, or to the topic doc that holds that rule; find
> other areas' docs through the Topic docs table in `AGENTS.md`.

- **Custom date ranges + team-visible saved views** (#181) are both built in
  `shared/collection`, so every surface the layer serves gets them with no per-page wiring
  and `PipelinePage.tsx` is not touched. **`dateRangeFacet`** (`shared/collection/
  dateRangeFacet.tsx` + the pure `dateRange.ts`) is a FACTORY over the existing `custom`
  kind, not a sixth facet kind — the `custom` arm already carries the whole value lifecycle,
  so `types.ts`'s union, the four switches in `facets.ts` and the render switch in
  `CollectionView.tsx` are untouched and each consumer supplies only `getDay`. Bounds are
  viewer-local `YYYY-MM-DD` compared lexicographically, so a range needs no clock at all;
  `coerce` rejects an impossible calendar day (`2026-02-31`) as well as a malformed one. The
  pipeline declares three ranges BESIDE its presets (`closeDateRange`/`createdDateRange`/
  `lastActivityRange`) plus a new `createdDate` preset facet (`last7`/`last30`/`thisMonth`/
  `thisQuarter`/`lastQuarter`, riding `dealMatchesAdvanced` like the other two) — beside, not
  merged, because the layer already ANDs across active facets, so every #21 preset predicate
  stays byte-identical and `crm_pipeline` stays at storage **version 1** (adding a facet is
  not a persisted-shape change; `coerceSelections` defaults a key the envelope lacks).
  **Saved views** are the repo's FIRST server-side preference store — one table
  (`saved_views`) behind one REST surface (`/api/saved-views`) in its own `backend/
  saved_views/` package, because a view belongs to a *surface*, not a CRM record. It is
  deliberately outside BOTH branches of `crm.service._truncate_all` — one step further than
  `crm_field_definitions`, which survives demo-clear but is wiped by `clear_all`. The two
  differ in what they reference: a field definition describes the SHAPE of CRM data, so
  surviving a full reset would leave schema describing rows that no longer exist, whereas a
  saved view describes a QUERY over stage keys, date bounds, user ids and search text, none of
  which a CRM reset erases (it never touches `users`). A view stays valid afterwards, so
  losing every teammate's views to reseed demo data would be the surprising outcome. It holds
  no FK to anything the reset sweeps, and `MAX_VIEWS_PER_SURFACE` bounds a table nothing else
  ever deletes from. It is
  also the repo's FIRST **per-row** authorization rule — every member reads and applies every
  view; only the creator or an admin may rename, overwrite or delete one — decided in the
  service inside the write transaction after a `SELECT … FOR UPDATE` pre-read, returning 403
  for the same reason `require_admin` does. Every mutator reads its row back in the SAME
  transaction that wrote it. `can_edit` and `created_by_name` are computed server-side, so
  the shared layer needs no auth context (`useAuth` throws outside `AuthProvider` and would
  break every existing `CollectionView` test). A view captures **facets, search text, sort
  and view mode** — never toggles, because column visibility is the per-device personal
  preference #124 established and a shared view must not rearrange a teammate's board — and
  is stamped with `CollectionStorage.version`; a view carrying another version is listed
  **disabled with a reason**, never hidden (nobody could repair it) and never applied (a
  silently-empty filter is what the stamp prevents). `CollectionState.applySnapshot` is the
  layer's one whole-state setter and shares `coerceEnvelope` with the sessionStorage restore
  path, so a payload off the wire lands on defaults instead of throwing. The menu fetches on
  OPEN, never on mount, and renders in the toolbar for every surface — including the
  empty-items branch, where a stale view may still need repairing or deleting.
