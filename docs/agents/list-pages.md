# List pages on the collection layer

> Topic doc split out of `AGENTS.md` (Product Rules). `AGENTS.md` keeps the enforceable
> invariants; this file is the full implementation record, moved verbatim. Add new
> implementation notes ("landed #N as …", design reasoning, divergences) HERE, not in
> `AGENTS.md`. Phrases like "the CRM bullet above" or "see the X bullet" refer to the
> matching Product Rules bullet — find its topic doc through the index at the top of
> `AGENTS.md`.

- **The three list pages run on the #73 collection layer** (#77 — Contacts, Companies,
  Todos; the pipeline board keeps #21's own bar). The layer filters an **in-memory** array
  and has no server-search hook, so a facet over a server-paginated slice would silently
  lie about what matched — the pages therefore sweep their whole corpus with
  `usePageAssembly` first, exactly as the blueprint does and as #21's then-unpaginated
  board already did. **Since #59 the board sweeps too** (`crm/pipelineAssembly.ts`), on
  this same wire format, which is what settled the fork issue #59 was told not to
  re-litigate: pagination is transport, facets stay client-side over the complete corpus,
  and there is no second filtering model. **The sweep is a KEYSET walk, never OFFSET**: `sort=id` is the only total,
  immutable, append-only order the endpoints offer (`created_at` is DESC on both, so a
  mid-sweep insert would displace the window), and `after_id` is honored ONLY with it —
  any other pairing raises rather than paginating wrong. `hasMore` comes from asking for
  one row more than a page holds, never from `total`: `list_contacts` runs its COUNT and
  its page SELECT in **two separate transactions**, so an insert between them would make a
  `total`-based test report "done" and truncate the corpus. The cursor is the *window*,
  not a filter — it deliberately does NOT reach the COUNT, exactly like OFFSET, so `total`
  stays the size of the whole matching set. Continuation pages skip the COUNT altogether
  (the sweep never reads it, and re-counting on every page would put a scan of the whole
  filtered set on the heaviest read path in the app); the skip is keyed on the CURSOR and
  not on `sort=id`, because the sweep's first page is indistinguishable from ordinary
  `?sort=id&offset=N` pagination, whose caller does need the total. Residual and stated rather than argued away: a
  row whose SERIAL id was allocated before the cursor passed it but which commits after is
  missed until the next sweep; the answer is each page's **Refresh** control, which is also
  what replaces the incidental reloading that server-side filtering used to give for free.
  Writes **patch from the server's own response body** through `crm/usePatchableAssembly.ts`
  — entries are **merge patches** (a response lacking a derived column must not blank it)
  plus a `remove(id)` tombstone (CakeCRM hard-deletes where the blueprint archives), and
  `retry()` **clears the overlay before re-sweeping** or a pre-retry patch merges back over
  the fresh rows. Two backend reads were made list-shaped to make that honest: `get_todo`
  gained the contact/deal joins (every todo write returns it), and `get_contact_detail`
  carries `last_contact_at`. A failed write is triaged on the status `ApiError` already
  carries (#55): a definite 4xx wrote nothing, anything else may have committed and been
  lost, so it re-sweeps — and a **404 is the one 4xx that proves the CORPUS wrong even
  though it proves the write never happened** (`rowIsGone`), because another seat, the
  assistant or Telegram deleted the row behind a swept list's back; that row is dropped
  rather than left to fail on every click. EVERY mutation path reports an uncertain
  outcome, not only the obvious ones — the detail pages host the EDIT forms, and an
  activity row or a note is one of the two signals behind `last_contact_at`. Because a
  swept corpus otherwise never reloads on its own (any keystroke used to round-trip and
  pick up other writers incidentally), returning to a backgrounded tab re-sweeps once the
  corpus is older than `CORPUS_MAX_AGE_MS`, alongside an explicit Refresh control. A write
  the assistant makes while the list is open is still invisible until one of those fires —
  the accepted ceiling, stated rather than hidden.
  The one write no patch can express is completing a **repeating**
  todo, which spawns its next occurrence server-side (#70) — that path re-sweeps, decided
  from the SERVER's copy of `repeat`, never the pre-write one.
  **Last contact** has no column behind it: it is derived per contact from the same two
  signals `analytics_service.get_contact_staleness` reads (activity_log + un-archived,
  non-housekeeping chatter) under the same alias, via an index-driven `LATERAL` — NOT
  `get_pipeline`'s grouped subquery, which aggregates the whole table once per query and
  can afford to only because it runs once for a whole-corpus read. That rule is now
  written into `get_pipeline` itself: #59 gave it a LATERAL twin
  (`_PIPELINE_LAST_ACTIVITY_LATERAL`) used by the keyset page, while the unbounded board
  and the tool's window keep the grouped form. Edit one twin, edit the other — an
  integration test pins that they return the same `last_activity_at`. `NotesThread` gained an
  optional `onChanged` so a note added on the detail page reaches the list's column.
  Both surfaces now exclude `provenance_service.confirm`'s housekeeping notes via
  `scoring_service.HOUSEKEEPING_NOTE_LIKE` (public since #77) — the assistant confirming an
  AI-populated field was silently resetting a contact's staleness clock.
  **Contacts and Companies keep ROUTED detail pages**, which is a deliberate refusal of the
  issue's "detail in the `CollectionDetail` shell" — though only one of its two reasons still
  holds. The Z-ORDER objection does NOT: `DetailModal` was `z-50` against a `z-40` launcher, but
  #75 gave it an opt-in `underLauncher` (`z-50 dock:z-[39]`) that `CollectionDetail` passes
  unconditionally, so a modal detail now ducks under the launcher rather than covering the very
  records that publish assistant context (#14). What survives is the SHAPE: the shell is
  `max-w-2xl` where these are full-width working surfaces, and four other surfaces deep-link to
  `/crm/contacts/:id`, which a modal cannot be. Instead **the route is the selection**: `contacts/:id?` is ONE route
  rendering the list page, which renders the detail when the segment is present. What
  matters is that the element TYPE never changes, so the assembly stays mounted and
  open → back does not re-sweep (pinned by `ContactsPage.test.tsx`, verified to fail
  against the pre-#77 two-component shape). The cost is the shell's ‹ › record navigation.
  **Todos DOES use `CollectionDetail`** — it has no route to preserve and its detail was
  already a modal over the launcher. Its "Open / Done / All" control is a **custom** facet,
  because only `CustomFacetDef` carries a `defaultValue` and it must default to an ACTIVE
  state to reproduce the old Pending-by-default page; a layer *toggle* would have been
  wrong twice (the hook never filters rows on toggles, and two states cannot express the
  Done-only tab that existed). Its due buckets use the LOCAL day via `ymd`, fixing a UTC
  drift that made an evening "due tomorrow" read as "due today" — and the day itself is
  `useLocalDay`, **state that advances on a timer aimed at local midnight**, fed into the
  configs so the dependency is real. Reading the clock inside a predicate is necessary but
  NOT sufficient: a predicate only runs when React re-renders, and time passing is not a
  render, so a tab left open overnight would keep yesterday's boundaries and stop flagging
  anything overdue. Its `now` is derived FROM the day string (at local noon, clear of both
  DST edges) so the two cannot disagree, and it re-arms on a monotonic tick rather than the
  day value, because `setState(sameValue)` is a React bail-out that would strand a clock
  stepped backwards. Todos' default sort is a **composite** `open_due` key, since the layer
  sorts by one value per field and "All" would otherwise interleave done and open todos the
  way the old tab bar never did. `OwnerScopeToggle` is
  **deleted** — an Owner facet with an Unassigned bucket replaces it and can select any
  owner, hiding itself on a single-seat install the same way.
