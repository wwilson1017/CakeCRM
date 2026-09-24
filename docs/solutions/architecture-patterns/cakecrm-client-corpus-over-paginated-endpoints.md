---
title: Moving a server-paginated list onto a client-side collection layer
date: 2026-08-26
category: architecture-patterns
module: frontend/src/crm, backend/crm/service.py
tags: [collection-layer, pagination, keyset, cache-coherence, react, postgres]
problem_type: pattern
---

## Context

CakeCRM's shared collection layer (#73) filters, searches and sorts an **in-memory array**; it has no server-search hook. Issue #77 adopted it on Contacts, Companies and Todos — three pages that until then used server-side `q=`, `status=`, `sort=` and `limit`/`offset` with infinite scroll.

That is not a UI rewrite. Applying a client-side facet to a server-paginated *slice* silently lies about what matched, so adopting the layer means moving the whole corpus into the browser — which changes pagination, cache coherence and failure handling all at once. This is the playbook, verified end-to-end on PR #107 (53 real-Postgres integration tests, 413 vitest tests, and a 12/12 independent smoke pass against the running app).

## Guidance

### 1. Sweep on an immutable, append-only key — and use a keyset, not OFFSET

Membership is not stable across a multi-second sweep: CakeCRM hard-deletes contacts and todos, and a todo also leaves `list_todos` when it is dropped or its deal is archived. Under OFFSET, one deletion behind the cursor shifts every later row back by one and a record is **skipped entirely**.

```python
_CONTACT_SORTS = {
    ...,
    # The only TOTAL, IMMUTABLE, APPEND-ONLY order here. A row inserted mid-sweep sorts
    # PAST the cursor instead of displacing rows behind it, and an updated row cannot move.
    "id": "ct.id ASC",
}
```

The blueprint's `created_at asc` did **not** transfer — both CakeCRM endpoints sort `created_at` DESC, which is not append-safe. Check the direction, not just the column.

Honor the cursor **only** with that sort, and refuse every other pairing rather than paginating wrong:

```python
def _check_assembly_cursor(after_id, sort, offset=0):
    if after_id is None:
        return
    if sort != "id":
        raise ValueError("after_id is only valid with sort='id'")
    if offset:                      # two competing ways to say where the window starts
        raise ValueError("after_id cannot be combined with a non-zero offset")
```

Silently ignoring the parameter is worse than refusing it — it looks exactly like a client stuck re-reading page one.

### 2. Derive `hasMore` from an extra row, never from a `total`

`list_contacts` runs its COUNT and its page SELECT in **two separate transactions** (`pg_fetchone` then `pg_fetchall`, each taking its own connection). An insert landing between them makes a `(page+1)*size < total` test report "done" and **truncate the corpus**.

Ask for `SIZE + 1` and test what came back. It keeps the question inside one query, and it is exact — an exactly-full page neither costs a needless round-trip nor, on the last allowed page, burns the assembly's `MAX_PAGES` budget.

Take the next cursor from the last **kept** row, not the probe row: the probe returns as the head of the next page rather than being skipped.

### 3. The cursor is the window, not a filter

The repo rule that a filter must reach the COUNT and the page query together exists so a total cannot disagree with the rows. A cursor is like OFFSET, which the COUNT has always ignored — so it must **not** reach the COUNT, and `total` stays the size of the whole matching set.

Skipping the COUNT on sweep pages is worth doing (up to `MAX_PAGES` scans of the whole filtered set, on the heaviest read path in the app, for a number with no consumer) — but key it on the **cursor**, not on `sort=id`. `sort=id` alone is legitimate offset pagination whose caller needs the total, and the sweep's own first page is indistinguishable from it. That costs exactly one COUNT per corpus load and no envelope field.

### 4. Patch writes from the server's response body, and make those responses list-shaped

There is no "current page" to refetch, and re-sweeping after every write blanks the table (the assembly surfaces no partial set). So fold writes in from the server's own response, keyed by id, in a map independent of the base array — a write landing mid-sweep then survives it.

Three rules the overlay needs:

- **Merge patches, not replacements.** CRM write responses are narrower than a list row (a derived `last_contact_at`, a joined `contact_name`); a replacement blanks exactly the columns the list renders. An explicit `null` must still overwrite — unlinking is a real edit.
- **A tombstone**, because CakeCRM hard-deletes where the blueprint archives.
- **`retry()` clears the overlay before re-sweeping.** Otherwise a patch written before the re-sweep merges back over the fresh rows: completing a repeating todo re-sweeps to pick up its spawned occurrence, and a surviving `completed: 0` from an earlier edit un-completes the original.

Two backend reads had to change to make this honest: `get_todo` gained the contact/deal joins (every todo write returns it, and a todo re-linked to another contact would otherwise keep the old name), and `get_contact_detail` carries the derived value so a detail reload propagates it.

### 5. Answer for every writer the page cannot see

This is the part that is easy to under-build, and it took three review stages to finish. Before the change, any keystroke round-tripped and picked up other writers incidentally. Afterwards nothing reloads on its own, while the assistant's CRM tools, Telegram capture, the Gmail touch scan, other seats and a second tab all keep writing.

- **Triage a failed write three ways.** A **404 means the row is gone** — drop it locally; it is the one 4xx that proves the write never happened *and* proves the cache wrong. Any other 4xx refused the write, so the cache is still right. Anything else may have committed with the response lost, so re-sweep.
- **Every mutation path reports an uncertain outcome**, not just the obvious ones. The detail pages host the EDIT forms; an activity row and a note are each one of the two signals behind the derived column. Wiring only the list-level create forms leaves the common paths unreconciled.
- **Bound the staleness.** An explicit Refresh control, plus a re-sweep when a backgrounded tab is brought forward and the corpus is older than a threshold. A write made *while the list is open* is still invisible until one of those fires — state that ceiling rather than implying freshness.

### 6. Keep the route as the selection

`contacts/:id?` is ONE route rendering the list page, which renders the detail when the segment is present. What matters is that the **element type** the router renders never changes: the assembly stays mounted, open → back does not re-sweep, and a cold deep link never sweeps at all (via the assembly's latching `enabled` gate). Two separate `<Route>`s both rendering the same component behave identically; what breaks it is routing `:id` at a *different* component.

This also settled whether to adopt the layer's modal detail: `DetailModal` is `z-50` and the assistant launcher is `z-40` (`DealDetailSheet` drops its own overlay to 39 precisely to stay under it), so a modal detail would cover the launcher for exactly the records that publish assistant context (#14). Todos, having no route to preserve and a detail that already covered the launcher, does use the shell.

### 7. Prove the SQL against a real database

The hermetic suite asserts SQL **string shape** and structurally cannot catch a syntax error in a LATERAL, a wrong `GREATEST`/NULL interaction, a placeholder bound in the wrong position, or a keyset window that skips rows. All of those pass every string test and fail on the first real request. Integration tests worth writing: the walk visits every row exactly once; a row inserted mid-sweep lands past the cursor; each derived signal in isolation; the exclusions; and the same value agreeing across list, search and detail.

## Why This Matters

Each of these was a real defect found by review on #77, not a hypothetical: the OFFSET skip, the COUNT/SELECT truncation race, the overlay resurrecting stale state after a re-sweep, the 404 ghost row, the un-reconciled activity write, and a `total` stripped from a caller who needed it. The pattern is cheap to get 80% right and the last 20% is where the lying happens — a list that looks authoritative while filtering data nobody can see is worse than one that is visibly loading.

## When to Apply

Any surface moving from server-side filtering onto `shared/collection`. The immediate next candidate is GTD `ProjectsPage`, deliberately deferred from #77. Note the ceiling: page size × the layer's `MAX_PAGES` (100,000 rows per entity here), past which the sweep fails loudly rather than showing wrong data; server-side search or virtualization is the upgrade, not a bigger cap.
