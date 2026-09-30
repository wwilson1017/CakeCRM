# CRM core: records, deals, pipeline, chatter, theme

> Topic doc split out of `AGENTS.md` (former Product Rules). `AGENTS.md` keeps the enforceable
> invariants; this file is the full implementation record, moved verbatim. Add new
> implementation notes ("landed #N as …", design reasoning, divergences) HERE, not in
> `AGENTS.md`. Phrases like "the CRM bullet above" or "see the X bullet" refer to the
> rule bullets this file opens with, or to the topic doc that holds that rule; find
> other areas' docs through the Topic docs table in `AGENTS.md`.

- **The CRM is first-class core** (`backend/crm/`, mounted at `/api/crm`; frontend
  `frontend/src/crm/` + `frontend/src/shared/`) — always-on, no enable flag. Ported
  from chatty's `crm_lite` and translated to Postgres (companies/contacts/deals/todos/
  activity_log, plus `crm_chatter` — editable/archivable notes threads on deals and
  contacts, landed #15; query idioms follow the matching `cake_os/backend/apps/crm`
  services so later feature ports diff cleanly). Companies are a first-class entity (#13):
  contacts/deals carry a nullable `company_id` FK and a company detail page rolls up
  the linked contacts/deals/activity. **Company link coherence** (#35) makes the *link*
  the single source of truth: every ingestion path (CSV import, smart-import confirm,
  `crm_create_contact`/`crm_update_contact`, the REST routes) resolves free-text company
  names and auto-creates the company through one shared primitive,
  `crm.service.resolve_or_create_company_ids()` — 2 queries per batch regardless of row count
  (the bulk loops pre-resolve and pass `company_id`, and degrade to per-row resolution
  if the batch call fails, preserving per-row error isolation); it is also the helper
  the #61 importer consumes. An explicit `company_id` always wins — including an
  explicit `null` on *update*, which means unlink (on *create* there is nothing to
  unlink and the REST route can't express absent-vs-null, so text always links there).
  Contact list/search LEFT JOIN `companies` and expose `company_name`; the UI renders
  `company_name || company` and search matches the joined name, so a linked contact is
  findable/displayable even with empty or stale legacy text. The legacy
  `contacts.company` column stays in place, non-authoritative (**the freetext↔link merge
  landed #126 in `ContactForm`; the COLUMN stays**); a second one-shot backfill migration
  (`20260816203810_company_link_backfill.sql`) repairs installs that imported between
  #13 and #35 by linking contacts that are still `company_id IS NULL` with matching
  text. Unlike #13's it deliberately does **not** inherit company onto deals: `NULL`
  no longer unambiguously means "never set" (a user can clear a deal's company), and
  no ingestion path creates deals anyway.
  **Inline quick-create** (#123) makes the deal form the first surface that can create a
  linked record without leaving it: `frontend/src/crm/components/RecordCombobox.tsx` is a
  generic server-searching picker with a `Create "<name>"…` row, and `DealForm` uses two of
  them for Contact and Company. It **replaces** the "fetch the first 200 rows into a
  `<select>`" pattern, and with it the capped-page hazard each form used to hand-patch: an
  out-of-page link had no `<option>`, so the control rendered blank and read as *none* —
  `DealForm` carried an append guard for the deal's own company and never had one for its
  contact. The selected record's LABEL is now a prop seeded from `deal.contact_name` /
  `deal.company_name`, so the display is correct by construction and both guards are gone.
  **That makes the joined name a wire-format requirement, not a nicety:** any query whose
  rows can reach this form must carry BOTH names or the picker renders empty for a link that
  exists, reproducing the bug it replaced. `get_deal` and `get_pipeline` already did;
  `get_dashboard_stats`' `top_deals` did **not**, and the dashboard hands its rows straight
  to the deal sheet and on to `DealForm` — so #123 added the `companies` join there and
  `test_top_deals_joins_the_company_name` pins it. The picker is deliberately NOT built on
  `shared/search`: `SearchInput` has no listbox, and that module documents itself (in
  `shared/search/index.ts`) as client-side-only over an already-loaded dataset, explicitly
  disclaiming server-paginated contacts and companies. **Three corrections to that issue's own pointers, each verified:**
  the search param is **`q`**, not the `?search=` it names (which `list_contacts` ignores, so
  building against it ships a picker that always shows the unfiltered first page); `DealCreate`
  has no free-text `company` field the way `ContactCreate` does, so a typed company must be
  resolved to an id before the deal is saved; and `POST /companies` does **not** go through
  the #35 resolver — it INSERTs unconditionally and surfaces a `uq_companies_name_ci`
  collision as a 400. That last one is why **`POST /api/crm/companies/resolve`** exists: it
  delegates to `resolve_or_create_company_ids` verbatim, so quick-create is get-or-create
  (race-safe by construction, one normalizer owned by SQL) while the full New Company form
  keeps its honest "already exists" error. Contact quick-create needs no new endpoint —
  `POST /contacts` with a name already works. **The ownership split is deliberate**: the
  contact is created with the name ONLY, so `_create_payload` assigns the caller, while the
  company rides the resolver and is left unassigned — the rule
  `test_auto_created_companies_are_left_unassigned` pins by reading that function's source.
  The trimmed query is the single form used for BOTH searching and creating, which is what
  closes the leading/trailing-whitespace duplicate hole for free. Reusable by design for
  **#126** (ContactForm's company field), which landed on it unchanged — see the paragraph
  below.
  Three rules inside the picker are subtle enough to state, because each was a bug first:
  **closing is not cancelling** — an outside click (the form's own Save button is one)
  closes the popover but does NOT abandon an in-flight quick-create, since the record is
  written either way, and `onBusyChange` lets `DealForm` refuse to submit underneath one
  rather than saving a deal without the link that is about to exist; **Enter is swallowed
  but does not select** until the user has typed or arrowed, because the list opens on focus
  and there is no "nothing highlighted" state, so a habitual Enter in an already-linked
  field would otherwise replace the link with whatever sorted first; and **display text and
  match text are different things** (`getMatchText`), because the company picker decorates
  an archived row `"Acme (archived)"` and matching on that would offer to create a duplicate
  of the row directly above. The contact side's exact-match dedupe only sees the 20-row
  page — companies are immune, resolving server-side — which is documented at
  `PICKER_LIMIT` as an accepted single-install trade.
  **#126 is the second surface**, and it is the freetext↔link merge the #35 bullet deferred:
  `ContactForm`'s free-text `Company` input and its capped `Linked Company` `<select>` become
  ONE `RecordCombobox`, consumed unchanged. So the form no longer has a way to record a
  company name without a company row behind it — typing an unmatched name offers
  `Create "…"`, which resolves to a real deduplicated row. The `contacts.company` COLUMN is
  untouched and still non-authoritative; the form keeps writing it in step with the link so
  the two cannot contradict. The one rule worth stating, because nothing on screen shows it:
  **an untouched company field omits BOTH `company` and `company_id` from a `PUT`.** The
  update route reads its body with `model_dump(exclude_unset=True)`, so an omitted key is not
  written at all — and that is the only thing standing between a pre-#35 contact holding
  unmatched free text and the silent erasure of the only record of that name, by someone who
  opened the form to fix a phone number. Sending `company: ''` would do it. Touching the
  field (pick, create, or the × clear) sends both keys; a CREATE always sends both, having no
  prior value to protect and no way to express absence (`POST /contacts` dumps without
  `exclude_unset`). Such a contact renders its free text as the picker's empty label plus a
  *not linked* hint, which both disappear the moment the user speaks for the field — left up,
  the label would keep naming a company they had just cleared. **Divergence from the
  blueprint, deliberate:** cake_os's `QuickAddModal` (#2016/#2049) defers the company create
  to submit so an abandoned form leaves nothing behind; CakeCRM creates on row press, because
  that is `RecordCombobox`'s shipped contract and the issue mandates one component for both
  surfaces. The cost is bounded — quick-create goes through the `/resolve` get-or-create, so
  an abandoned form leaves at most one unowned company and a retry reuses it.
  Two consequences of reusing the component unchanged are **accepted, not overlooked**.
  (1) The picker's × is gated on a non-null value and `companyTouched` is set only by
  choosing or clearing, so an unlinked contact would have had NO way to delete a wrong
  legacy name without first linking some company to it — `ContactForm` therefore renders its
  own inline **Remove** action on the not-linked hint. (2) Typing a name and pressing Save
  **discards it**: the widget keeps its query private and reports only choose/create/clear,
  where the removed free-text input committed on Save. `Create "…"` is the commit
  affordance. That is `RecordCombobox`'s contract and has been true of `DealForm`'s two
  pickers since #123, so it is a property of the component, not a regression this issue
  introduced; both are pinned by tests in `ContactForm.test.tsx`. The one capability
  genuinely retired is editing unlinked free text to a DIFFERENT arbitrary string without
  linking — which is the merge the #35 bullet deferred, not a side effect of it.
  User-defined **custom fields** (#19) add a
  two-table EAV (`crm_field_definitions` + `crm_field_values`) on contacts/companies/
  deals, managed in `/crm/settings`, rendered in the entity forms and detail pages, and
  exposed to the assistant via `crm_{get,set}_{contact,company,deal}_fields`;
  `crm_field_values` is polymorphic (no entity FK), so it is cleaned at every
  entity-delete + `_truncate_all` site (definitions survive demo-clear, wiped only by
  `clear_all`), and `is_required` is advisory-only (never enforced server-side).
  **Deal lifecycle + sales intelligence** (#22 Phase 1) add `deals.lost_reason` and a
  soft-archive `deals.archived_at` (NULL = live) plus an append-only `deal_stage_events`
  log. Every deal column update funnels through `service._write_deal_update`
  (`create_deal` and `archive_deal` are the two writes that don't — neither has an old
  stage to transition from), which in ONE transaction takes `SELECT stage … FOR UPDATE`, writes the row, appends a stage event
  when the stage moved, and CLEARS `lost_reason` when a deal leaves `lost` (the bug the
  blueprint fixed after our snapshot). `archived_at` is a **sweep**: `LIVE_PREDICATE`
  is carried by every deal-reading query (pipeline, dashboard, analytics, list/search,
  contact/company rollups, touch-count backfill, the Gmail-scan open-deal attribution),
  and `crm/analytics_service.py` IMPORTS those predicate constants rather than re-typing
  them. The line the sweep draws is **work items follow the deal, history does not**: an
  archived deal's open todos drop out of `list_todos` (archiving is the user's "stop
  nagging me" gesture, and the heartbeat is told to read that list), while `activity_log`
  is never filtered — it records what actually happened, and you need it to decide
  whether to restore. Deliberate exceptions: `get_deal` (fetch-by-id must still resolve
  an archived deal so it can be shown/restored/merged), the is-the-CRM-empty counts, and
  the **two opt-in holes** that keep an accidental archive or a wrong merge recoverable —
  `crm_search_deals(include_archived=true)` (the assistant's route back, needs a provider)
  and, since **#83**, `get_pipeline(include_archived=true)` (the keyless one). A stage
  change on an archived deal is refused outright: won + archived would book revenue no
  report can see.
  **#83's hole is exactly one query wide, and the asymmetry is the rule, not a lapse.**
  The flag opens `get_pipeline`'s *deals* query only; `stage_summary` keeps
  `LIVE_PREDICATE` unconditionally. That looks like precisely the one-sided filter the
  function's own no-`owner_id` comment forbids, and the difference is that owner is a
  symmetric facet (cards and totals must describe the same set or the page lies) while
  archived is not: an archived deal must be **findable** or it is unrecoverable, and must
  never be **money**. The client mirrors the same split — `liveVisibleItems` feeds every
  $ aggregate, the open-pipeline header, the select-all ids and the bulk payload, while the
  cards render from `state.kanbanItems` and do not — so cards and totals still agree about
  value. Reaching it: an **Archived facet** (`'include' | 'only'`, default null = live only).
  Since #74 it is a plain `single` `FacetDef` on `pipelineCollection.ts` and its value lives
  in the layer's `collection_crm_pipeline_v1` envelope, so the layer counts it, **Clear
  filters** resets it, and #83's hand-rolled coercion is gone — the facet's predicate fails
  an unrecognised value toward LIVE-ONLY rather than toward a wider board. It is the ONE
  facet that also widens the FETCH, because a client predicate cannot filter rows the server
  never sent; `PipelinePage.load` keys the query param off a derived boolean read from
  `state.facetSelections.archived`, so an unrelated facet change never refetches — and a
  second `loadGen` ref (distinct from `writeGen`, which guards against racing *writes*)
  discards a superseded load, since two quick facet flips can otherwise resolve out of order.
  A failed non-silent load now toasts: with `data` already populated a swallowed failure
  renders the previous payload, which under `'only'` is an empty board indistinguishable
  from "no archived deals".
  **Two things the collection layer cannot express, and where they went instead (#74).**
  (1) The layer skips an INACTIVE facet's predicate, so the resting "hide archived" state is
  not enforceable as a facet at all — it is the server's `LIVE_PREDICATE`, which is exactly
  why the facet widens the fetch. The gap that leaves is a live-only refetch that never
  lands: deferred behind an in-flight write, or failed outright, with archived rows still in
  `data` and the facet already off. `load` closes it by pruning them from `data` on both
  paths (`pruneArchivedFromBoard`), which is where #83's null predicate branch went. (2) An
  empty `items` makes the layer render its own empty state INSTEAD OF its toolbar, so the
  facet would be unreachable exactly when it matters — archive your last open deal and the
  recovery view is behind a control that is no longer on screen. A **Show archived deals /
  Show live deals** link therefore sits in the page header beside "Show all", for the same
  reason and above `CollectionView` for the same reason.
  Archived cards render **inert** — dimmed with an ARCHIVED chip, no bulk checkbox, never
  selected, excluded from select-all, and genuinely un-draggable via `shared/dnd`'s
  `dragDisabled`, widened from `boolean` to `boolean | ((item) => boolean)`. That widening
  needs the two named helpers in `shared/dnd/dragDisabled.ts` rather than an inline check,
  because the prop now answers two different questions and conflating them is silent: a
  **function is truthy**, so `KanbanBoard`'s old `!dragDisabled` overlay test would have
  unmounted the `DragOverlay` for *every* card the moment any per-item policy was supplied —
  live cards would drag with nothing following the pointer. `boardDragDisabled` (=== true)
  gates the overlay, `resolveDragDisabled` answers per card, and a test pins that a
  predicate is board-*enabled*. Since #74 the board reaches `shared/dnd` through the
  collection layer, so `CollectionKanbanProps.dragDisabled` carries the SAME union and
  `KanbanView` unwraps the `{id, item}` wrapper before calling it — a naive pass-through
  compiles, throws nothing, and simply reads `undefined` off the wrapper, answering
  "draggable" for every card. There the layer's own `dragLocked` is checked FIRST and
  collapses to a literal `true` rather than being OR-ed in, for the truthiness reason above.
  **The LIST view owes that same ceiling and did not pay it until #74's settle**: it is the
  shared layer's own table, not the page's markup, so the board's `selectable={!archived}`
  had no counterpart there and every archived row rendered a checkbox. Fixed with
  `CollectionSelectionProps.isSelectable` — id-keyed, so the interface stays non-generic and
  every consumer keeps passing `selection` as an inline literal — which withholds the
  checkbox ENTIRELY on an ineligible row (not a disabled one, not an unchecked one) and
  narrows select-all to the selectable rows. Both halves were live bugs, and the second is
  the one a reviewer misses: the page prunes archived ids out of `selectedIds` so the bulk
  count and the bulk payload describe one set, which made a row checkbox a control that
  stored an id on every click and never ticked — and with one archived row on screen "every
  visible row is selected" is unreachable, so the header box could neither tick nor, its
  clear branch being gated on that same flag, clear. The payload was never at risk
  (`applyBulkMove` recomputes from `liveVisibleItems`); what was broken was two dead
  controls. Same shape of rule as `dragDisabled`, for the same reason — one class of row is
  inert while the surface around it stays live.
  `POST /api/crm/deals/{id}/restore` (member-accessible, sync
  `def`, calls the already-bidirectional `archive_deal(archived=False)` — no new service
  logic) backs the deal sheet's archived banner; the banner reads the **re-fetched**
  `archived_at`, not the frozen list row, since the assistant can archive a deal between the
  board's load and the sheet opening. Restore hands the returned row UP (`onRestored`) for
  the board to patch in place, which is why the route returns the deal rather than
  `{"ok": true}`: a silent refetch can fail invisibly and leave the board still showing a
  deal as archived after a restore that happened. The board is also refreshed after the patch
  (a restore closes the sheet the way `onClose` does, which refreshes so an in-sheet note
  reaches `last_activity_at`) — the patch is what makes the board *correct*, the refresh only
  makes it *fresher*. `CrmDashboardPage` wires `onRestored` too: the banner renders on any
  host, since it reads the sheet's own re-fetched `archived_at`. Mark Won/Lost hide on an
  archived deal, and `DealForm` both disables its stage `<select>` **and omits `stage` from
  the PUT** — the server refuses the stage change and rejects the **whole** update, so an
  editable control would discard every other field the user just typed, and a *disabled* one
  still would if the deal had moved stage elsewhere since the board loaded: a disabled
  control's value is not user intent, so the field is not sent at all. `include_archived` is **refused (400), not ignored**, alongside
  `stage`/`contact_id`: that branch is `list_deals`, which keeps the sweep, and honoring it
  there would be a second hole. Scope ceiling: view + restore only — no archive route, no
  merge UI, no archived-deals page. Note restoring a **merge source** is not an undo: the
  merge already repointed activity/todos, copied notes and gap-filled custom fields onto the
  target; restore only makes the source visible again.
  **The desktop board is bounded to the window** (#129) and is therefore ONE scroll region on
  both axes: `shared/dnd/useBoardScroller` writes its height so the bottom edge lands on the
  bottom of the visible area, the per-column `max-h-[70vh]` scrollport is gone (desktop only —
  mobile keeps it, along with its swipe and chip bar), stage headers are `sticky top-0` over the
  opaque page ground, and `items-start` is what gives that sticky any travel. The height is
  MEASURED rather than expressed in CSS, and that is forced rather than chosen: `index.css` sets
  only `#root { min-height: 100svh }`, so `CrmLayout`'s `height: '100%'` resolves to `auto`, its
  `overflow: 'auto'` never engages and the DOCUMENT scrolls — verified in a browser. Giving the
  shell a real height chain would change the scroll model of every CRM page at once, which is a
  bigger change than a board fix. Two consequences worth knowing: the board's columns carry
  `LAUNCHER_CLEARANCE_PX`, because a bounded board cannot scroll the page out from under the fixed
  assistant pill the way every other page does; and a SHORT column's sticky header unpins once its
  own cards have scrolled by, since a sticky element cannot leave its containing block and
  `items-start` makes that block the column's own content. **This is also the consumer
  `collision.ts` warned about** — a board with a vertical fold — so its widening now declines
  explicitly (`boardBand` returns `null`) and lanes are clipped to the board box on that path, in
  BOTH the pointer map and the `closestCorners` fallback.
  **The board's FIRST load waits for the tab** (#129): a route can be opened into a background
  tab, and that load is a keyset sweep of every deal, so it is deferred until the first
  `visibilitychange → visible`. Returning to the tab also re-sweeps a corpus older than
  `CORPUS_MAX_AGE_MS`, which is the list pages' `useCrmCorpus` idiom extended to the one swept
  corpus that had no staleness bound at all — same constant, same mechanism, silent so it cannot
  wipe an open sheet. Deliberately NOT gated: the refetches that settle a write
  (`applyBulkMove`'s reconcile, `replayDeferredLoad`). They can run while hidden, and they must —
  `applyBulkMove` holds a lock that disables drag and refresh until its reconcile lands, so
  gating it would wedge the board behind that lock until the user came back.
  **A Won card shows last contact instead of the open-deal nudges** (#129): the lead score asks
  "is this still alive" and the touch count asks "are we working it enough to close", and neither
  question survives the close — post-sale the board is read for which accounts have gone quiet.
  `pipelineBoard.lastContactLabel` renders `last_activity_at` (#21's derivation, no new query),
  keyed off the COLUMN so a card mid-drop reads right, and says "No contact logged" outright
  rather than rendering an empty slot.
  **The desktop board adapts to how focused the view is** (#182):
  `pipelineBoard.boardColumnLayout(visibleStageCount)` maps the number of stage columns the
  board is actually RENDERING onto a `BoardDensity` — `compact` at 5+, `roomy` at 3-4, `wide`
  at 1-2 — and the column's fixed 288px becomes `flex: 1` inside that tier's min/max band.
  Width and card density are ONE behaviour, not two that correlate: the tier raises the
  ceiling (360/440/560) because it is also adding fields, and a tier that added fields
  without room would only make the card taller.
  **Keyed off the COUNT, never a measured pixel width**, so two reps who have narrowed the
  board the same way see the same card whatever their monitor, and so the boundaries stay
  pure and unit-testable with no DOM. All three ways of narrowing arrive already resolved,
  because `columns` is built from `visibleStageKeys` — #124's durable default, the per-tab
  hide, and the stage facet alike.
  **The floor is 288px at EVERY tier** — the old fixed width — so no tier is a narrowing, the
  default 5-and-6-column board lays out byte-identically, and `min-width` still stops the
  shrink so #129's single scroll region takes over sideways once the floors overflow
  (measured: 288/366/560px and h-scroll at 6/4/2 visible stages on a 1600px window).
  **Each wider tier only ADDS**: `roomy` renders the close date unconditionally (an absent one
  becomes "No close date" — #128's rule that a missing value is a state worth reading) and
  promotes the last-contact line onto OPEN cards; `wide` adds the owner via `useUsers`'
  `nameFor`, the resolver the list view already uses, so the two surfaces cannot name one
  owner differently. Nothing is ever taken away as the board narrows.
  That promotion is **not** a walk-back of #129: its trade is about a CLOSED deal, where the
  two nudges answer questions the close retired, so a Won card still swaps them out at every
  tier — the open card merely gains the line beside them. Both render ONE shared expression,
  so the label, the tooltip and the no-activity wording cannot drift.
  Two smaller rules. The two `renderColumn` branches are written as WHOLE objects rather than
  per-property ternaries, so no render carries the `flex` shorthand beside a conflicting
  `flexShrink`/`width` longhand (the warning `DealBoardCard`'s border already documents), and
  mobile's `85vw` snap column is untouched by construction. And the card title carries
  `flex: 1; minWidth: 0`: its row is `space-between` over up to four children, so a short
  title on a 560px card drifted into the middle until the title owned the slack — invisible
  at 288px, which is why it survived until a column could grow.
  **The card carries an explicit `aria-label` and must keep one** (#176). It is a
  `role="button"` div, so with no label its accessible name is computed from its own
  contents — which begin with the bulk-select checkbox's `Select <title>`, making the
  control that OPENS a deal announce as "Select Acme renewal, Acme renewal, $600 …": the
  wrong verb, the title twice, and the money in the name. The label is
  `Open <title>` / `Open <title> (archived)`, so the ARCHIVED chip — the one thing in the
  subtree worth hearing — survives while the metadata row does not. **The general rule for
  this repo:** a container given an interactive `role` that also holds a labelled control
  needs a name of its own, and the test for it asserts the ACCESSIBLE NAME (#162's
  convention), never `textContent` — which here can see neither the bug nor the fix, both
  being attributes, so a `textContent` assertion is green in both directions.
  `deal_stage_events` is the one CRM table with a real FK to `deals`, so it MUST stay in
  every `TRUNCATE` sweep or the CRM reset errors out. `merge_deals` repoints
  activity/todos, copies notes with a `[Merged from deal #N]` marker, gap-fills custom
  fields (the target's own values always win), and archives — never deletes — the
  source. **Bulk stage moves** (#55) run through `service.bulk_move_deals` in ONE
  transaction, capped at `BULK_MOVE_MAX` (200 — the cap bounds both the lock window and
  the post-commit rescore, since CakeCRM sends one unchunked request): rows are locked
  `ORDER BY id ... FOR UPDATE` (`merge_deals`' deadlock rule), then flushed as one
  `UPDATE` per distinct field map plus ONE multi-row `unnest` INSERT into
  `deal_stage_events` **on the same cursor**, so "deals moved but the history is
  missing" is unreachable. The stage-change RULES live in exactly one place —
  `_classify_deal_update`, a pure helper shared with `_write_deal_update` — so the
  single-deal and set-based paths cannot drift about WHAT to write (an integration test
  pins twin deals moved through each path to identical rows *and* identical stage
  events). The classifier deliberately does not decide WHETHER to write; each path decides
  that for itself, and **since #96 they agree**. Bulk skips a same-stage deal in Python on
  its locked pre-image; `_write_deal_update` instead carries an `IS DISTINCT FROM` test
  over exactly the columns it is about to SET, so a write that changes nothing matches no
  row and `updated_at` never moves. **The decision is made in SQL, not Python, and that is
  load-bearing**: the assistant's tool arguments are not runtime schema-validated, and
  `1.0 == True` is True in Python where Postgres correctly refuses a boolean into a
  `DOUBLE PRECISION` column — so a Python pre-image comparison would turn invalid writes
  into silent no-ops, and would mishandle NULL (an unlinked `contact_id`) besides. Bulk can
  stay in Python because it writes exactly one caller-controlled column, a `DEAL_STAGES`
  string validated before the connection opens. Postgres coerces on **assignment** but
  promotes on **comparison**, so the distinctness test casts the parameter to the column's
  destination type, declared once in `_DEAL_COLUMN_TYPES`. The two contexts disagree in
  OPPOSITE directions, so the cast is not optional: INTEGER promotes on comparison
  (`probability=40.1` stores 40 unchanged, but an uncast `IS DISTINCT FROM 40.1` calls it
  distinct and bumps `updated_at` anyway), while TEXT accepts an I/O conversion on
  assignment and has NO comparison operator (`title = 12345` has always stored `'12345'`,
  but an uncast comparison raises `operator does not exist: text = integer` — live, since
  `crm_update_deal` forwards raw unvalidated LLM arguments). Only the comparison is cast,
  so assignment behavior and its type errors are untouched. **`_DEAL_COLUMN_TYPES` is
  deliberately NOT the allowlist**: it covers internal-only columns (`lost_reason`) and grows
  whenever a new internal write path routes through the chokepoint, so deriving
  `update_deal`'s allowlist from it would be default-OPEN — declaring a type for an internal
  column would silently make it writable by `crm_update_deal`'s raw model kwargs and
  `PUT /api/crm/deals/{id}` in the same commit. The boundary is the hand-maintained,
  default-CLOSED `_DEAL_USER_WRITABLE` (no `lead_score`, no `archived_at`), and a **hermetic**
  test asserts only the safe direction, `_DEAL_USER_WRITABLE ⊆ _DEAL_COLUMN_TYPES` — so
  "no writable column without a declared type" still holds, in the suite CI actually runs.
  An integration test pins each declared type against `information_schema`. The `deal_stage_events` INSERT is
  gated on the UPDATE's rowcount as well (a real stage change always differs, so this is
  structural rather than reachable). Two consequences are accepted rather than incidental: a
  **custom-field-only save no longer bumps `deals.updated_at`** (`DealForm` always PUTs the
  standard fields and then writes changed custom fields separately, and `set_field_values`
  never touches the parent row — so that bump was a side effect of an unchanged-form PUT,
  and the detail page's `CustomFieldsSection` never produced one at all), and a no-op save
  no longer floats a deal up an `updated_at DESC` ordering — including `crm_get_pipeline`'s
  first-25-per-stage window.
  The custom-field one **resolves an inconsistency by picking uniformity, not by picking
  the more accurate answer**, and that is worth stating plainly: a user who edits only a
  custom field has done real work on that deal, and nothing in `LAST_TOUCH_SQL` now records
  it, so the deal keeps getting nudged until someone logs a note. It was arbitrary before
  (bumped from the edit modal, not from the detail page) and is consistently
  **not-a-touch** now. Taking the other branch belongs in `set_field_values`, which already
  holds the entity row `FOR UPDATE` — but it is a product call about what a "touch" means
  across contacts and companies too, and it needs its own change detection first: both UIs
  send only changed values, while the `crm_set_*_fields` tools can send unchanged ones, so
  a naive bump there would reopen exactly this bug against `crm_field_values`.
  **Deliberate divergences between the two paths**, each with its own reason: *where* the
  no-op is decided (SQL vs Python, above); the **error contract** — `_write_deal_update`
  raises, bulk isolates per deal (missing/archived deals report in `errors` while the rest
  still commit), because one archived deal must not sink a 50-deal selection; the
  post-commit **rescore**, which the single-deal path runs unconditionally while bulk
  rescores only `updated_ids` (correct rather than an oversight — `score_on_event` is
  swallowed on failure and `_maybe_refresh_scores` excludes terminal deals that already
  carry a score, so re-calling `mark_deal_won` is the only repair route for a won deal
  whose rescore failed); and **provenance**, where `crm_update_deal_stage` badges a skipped
  write while `crm_bulk_move_deals` badges only `updated_ids` — left alone because
  `provenance_service.record` documents re-badging an identical rewrite as intended
  ("EVERY AI (re)write resets confirmation"), which makes bulk the outlier there, not the
  single-deal path.
  In **bulk specifically**, a deal already in the target stage is dropped from the write
  plan after the locking `SELECT … FOR UPDATE` and before any write SQL is issued — so no
  `updated_at` bump, which `LAST_TOUCH_SQL` would otherwise read as a touch and reset the
  staleness clock on a deal nothing changed. (The single-deal path reaches the same end
  state differently: it *issues* the UPDATE, which then matches no row.)
  Whole-request refusals come back as `ok:false` with HTTP 200, never a 4xx, because the
  board's honesty depends on only transport/5xx failures throwing: a refusal means
  nothing was written (revert), a thrown 5xx means the outcome is genuinely unknown
  (never revert — a connection can drop after the commit). **The open-stage-only promise
  is kept at the TOOL layer only** (#99): `crm_update_deal_stage` and
  `crm_bulk_move_deals` both advertise it, so both carry a schema `enum` of
  `service.OPEN_STAGES` *and* an executor refusal of `CLOSED_STAGES` — the enum only
  steers (nothing validates tool args server-side), so the executor is the enforcement
  point. `CLOSED_STAGES` sits beside `DEAL_STAGES` and `OPEN_PREDICATE` is its SQL
  spelling (a test pins them in agreement). The service, the REST route and
  `crm_update_deal`/`crm_create_deal` stay permissive by design — this is interface
  honesty, not a data-integrity boundary; closes route to `crm_mark_deal_won`/`_lost`,
  which *can* record a lost reason.
  **A human can write that reason since #128**, through `POST /api/crm/deals/:id/mark-lost`
  → `service.mark_deal_lost`. Before it, `lost_reason` rendered on the deal sheet but the
  assistant was its only writer — `_DEAL_USER_WRITABLE` excludes the column, so a keyless
  install could read a lost reason and never type one. The route delegates to the lifecycle
  verb rather than widening that set, which is what preserves the invariant the exclusion
  exists for: a reason still arrives only WITH the close, and can never be pasted onto a deal
  that isn't lost. `mark_deal_lost` gained an `author_id` the route fills from
  `get_current_user` — the generated "Deal lost —" chatter note is a human's typed prose, so
  leaving it NULL would file a rep's own work as Unattributed (the assistant tool still omits
  it, which stays correct for Phase A). The endpoint is chosen by the ACTION, not the text:
  `crm/dealStageWrite.stageWriteRequest` (shared by `PipelinePage` and `CrmDashboardPage`, so
  they cannot drift) routes to the verb whenever the Mark Lost dialog was used *even with an
  empty reason*, because `PUT /deals/:id {stage:'lost'}` does not zero `probability` and
  writes no note — a drag or bulk move still takes the plain PUT. `components/LostReasonModal`
  reuses #57's `composerKeyAction`, inheriting its IME (`isComposing` off the NATIVE event)
  and AltGr guards instead of the blueprint's weaker inline chord, and renders through a
  **portal**: the surface it opens over sits BELOW the assistant launcher — `DealDetailSheet`'s
  root set `zIndex: 39`, and since #75 its replacement is `DetailModal` in `underLauncher` mode
  (`z-50 dock:z-[39]`) — and either one establishes a stacking context no descendant can escape. It also holds a
  synchronous submit-once latch, because `_write_deal_update` returns True for a no-op, so a
  repeat submit would append a second note. The REST boundary **rejects** a reason past
  `MAX_LOST_REASON` (422) where the service truncates — deliberately unlike `BulkDealMove`,
  which declines a Pydantic cap because *its* service refuses with a renderable sentence;
  silently dropping the tail of typed prose is data loss, not a refusal worth preserving.
  **Owner is displayed since #128** too, via `components/OwnerName` on the deal sheet and the
  contact/company detail pages. `useUsers().nameFor()` already resolved NULL to "Unassigned"
  but only the pipeline facet chip consumed it, so an owner was editable and filterable yet
  never *shown* — worse than the blueprint's blank-row bug. Those rows render
  **unconditionally**, unlike every hide-when-blank neighbour: NULL owner is a real state
  (#60) and a hidden row is what makes it unreadable as one, so a `{owner_id && …}` wrapper
  reintroduces the bug. On `CompanyDetailPage` it is its own line rather than a `subline`
  term, since that string is built by dropping blank fields. Chatter now also attaches to
  **companies** (zero-migration: `entity_type` is
  free TEXT), cleaned in `delete_company`.
  **Chatter notes take attachments** (#57, `backend/crm/attachment_service.py` +
  `backend/core/thumbnails.py` + `frontend/src/crm/{chatterAttachments,chatterComposer,postNote,useChatterPost,useAuthedBlobUrl}.ts`
  + `components/{NoteComposer,NoteAttachments,AttachmentLightbox}.tsx`), keyless — nothing
  here keys off `ai_ready`. **The bytes live in Postgres (`bytea`), and that answers the
  issue's gate question rather than dodging it:** the Railway container filesystem IS
  ephemeral EXCEPT the volume `railway.json` requires at `/app/backend/data`
  (`requiredMountPath`; a deploy without it does not start — it is why the branding logo
  and the encryption-key fallback survive), so files-on-disk WOULD have worked on both
  targets. Postgres wins anyway on one store / one transaction / one `pg_dump`: the
  README's documented rollback is restoring a dump, which contains no files, and with the
  bytes in the row every `TRUNCATE` and cascade takes them atomically instead of leaking
  bytes at each site. The honest cost is stated in the migration header — attachments grow
  the database and the dump, `core/postgres.py` has no streaming primitive, and a hard
  delete does not immediately shrink TOAST files. Bounded by a **10 MB per attachment**
  (the `assistant/uploads` precedent, deliberately tighter than the blueprint's 20 MB
  because there is no streaming read) and **10 per note**; NOT bounded at install level,
  which is accepted for a self-hosted CRM whose members can already delete every record —
  a quota is the named upgrade path. Two honest limits on those caps: peak memory is
  per-request × threadpool concurrency (~40 threads on the one worker), not 10 MB — held
  down in practice by thumbnails-only lists, explicit-open originals, the 304 path and a
  client that aborts abandoned downloads, with a weighted admission gate as the upgrade
  path; and an oversized **multipart** body is spooled by Starlette BEFORE any route code
  runs, so no per-route cap can stop it. That is why `main.MAX_REQUEST_BYTES` exists — a
  64 MB middleware backstop (Content-Length only) that runs before the body is consumed.
  It is a disk backstop, not a feature limit, so it must stay above the largest legitimate
  request (an assistant upload: 5 × 10 MB); a test pins that ordering, and another pins the
  spool-before-dependencies behaviour that makes middleware the only workable layer.
  **That backstop is not an admission limit, and #127 split the two.** A disk backstop
  sized for the app's largest route is 6.4× what a chatter attachment may be — and 32-64×
  what the logo and CSV-import routes accept — so everything between each route's real cap
  and 64 MB was admitted, spooled and parsed before that route's bounded read refused it:
  the cheap outer gate none of these uploads had. `main._ROUTE_REQUEST_LIMIT_SPECS` (the
  hand-edited table; `_ROUTE_REQUEST_LIMITS` is its compiled derivative) is therefore
  a first-match-wins path-template → ceiling table consulted by the SAME middleware
  (`_request_limit_for`), sizing **each** upload route at its own feature limit plus
  `MULTIPART_ENVELOPE_BYTES` and leaving every other path on the global ceiling. It lives
  in the existing middleware rather than a `Depends` guard or a second middleware for the
  reason the paragraph above already establishes — middleware is the only layer that runs
  before the body is consumed.
  **The table is keyed by the route's path TEMPLATE and compiled with Starlette's own
  `compile_path`, and hand-writing those patterns instead is a bypass, not a style choice.**
  A hand-written `\d+` for `{note_id}` reads as obviously correct and is wrong: `note_id:
  int` is FastAPI **validation**, not routing, so the router compiles that parameter to
  `[^/]+` and `/api/crm/chatter/note/abc/attachments` reaches the multipart parser, spools,
  and only then returns 422 — under a `\d+` gate it drew the 64 MB backstop the whole table
  exists to avoid. An admission pattern must cover everything the ROUTER accepts, not
  everything the handler will go on to accept; deriving it from the template is what makes
  that unrepresentable rather than merely fixed once. (An earlier revision of this work
  shipped the `\d+` version and a test that asserted the bypass was correct behavior.) The
  **assistant** upload route is deliberately absent: its legitimate 5 × 10 MB already sits
  close to the 64 MB backstop, so a row would only restate it. #127 also gave
  `branding/router.upload_logo` the repo-wide `read(cap + 1)` idiom — it was the one upload
  route still doing an unbounded `await file.read()`, buffering the whole part before the
  size check could refuse it.
  Three properties are pinned, and the first two are pinned that way because the obvious
  test does **not** fail against the bug: the headline test asserts the **parser never ran**
  rather than merely a 413 (the routes have always 413'd) and sizes its body from the
  FEATURE cap, never from `_request_limit_for` — sizing it off the function under test made
  it pass with the table emptied, since the request then simply hit the global ceiling
  instead; the mounted-route guard asserts the table is **non-empty** before looping, since
  a `for` over an empty table passes while checking nothing; and
  `test_every_upload_route_is_bounded_below_the_backstop` enumerates every file-taking
  route from the app itself, so **a new upload route that forgets its row fails CI** rather
  than silently admitting 64 MB. That last one is only as good as its detector, so the
  detector reads FastAPI's dependency graph via `get_flat_dependant` +
  `isinstance(field_info, params.File)` and carries its own synthetic self-test: the
  obvious version — a string match for `UploadFile` on `route.dependant.body_params` —
  silently misses `data: bytes = File(...)` (annotated `bytes`) and any file arriving
  through a `Depends(...)` sub-dependency (`body_params` is not flattened), which are both
  ordinary FastAPI and would have been waved through green. A fourth test pins each row's
  exact `feature cap + envelope` arithmetic, because a wrong VALUE (a row at 63 MB)
  satisfies every structural guard while reopening nearly the whole window. Two of these
  are pinned at the MIDDLEWARE rather than at the helper, which is not a stylistic
  preference: a root_path test that calls `_request_limit_for(get_route_path(...))` itself
  passes no matter what the middleware feeds in — reverting the fix left the file green.
  Unchanged and deliberate: a **chunked** body declaring no
  Content-Length still slips both ceilings and is caught only by the route's
  `read(cap + 1)` — bounded in memory, still spooled — because counting bytes as they
  stream stays "real machinery for a case no browser produces". A *lying* Content-Length is
  not a third hole: h11 delivers exactly the declared byte count to the app, so the
  transport enforces the number the middleware trusted. `crm_chatter_attachments` is the second CRM table
  with a **real FK** (`crm_chatter ON DELETE CASCADE`), which is the whole lifecycle
  design: `delete_contact`/`delete_company` need NO new code, and the FK means the table
  MUST ride BOTH `_truncate_all` statements (Postgres refuses to truncate a referenced
  table alone — the `deal_stage_events` rule). It is deliberately **excluded** from
  `is_crm_empty`/`_crm_empty_in_txn`: the cascade makes "attachments while `crm_chatter` is
  empty" unrepresentable. Three rules are non-negotiable and each has a test: (1) the
  stored MIME comes from **magic bytes** and the client's declared type is *not even a
  parameter* — four image types plus PDF keep a real type, everything else (SVG included)
  is stored and served as inert `application/octet-stream`, because these bytes come back
  from the app's own origin where a stored XSS reaches the session token (the branding-logo
  lesson, twice); (2) serving is **authenticated** (`GET …/thumb` and `…/file` behind
  `get_current_user`) and the frontend fetches through `apiBlob()` into object URLs — a
  bare `<img src>` cannot work at all here, since auth is a Bearer token with no cookie
  fallback — with `nosniff`, an attachment `Content-Disposition` (RFC 5987), `ETag`,
  `Vary: Authorization` and `Cache-Control: private, no-cache`, never `immutable`, because
  `RESTART IDENTITY` reuses attachment ids and a fresh immutable response is never
  revalidated; (3) list views fetch **only** the ≤28 KB server thumbnail — the original
  loads on an explicit open. `filename` is normalized once in the service and stored NOT
  NULL (a nameless upload used to crash the header encoder; a path-bearing one is a header
  problem). `create_attachment` runs cheap-preflight → thumbnail (OUTSIDE any transaction —
  Pillow never runs holding a row lock) → ONE transaction that re-checks everything under
  `SELECT … FOR UPDATE` on the parent note, with **idempotency checked before the cap** so a
  lost response on the tenth attachment stays retryable; `delete_attachment` takes the same
  parent-then-child lock. A NULL thumbnail is a legal terminal state (a non-image, or an
  image the decompression-bomb ceilings refused) and renders as a **download-only** chip —
  never the lightbox, or the browser would perform exactly the decode the server declined.
  `core/thumbnails.py` is ported from the blueprint's *gallery* lineage, not its chatter:
  cake_os chatter has no server-side thumbnails at all, so #57 is a composite of three
  upstream features rather than a port of one. Attachment metadata rides
  `get_chatter`, so `crm_get_chatter` inherits it with no new endpoint — one more
  user-typed-text field in front of a background turn, on #22's terms (the ceiling is still
  one `notify_user`). No agent upload tool: the model has no bytes. **Phase 2** adds the two composing reads:
  `crm_get_deal_health` (one deal — #18's `score_deal()` plus days-in-stage,
  days-since-touch, open/overdue todos and missing links, reduced to a `flags` list;
  it composes and never recomputes the scoring model) and
  `crm_get_pipeline_analytics` (time-in-stage, per-stage conversion and velocity read
  from `deal_stage_events` — precisely the half #20 had to drop for want of a
  stage-change trail, so the two analytics tools are complementary, not overlapping).
  Because that log only began at Phase 1, every response carries `history_since` /
  `history_covers_window` and `SALES_GUIDE` tells the assistant to state the real span
  rather than present a partial funnel as the whole picture.
  The pipeline board's bulk UI (#55) holds a `bulkPending` lock from the click until the
  reconcile refetch settles — while held, `moveDealStage` returns early and drag is
  disabled, so a single-deal rollback can't clobber the server truth the bulk is about to
  fetch. Selection is a `Set<number>` intersected with the currently visible set; **since
  #74 that intersection is computed twice, deliberately, by two owners that cannot
  disagree**: `CollectionView` derives the bar's COUNT from selected ∩ the current view's
  items (and renders the bar only when that set is non-empty), while `applyBulkMove`
  recomputes `applicableBulkIds(bulkSelected, state.visibleItems)` at CLICK time, because
  the selection can change between the render that drew the bar and the click that applies
  it. They agree because both read `visibleItems` — which requires the pipeline config to
  declare **no `getVoided`** (that absence is what keeps `kanbanItems === visibleItems`)
  and hidden-stage deals to be absent from `items` entirely, not merely from the columns.
  Adding a `getVoided`, or hiding a stage by omitting its column while leaving its deals in
  `items`, silently splits the two; `pipelineCollection.test.ts` pins the former.
  **Since #124 the board starts with `won` and `lost` put away**, and the whole change is one
  default plus a Settings card — #74 had already built the machinery (`hiddenStages`,
  `visibleStageKeys`, `revealStage`, the always-mounted "N stages hidden · Show all"). So
  stage visibility now has **TWO keys, answering different questions**: sessionStorage
  `crm_pipeline_hidden_stages` is "which columns have I put away IN THIS TAB right now"
  (transient, edited from the board), while localStorage `cakecrm_pipeline_show_closed` is
  "should a board START with the closed stages showing" (durable per device, edited from
  Settings → Personal → Pipeline board, and FALSE by default). `loadHiddenStages` consults the
  second only when the first holds nothing usable, so a tab-local reveal outranks the standing
  default without overwriting it. **One store could not express both**: collapsing them makes
  every board-side hide durable and every reveal permanent. A stored `[]` is honoured verbatim
  rather than re-seeded — it is what "Show all" writes, and re-seeding would undo that button
  on the next mount. `CLOSED_STAGES` is derived in `constants.ts` from `OPEN_STAGES`, never a
  second `['won','lost']` literal.
  **The reconciliation inside `saveShowClosedStages` is the feature, not tidiness.**
  `PipelinePage` persists its hidden set on the FIRST render, so by the time anyone walks from
  the board to Settings their tab always HAS a stored set — and a stored set outranks the
  preference. Writing only the preference would therefore leave the toggle looking INERT to the
  one person most likely to check it. It is narrow on purpose: it touches `CLOSED_STAGES` and
  nothing else, so a manually hidden open stage survives, and it applies the `show` ARGUMENT
  rather than re-reading storage, so the toggle still works for this tab when the localStorage
  write was refused. Three tests pin it, and the board → Settings → board round trip is pinned
  at the PAGE, because the storage-level test cannot see the mount that reads it.
  Persistence is `localStorage` per device, on the `cakecrm_theme` precedent — this repo has no
  per-user server preferences store and #124 is not the issue that builds one. **Two limits are
  accepted and stated rather than hidden:** a pipeline already open in ANOTHER tab keeps its own
  session override and does not follow the setting (the setting is a default, not a broadcast),
  and a tab holding a stored `[]` from before this landed never acquires the new hidden default.
  Nothing on the board is a lie in either case, and the "N stages hidden · Show all" control is
  always on screen. **The board's own FILTERS still apply on top of both** — `visibleStageKeys`
  narrows by the persisted stage facet as well as by `hiddenStages` — so a facet selecting only
  Lead keeps Won off screen with the preference ON. Clearing someone's filters from a Settings
  page would be the worse answer, so the card says so instead. **Display-only, structurally:**
  the board reads only `deals` from the payload and derives every total client-side downstream
  of the same `items` filter, `openPipelineTotals` filters to `OPEN_STAGES` regardless, and the
  dashboard and `get_analytics()` are separate server reads that know nothing about a browser
  preference — so win/loss ratios stay true.
  **Making the closed stages hidden by DEFAULT turned two of #110's accepted costs into the
  ordinary path, so both changed with it.** `updateDealStage` now calls `revealStage` BEFORE
  awaiting the write rather than after — reversing #110's stated ordering — because the
  optimistic move and the reveal have to be one gesture: with the reveal after the await a Mark
  Won made the card vanish for the whole duration of the PUT, which is the exact failure that
  reveal exists to prevent. And a `?deal=` link now un-hides the stage its deal sits in, exactly
  as `?stage=` always has, because a hidden stage is filtered out of `items` itself (unlike a
  facet, which applies downstream of it) and `items` is what `CollectionDetail` resolves against
  — so #110's "one extra GET" for such a deal became every link to a closed deal on a default
  install, and is now no GET at all.
  The rejected/unconfirmed/skips wording lives in the pure
  `crm/bulkOutcome.ts` (`ApiError` was added to `core/api/client.ts` to carry the status
  that split needs). The ~48 `crm_*` agent tools + executors are
  collected UNCONDITIONALLY via `crm.tools.get_crm_tools()` — each def carries a
  `"writes"` flag (the single source of truth for the assistant's confirmation gate),
  consumed by `assistant.registry.ToolRegistry` (landed #4).
  **The tool layer knows who is asking** (#190, Phase B/B1): `ToolRegistry(user=…)` takes
  the `get_current_user` row and curries it into `get_crm_tools`/`get_gtd_tools`, which is
  what lets `crm_log_activity`/`crm_add_note` pass `actor_id`/`author_id` and the four
  interactive creates (plus `todo_create`) stamp the requesting seat as `owner_id`.
  Dispatch is untouched — still `fn(**args)` over the model's arguments — because the
  binding happens in the executor map, the closure shape `get_notification_tools(registry)`
  already established. **Identity is server-supplied and unspoofable:** every bound
  argument is stripped from the model's args first, unconditionally, so `user=None` means
  NULL rather than "whatever the model produced". Five reads (`crm_find_contact`,
  `crm_search_companies`, `crm_list_todos`, `crm_get_stale_deals`,
  `crm_get_contact_staleness`, plus `todo_list`) take an `owner` WORD — `me`,
  `unassigned`, or an email — never an id, so the model cannot address a seat by guessing
  a number. **The two company writes take the same word as an ASSIGNMENT** (#237, port of
  cake_os #3184): `bind_owner_assignment` resolves it through the same `_resolve_owner`
  (with `require_active=True`, since the UI's owner dropdown offers only active seats too),
  drops a model-supplied `owner_id`, defaults a create to the asking seat, and on an update
  sends NO `owner_id` key when the word is omitted — `update_company` reads key presence as
  "set", and None there means unassigned. Contact and deal updates still take no owner.
  `crm.service.owner_condition` + the `UNASSIGNED` sentinel are how the read filter reaches
  the shared WHERE builders, which is what keeps a filter from landing on a page query
  without also landing on its COUNT. Binding a user never changes which tools exist or
  their `writes` flags — the background allowlist is derived from that map, and a test
  pins it in both directions. The four read-only
  intelligence tools (`crm_get_stale_deals`, `crm_get_contact_staleness`,
  `crm_find_duplicates`, `crm_scan_gaps`, all in `crm/analytics_service.py`) are pure
  SQL — keyless — and because `writes:False` derives the background allowlist they are
  heartbeat-callable for free.
  **Every tool that returns a deal record returns its `url` too** (#145) — a link that
  opens that deal, attached by `crm.links.with_deal_url()`. `crm/links.py` is the ONE
  server-side definition of the shape, `/crm/pipeline?deal={id}`: the real board route
  plus a query param, deliberately not a `/crm/deals/{id}` route, because `PipelinePage`
  already parses a `?stage=` sibling and a second form would mean two shapes against one
  parser. `deal_url` is absolute only when the install's public address was actually
  configured (`FRONTEND_URL` or a Railway domain — recorded as
  `settings.frontend_url_is_default`, tracked the same way `jwt_secret_is_auto` is);
  otherwise it stays relative, because asserting the `localhost:5173` dev default in a
  message sent to someone's phone is worse than a path their browser resolves.
  **The rule is stated rather than a chosen subset, and membership is decided by tracing
  what a tool's SERVICE returns — never by its name.** Far more tools qualify than the
  four the issue names: alongside the deal reads and all seven write confirmations,
  `crm_get_contact` and `crm_get_company` embed their rollup's deal rows, `crm_dashboard`
  returns five under `top_deals`, `crm_analytics` returns a `stale_deals` list beside its
  scalars, and `crm_find_duplicates`/`crm_scan_gaps` name deals one level deeper. The
  blueprint shipped that same miss three times, each time reasoning from a tool's headline
  purpose — and so did this port: `crm_get_company` was found by the final reviewer, and
  the converse guard was green because its own list of deal-returning services had been
  hand-written and did not name `get_company_detail`. That list is now derived: a test
  scans `crm.service` and `crm.analytics_service` for functions whose SQL reads the deals
  table and fails on any that is classified in neither direction, so the next one has to be
  looked at.
  `with_deal_url` is inert on anything without an integer `id` (`type(...) is int`, since
  `bool` is an int subclass and would emit `?deal=True`), so an error dict or `None`
  passes through — a link built from a missing id is worse than no link. The matching
  `CRM_DEAL_URL_GUIDANCE` is applied by ONE pass over `CRM_DEAL_URL_TOOLS` **at import**,
  never inside `get_crm_tools()` — that is called per turn and returns the shared list, so
  appending there would grow every description without bound. `tests/test_crm_deal_links.py`
  makes both halves structural: it derives the attaching set from the executors' own
  source and fails when the registry or a description disagrees, fails when an executor
  reaches a deal-returning service without either attaching a link or recording a waiver,
  and READS the TypeScript source to pin `dealDeepLink` against `DEAL_PATH_TEMPLATE`
  (two suites each asserting their own hardcoded copy of the string would prove nothing —
  editing the template and its expectation together is one self-consistent commit that
  leaves both green and the producers divergent). Never re-guard this with a hand-written
  tool list: a hand-list can only re-assert the mistake it was written beside.
  On the page, `?deal=` is resolved **during render** (`crm/dealDeepLink.ts` holds the pure
  rules; this repo's react-hooks ruleset makes a synchronous setState inside an effect a
  build error, so the `?stage=` render-compare pattern is the sanctioned one). Unlike
  `?stage=`, the parameter is **kept**, and the resolution keys off `location.key` — every
  navigation is its own event, including one to the URL already showing. That is what makes
  following the same link twice work (the id alone cannot tell a second click from no click)
  and what lets a link whose refresh FAILED be retried. Consuming it was tried and cost more
  than it bought: the rewrite is itself a navigation, so it re-armed the link it had just
  resolved and fired a second board refresh, and it raced the `?stage=` consumer for the
  same params object. Keeping it also means reload reopens the deal and the address bar is a
  real copy source — worth knowing for #75, which specifies stripping on the blueprint's
  authority, where losing the deal on refresh is called out as a caveat. Board membership is asked of
  the WHOLE payload, never of `filteredDeals`: a session facet says nothing about whether a
  deal exists. A miss on a board that PREDATES the link earns one silent refresh before any
  notice, because the assistant hands out links to deals it just created while the drawer
  sits over an already-loaded board; if that refresh fails the page says NOTHING rather than
  telling someone their live deal was deleted; if that one refresh fails the link is
  RETIRED rather than left armed, or an unrelated load minutes later would pop a sheet open
  with no gesture toward it. Staleness is measured in **load generations**
  (`boardLoads`), never in `data`'s object identity — every optimistic update on that page
  replaces `data` without asking the server anything, and reading that as "the board caught
  up" produced exactly the false accusation the refresh exists to prevent (caught in
  review). The notice takes itself back if the named deal later appears, since it tells the
  user to turn on the Archived facet and that has to be allowed to work; and a newer link
  supersedes the sheet an older one opened, keyed on which deal a LINK opened so a card the
  user clicked themselves is left alone.
  **The drawer is the primary surface, and it needed a fix outside this feature to work at
  all:** `assistant/MarkdownContent` rendered every link `target="_blank"`, and the session
  token lives in `sessionStorage`, which is per-tab and which a `noopener` tab does not
  inherit — so an in-app deal link opened a tab with no session, bounced through `/login`
  and landed on the dashboard with the id discarded. Same-origin links now navigate in the
  same tab through a react-router `Link`; everything else still opens in a new tab with
  `noopener noreferrer`. Internal-ness is decided by resolving the href with `URL` and
  comparing origins, never by a `startsWith('/')` test — `//evil.com/x` starts with a slash
  too, and an assistant message can carry a prompt-injected href.
  **One deployment note:** with neither `FRONTEND_URL` nor a Railway domain set, `deal_url`
  emits a relative path, which is correct in-app but not clickable in Telegram or a push
  notification. That is the least-wrong output (an absolute `http://localhost:5173/...` is
  wrong for every reader who is not at that machine), and the fix is to set `FRONTEND_URL`
  on any install whose assistant messages leave the app.
  **Every write tool's success result names the record it actually wrote** (#236, port
  of the blueprint's #3052): `target` = `{entity_type, id, title, owner_id}` plus `url`
  for a deal, attached by `crm.tools._with_target` (bulk twin `_with_targets` → a
  `targets` list of the ids actually written, ONE query). The blueprint's audit found notes logged against
  hallucinated deal ids while the result said only `{"ok": true}`, so the model narrated
  success against the deal the user had named. `title` is a deal/todo `title` or a
  contact/company/project `name` under one key; `todo_projects` has no owner column, so
  a project's `owner_id` is always None. Tools holding the row pass it as `record=` and
  spend no query; the rest issue one lean three-column SELECT after the write — BEFORE it
  for the deletes, which have no row left afterwards. `crm_log_activity` names deal >
  contact, and an activity on nothing carries an all-None block. A failed confirmation
  read never fails the committed write (an error would invite a retry and a duplicate
  note): the id survives flagged `lookup_failed`, and so does a row deleted between the write and the read — every requested id is reconciled. The GTD `todo_*` writes carry it too,
  since GTD is the default mode. The behavioural half is one SALES_GUIDE paragraph
  ("Check what you wrote"). `tests/test_crm_write_target.py` derives the write set from
  every def list the registry composes and pins a reasoned waiver list in both
  directions (memory, context files, the Gmail draft, `notify_user`,
  `crm_recompute_lead_scores`), and checks a real registry in both todo modes carries no
  write it never saw.
  **Bare deal ids in outbound text become `Title (url)`** (#238, port of the blueprint's
  #3175). `ToolRegistry.execute_tool_sync` feeds every result to
  `crm.links.remember_deal_refs`, which records `{id: (title, url)}` on the registry's
  `deal_refs` for any dict whose `url` equals `deal_url` of its own `id`/`deal_id` and that
  has a title — only `with_deal_url` produces that url, so contacts and todos never count.
  A registry is built per turn, so the map is per turn. `link_deal_refs` then rewrites
  "deal #14" / "deal 14" / "#14" at the two seams whose reader cannot click an id:
  `notify_user` (title and body, each linked on its own) and Telegram's `_flush`. **Only ids
  a tool returned this turn are rewritten**, so a hallucinated id is never lent a real
  title, and a bare `#N` after a label (`PO #14`, `todo #14`) is left alone. The
  "already linked" test is digit-bounded, because deal 1's relative url is a prefix of deal
  14's. A Telegram turn that pauses for Approve/Deny spans several registries (the turn up
  to the card, one per button press, the continuation), so `telegram.service._paused_deal_refs`
  carries every deal the paused turn saw into the continuation's reply, keyed by link and
  cleared by a new message; a bare "so" is not a label, since it is usually the English word. **The drawer and history are deliberately
  untouched**: the drawer streams deltas it cannot take back, and the persisted row keeps
  the model's own wording. A relative `deal_url` stays relative — the rewrite never invents
  a host. Pinned by `tests/test_deal_ref_outbound.py`. **AI touch counts +
  field provenance** (#16) are the two zero-keys-degrading AI reads: an in-process
  daemon worker (`crm/touch_count_service.py`, event-driven off note/activity writes,
  light tier via `get_ai_provider(agent_model_tier="light")`, prompt-injection-hardened,
  never-fabricate) stores an estimated touch count in `deals.ai_touch_*` for a pipeline
  nudge pill; and `crm/provenance_service.py` (`crm_field_provenance`) records which
  standard fields the assistant wrote (recorded inside the assistant's write-tool
  executors — human router edits don't), badged until confirmed or overwritten. Both are
  invisible with zero keys (no count is computed, no provenance is written).
  **Since #56 the touch count is explainable, and that changed what the number means:**
  the model now judges EVERY numbered evidence line and `ai_touch_count` is the **derived
  sum** of the lines it marked as touches, so the pill and the drill-down cannot
  contradict each other (one note describing three calls now counts once — the price of a
  number you can audit). The per-line verdicts live in `deal_ai_touch_evidence`, **one
  JSONB snapshot row per deal** (not a normalized table: verdicts are replaced wholesale,
  never queried across deals, and the synthetic `[deal notes field]` line has no source
  row; not a column on `deals` either, or a multi-KB blob would ride every pipeline card).
  It is FK-less per the audit-table convention and therefore MUST stay in both
  `_truncate_all` strings, but is deliberately **excluded** from `is_crm_empty` /
  `_crm_empty_in_txn` / the seed guards (derived, not entity data — same side as
  `deal_stage_events`/`proactive_nudges`) and owns no sequence (PK is `deal_id`).
  `service._store_touch_count` writes count and snapshot in ONE transaction with the #16
  stale-write guard unchanged (ordering key on the event path, CAS on `force_write`), and
  the snapshot write is **gated on the UPDATE's rowcount**, so a recompute that loses the
  guard leaves no explanation of a number it never stored. Items store **no line text** —
  the reader renders the live row, because a note edit rewrites `message` without moving
  either guard key. A reply whose verdicts fail validation writes the **count alone and
  deletes the stale snapshot** (an explanation of a different inference must not survive
  beside a new number); incomplete coverage is a rejection, never a partial merge. The
  classified window shrank to **35 chatter + 15 activities** because the reply now scales
  with it and `stream_turn` exposes no `max_tokens` knob — the smallest fixed provider
  ceiling is 4096 output tokens (Ollama, openai_compat default), and a test pins the
  arithmetic so raising the window has to confront the ceiling. Read-only via
  `GET /api/crm/deals/:id/touch-count/evidence` → `touch_count_service.get_touch_evidence`,
  which never re-runs AI and reports `verdict_state` **current/stale/superseded/none**. It
  earns "current" only against FIVE checks, because four kinds of drift move none of the
  stale-write guard keys and so are invisible to a sum comparison alone: the visible touches
  vs. the number on the pill (catches a contact deletion that destroyed shared
  `activity_log` rows, or an archived note), a row **edited** since it was judged (an edit
  to a non-touch row moves nothing else), a live row the snapshot never **covered**
  (archiving a judged row out of a full window pulls an older one in, leaving the row count
  and newest timestamp untouched), a judged row that has **vanished** from the live set
  (clearing a `deal_notes` field judged not-a-touch moves *nothing* — that entry is in
  neither the watermark nor the evidence count — and on a closed deal a deleted row escapes
  the evidence-count check too, since that one is open-deals-only), and an item whose digest
  is unusable so it cannot be **verified** at all. Comparing the live and stored key sets in
  BOTH directions is what makes this a closed question rather than a list of drift routes to
  keep extending — four separate review findings landed on this reconciliation before it was
  symmetric. Edit detection compares a stored digest of the line as judged
  (`_line_hash`) against the live line — deliberately not a timestamp, since `activity_log`
  has no `updated_at`, `deals.notes` changes without one, and an `updated_at`-vs-`computed_at`
  comparison misses an edit made while the model was running. The bounded list also reports
  `truncated`, derived from a probe row fetched past each window so a deal holding exactly a
  full window is not mislabelled. Counts written before #56 report `none`; `?scope=all` is
  the documented repair, and nothing is backfilled at deploy time. Zero keys still degrades
  cleanly: no count means no pill and no drill-down, while stage moves (read from
  `deal_stage_events`) carry a **deterministic** never-counted verdict needing no provider
  at all. Field edits are deliberately absent **as an evidence source** — CakeCRM has no
  per-edit timeline (#15 dropped chatter's audit columns and `crm_field_provenance` is
  current-state, not history), so synthesizing one would fabricate history. `/backfill/status` also reports verdict health (ok/fallback/
  failed), because a model that silently stops emitting the schema would keep updating the
  badge while every detail view went empty. Contact
  import is keyless for CSV/vCard; the AI smart-import path (`get_ai_provider()`)
  degrades to a warning when no provider is configured and its UI affordance keys off
  `ai_ready`. First-run offers to load fictional sample data (prompt tracked on the
  `crm_meta` singleton, not a per-integration flag). The **CRM-first shell** (#9) leads
  nav with Dashboard/Pipeline/Contacts/Todos, surfaces the assistant as a persistent
  launcher (never the home page) — **bottom-RIGHT, and it FOLDS**: on load the pill reads
  its full label for `LAUNCHER_INTRO_MS`, then folds to a bare icon circle, unfolding while
  the pointer is within `LAUNCHER_REACH_PX` of it or it holds focus (the intro restarts on a
  LABEL change, since `aiReady` resolves after mount). Proximity is one document
  `pointermove` measuring distance to the button's box, never an invisible padded wrapper,
  which would swallow clicks meant for page content beside it; touch pointers are ignored.
  `aria-label` carries the name throughout, the transition lives in `.ck-launcher*` in
  `index.css` so reduced-motion can turn it off, and the fill is its own token,
  `--color-ck-accent-launcher` (a hair darker and warmer than `accent`, `accent-ink` on it
  pinned at AA by `hueContrast.test.ts`; it has no `-text` twin because nothing paints it as
  a glyph). `ToastViewport` starts its stack at 88px so a toast never lands on the pill —
  and shows a **dismissible** "add an AI key" nudge —
  never a gate, gated on `!credentials_present`, dismissal tracked on
  `crm_meta.ai_key_prompt_dismissed`. Branding (company name / logo) is edited at
  `/crm/settings`, consuming the existing `/api/branding`.
  **The Settings page is a shell, not a list** (#103): four sections — Personal →
  Assistant → Workspace → Integrations, member-visible first so a member's nav is a
  *prefix* of an admin's and the post-login `isAdmin` flip only appends tabs, never
  inserts one before the section on screen — rendered by `SettingsPage.tsx` as an
  underline tab strip of `<Link>`s in `<nav aria-label="Settings sections">`
  (navigation ⇒ underline tabs, the `ViewSwitcher` rule; deliberately **not** an ARIA
  tablist, because these tabs navigate and a `role="tab"` would promise arrow-key
  roving this does not implement) over the active section's cards **only**. Every card
  wraps itself in `components/SettingsCard.tsx` (`<section aria-labelledby>` + a real
  `<h2>` + optional description/badge), which owns padding; the page owns column width
  and inter-card spacing, so a card carries no `marginTop`/`maxWidth`/padding of its
  own — cards **replace** their old outer `<div>` rather than nesting inside the shell,
  and `settingsSections.test.ts` fails the build if a settings card still imports
  `cardStyle`. The section is deep-linkable as `?section=<id>` and is a pure function of
  URL + role recomputed every render (nothing memoises `isAdmin`, in either direction).
  That is load-bearing for the **Gmail OAuth callback**, which lands on `?gmail=…` with
  no `section`: `wantedSection()` maps a **non-empty** `gmail` to Integrations so
  `GmailCard` mounts and its effect can toast and strip the params, and that same effect
  writes `section=integrations` back as it strips — without which removing `gmail` would
  drop the view to the default section. Two details keep that from becoming a trap, since
  `gmail` outranks `section`: the non-empty test is exactly `GmailCard`'s own
  `if (!result) return`, so the page cannot select a section the card then declines to
  clean up; and the nav **deletes** `CALLBACK_PARAMS` from its links (it preserves every
  other param), because a one-shot callback param riding along would pin the view to
  Integrations and make every tab inert — permanently for a MEMBER, who never mounts
  `GmailCard` and so never strips it. All of it is pinned by `SettingsPage.test.tsx`.
  Only the active section mounts, so Telegram's 4 s link-poll runs only while
  Integrations is on screen; the cost is that switching sections remounts (unsaved
  in-card drafts are lost — acceptable, since the tabs are links and switching is a
  navigation). Branding's form lives in `components/BrandingCard.tsx`.
  The **theme itself is fixed**
  (#54) — one polished CakeCRM look in light and dark, defined as `--color-ck-*` tokens
  in `index.css` with a `.dark` override block; there is **no user-configurable accent**
  (`accent_color` was removed from `/api/branding`, and a stale key on disk is stripped
  on read). The neutral tokens are *semantic* (`bg` = page, `card` = surface, `ink` =
  primary text), so overriding them under `.dark` re-themes the whole app — login,
  setup, CRM, assistant, settings — with no per-component `dark:` variants; that block
  compiles **unlayered**, so it wins over Tailwind's `@layer theme`. Both the Tailwind
  `ck-*` utilities and the inline `var(--color-ck-*)` styles in `shared/styles.ts` +
  `crm/styles.ts` resolve through those tokens, and those modules carry **no literal
  hex/rgba fallbacks** — a fallback would silently pin a light colour into a dark
  surface. Tints are derived with `color-mix()` off a token (`shared/styles.tint()`),
  never hand-written rgba — the one exception being the three per-theme chrome tokens
  (`hover`/`scrim`/`shadow`), declared literally in *each* block because they tint
  **ink**, not the accent: a single dark tint would vanish on a dark surface.
  **Every hue is TWO tokens, split by ROLE — `--color-ck-<hue>` is the FILL (background,
  border, dot, bar) and `--color-ck-<hue>-text` is the GLYPH (text or icon)** — and since
  #119 that rule covers the whole family (`green`/`amber`/`red`/`ai`, the six `stage-*`,
  and `accent`), not just the brand red. #54 lightened the hues under `.dark` and never
  wrote the mirror rule for light, so in light mode all eleven failed AA 4.5:1 as text,
  worst `ScorePill` warm at **2.45:1** on a stage-washed deal card.
  **The split is arithmetic, not taste, and that is why #68's "retune the token, don't
  migrate call sites" answer was unavailable here:** a chip's background IS
  `tint(<the same token>, 12)`, so darkening one token to fix its text darkens the wash
  under it — clawing back most of the gain *and* pushing the neutral ramp under the floor
  #68 tuned it to (`ink-dim` on a stage-washed deal card has 0.22 to spare). The wash must
  hold still while the glyph moves. Consequently the constants are explicit —
  `SAGE_FILL`/`SAGE_TEXT`, `GOLD_*`, `CORAL_*`, `AI_*`, and `STAGE_COLORS[x]` carries
  `{ text, fill, bg }` — with the old ambiguous bare names **removed** so the compiler,
  not a grep, finds every consumer; that ambiguity is exactly what shipped the eleven
  failures. `ACCENT` keeps its unsuffixed fill name (it predates the rule, is
  overwhelmingly a fill, and ~50 sites already route text through `ACCENT_TEXT` — which
  is why fixing light-mode accent cost zero call-site edits).
  Values are **derived**: the smallest OKLCH lightness step from the fill (hue and chroma
  held) that clears 4.5:1 on every surface the app really paints, targeted ~4.55.
  `--color-ck-on-status` (white light / near-black dark) is the foreground for a hue used
  as a **solid action fill** — `accent-ink` is white in both themes, right on the brand red
  (4.66:1) but 2.49:1 on the green `.dark` lightens for text.
  **`opacity` on a container that holds a chip is now a bug, not a style choice**: it fades
  text and backdrop together, and a hue tuned to just over 4.5:1 cannot survive any fade —
  #83's archived deal card at `opacity: 0.55` measured **2.08:1** on its `ScorePill`, and no
  value below 1.0 fixes it. De-emphasise with `ink-dim` and an explicit chip instead.
  `core/theme/hueContrast.test.ts` is the guard (sibling to #68's `inkContrast.test.ts`,
  separate because they measure different families against different surface models): it
  resolves `var()` chains — most dark `-text` tokens are passthroughs to their fill — reads
  `accent-soft`'s per-theme mix percentage rather than assuming it, pins the base↔text
  pairing **one-to-one in both directions**, and carries a detector self-test that injects
  the real pre-#119 regression. Two things it needs from you: it composites the wash from
  the **fill** while measuring the **text** token (mixing from the text token models a chip
  that darkens with its own label — the coupling the split removed, and the actual bug #119
  found in `MemoryPage`), and unlike its sibling it deliberately does **not** model a chip
  inside an ink-hovered row, because that headroom is free for the ink ramp and here would
  force a visibly larger colour change to clear a pairing nothing renders. **The neutral ink ramp is bound
  by the same rule and is COMPRESSED because of it** (#68): all four of
  `ink`/`ink-mute`/`ink-soft`/`ink-dim` are body text — `ink-dim` alone paints every
  `labelStyle` label, every `sectionHeading()` and most empty states — so every step must
  clear 4.5:1, and a 4-step neutral ramp cannot do that and keep its old spread. Will's gate
  chose to **re-space the token values, not migrate the ~117 call sites**, so `ink-dim` sits
  just inside the lightest passing value (`#595959`, edge `#5c5c5c` / `#adaba5`, edge
  `#acaaa4`) with `ink-soft`/`ink-mute` above it at even CIE L* steps. The three
  secondaries stay as distinct from each other as
  they were (~6-7 L*; the old LIGHT ramp's own mute→soft step was already only 6) — what
  shrank is the primary→secondary gap, 27 L* → 9 light and 31 → 10 dark. The binding surface
  is **never a raw token**: `tint()` chips and row hovers composite an ink wash over
  bg/card/raised, and a chip inside a hovered row stacks two, so the floor is a stacked wash —
  4.72:1 light, 4.56:1 dark; the hover wash beneath is 5% in light and 6% in dark, which the
  guard reads out of `--color-ck-hover` rather than assuming. **That floor is deliberately
  tighter than what renders, and the test distinguishes the two** — dark's floor sits on a
  chip-inside-an-ink-hovered-row over `card` that has *no producer today* (the shared
  collection layer hovers by swapping to the opaque `bg` token, not an ink tint) and is kept as
  headroom, so dark's worst RENDERED pairing is 5.09:1. Keep that distinction if you touch
  these numbers: two review rounds went to prose that called a modelled bound a real pixel.
  That compression is also why `.dark .hljs-comment` moved to `ink-dim`: the new `ink-mute`
  sits 10 L* from `ink` and would have rendered code comments at nearly the weight of the
  code around them. `core/theme/inkContrast.test.ts` parses the **shipped** `index.css`
  (never a copy of the palette — a duplicated table drifts silently, which is the failure it
  exists to stop) and fails CI on any of the 43 surfaces × 4 tokens × 2 themes falling under
  4.5:1 — so **adding a new `tint()` background under ink text means adding it to that
  surface list.** Fonts are **self-hosted**
  via `@fontsource` (Montserrat for
  headings + buttons, Open Sans for body) — no Google Fonts CDN request, so an offline
  deploy renders correctly; `index.css` also carries a `.dark .hljs*` block because
  the assistant's `highlight.js` stylesheet is a fixed light theme. The light/dark
  choice persists in `localStorage`
  (`cakecrm_theme`) and is applied by a pre-React anti-flash script in `index.html`
  whose key is a contract with `core/theme/useTheme.ts` — both also keep the
  `theme-color` meta in sync so mobile browser chrome follows the app. So the CRM stays fully
  usable — and fully themed — with zero AI keys. The launcher opens a **context-aware slide-over drawer** (#14): the
  open deal/contact/company is published through a shared record context
  (`frontend/src/crm/RecordContext.tsx`, set by the detail pages + `DealDetailBody`, #75)
  and injected **per-turn** into the assistant's **volatile** system prompt as a
  server-built sentence from a validated `{record_type, record_id}`
  (`assistant/router.ChatContext` → `identity.build_context_note`) — never persisted,
  never client free text — with record-aware quick actions rendered in the drawer.
  **Lead scoring** (#18) is the third zero-keys, **pure-algorithmic (no AI)** CRM read: a
  0-100 `lead_score` on deals and contacts, recomputed inline at write-event chokepoints
  (serialized per-entity by a `pg_advisory_xact_lock`, never bumping `updated_at`) plus a
  **bounded** daily heartbeat refresh (T1 `_maybe_refresh_scores`, ≤`_REFRESH_BATCH` stalest
  rows per tick, self-resuming). It uses **dedicated columns**, never `deals.probability`
  (a live user/assistant-editable, provenance-tracked field) — and `lead_score` is never
  user/tool/assistant-writable. Deals sort by score client-side (within kanban column);
  contacts keep a server-sorted `lead_score` column (`DESC NULLS LAST`) for the REST/tool
  path, but since #77 the Contacts page sorts client-side over its assembled corpus.
  **#125 gave it its first human INPUT: `deals.deal_temperature`** — `'hot' | 'warm' | 'cold'`,
  or NULL when nobody has triaged the deal. `lead_score` stays unwritable; the temperature is
  an input like `probability`, so it sits in `_DEAL_USER_WRITABLE` + `_DEAL_COLUMN_TYPES`,
  writes through `_write_deal_update` (inheriting #96's SQL no-op test and the `score_on_event`
  rescore) and is exposed on `crm_update_deal` / `crm_create_deal`.
  **A dedicated column, not the blueprint's #19 custom field, and the reason is this module's
  own docstring**: it recorded that this exact factor was dropped from the #18 port "because
  CakeCRM ships zero custom-field definitions". Storing it as a custom field does not retire
  that — `crm_field_definitions` ships EMPTY by deliberate design (its migration says the
  blueprint's pre-seeded definitions are one operator's business data) and nothing seeds one,
  so the factor would be dead code until an admin hand-created a definition under exactly the
  right key. Three more: `field_service.set_field_values` touches neither the parent row nor
  the score, so the EAV route would need NEW rescore wiring instead of riding the chokepoint;
  every deal-returning read in `crm/service.py` is `SELECT d.*`, so a column reaches the board,
  the list, the sheet, the dashboard, both rollups and the Reports timeline with no query edits;
  and a CHECK constraint makes an invalid tier unrepresentable where an EAV `TEXT` value cannot.
  **`SELECT d.*` is not the whole story, though** — `tools._DEAL_SUMMARY_FIELDS` is an explicit
  projection, so a new deal column stays invisible to `crm_get_pipeline` and `crm_search_deals`
  until it is named there. Two other readers keep explicit column lists too
  (`analytics_service.get_stale_deals` / `get_deal_health`); temperature is deliberately absent
  from both, since exposing the value is a different question from adding a health flag.
  **Three divergences from the blueprint, each deliberate.** (1) **Three tiers, not four** — it
  carries a `Cool` between warm and cold; #125's title and body both say Hot/Warm/Cold, and a
  shorter ladder matters for a click-to-cycle control. (2) **Unset is NEUTRAL (1.0), not Cold's
  0.4x.** NULL means nobody judged the deal, and only a judgment should move a score; porting
  the Cold default literally would have multiplied EVERY existing deal's score by 0.4 at the
  next daily refresh — an install-wide silent change to a sorted number, caused by a deploy
  rather than by any user action. (The blueprint is not self-consistent here either: it reads
  the EAV row with a bare `.get(key, "Cold")`, so a never-scored deal gets 0.4x while one
  explicitly CLEARED stores `''` and falls through to a different default.) (3) **Rescaled to
  1.6 / 1.1 / 0.5.** At the blueprint's 2.5x a hot `proposal` computes ~101 and a hot
  `negotiation` ~143 *with typical secondary factors* — both clamp to 99, so two deals a rep
  ranks very differently render identically. The clamp is still reachable for a deal strong on
  every factor, which is correct; what must not happen is the ordinary middle of the range
  collapsing. Temperature is still the strongest single factor (widest existing spread is
  engagement's 0.6-1.25), which is the point of the feature — a rep's read beats every inferred
  signal. An unrecognised stored value also reads as 1.0, failing toward neutral because the
  CHECK makes one unrepresentable, so reaching that branch means the column drifted.
  On the client, `crm/dealTemperature.ts` holds the pure rules and
  `components/DealTemperatureIcon.tsx` is the one control, used by the board card, the List
  column and the deal sheet. **The cycle is `not set → hot → warm → cold → not set`**: one
  click from rest flags a hot deal (the blueprint's call, and right), and it RETURNS to unset,
  which the blueprint's does not — there, clearing needs its custom-fields dropdown, and this
  repo has no second surface for a column, so a one-way cycle would make a mis-click permanent
  from the card. **Every state has its own SHAPE**, not just its own hue — dashed ring (not
  set), flame (hot), filled dot (warm), solid ring (cold) — because distinguishing tiers by
  colour alone fails WCAG 1.4.1 and a `title` helps assistive tech, not someone looking at the
  screen. The first cut gave warm and cold one filled dot in two colours; `dealTemperatureRenderKind`
  now exports the mapping and the test walks the click cycle asserting no two ADJACENT states
  share a kind, verified red against that regression. The flame takes `CORAL_TEXT`, not a fill
  token: `shared/styles.ts` says `_TEXT` paints a glyph, "text **or an icon**" (#119), and that
  pairing on a stage-washed card is already measured by `hueContrast.test.ts`, so nothing new is
  owed there; the dots are fills and owe nothing either. The List column is **display-only**
  like `stage` — a lexical sort over `'cold' | 'hot' | 'warm'` orders the tiers wrongly while
  looking like it works, so sorting by temperature needs a real sort field with an explicit
  rank, as its own change.
  All three surfaces write through #74/#75's `writeDeal` as a **fields-only patch**, which is
  what serialises a temperature cycle against a drag of the same deal on that deal's write
  chain — both reconcile from `get_deal`'s full row, so in parallel a stage response would
  spread a stale `deal_temperature` over the one just written. **The board card takes the writer
  as a plain prop; the List reaches it through a context** (`components/DealTemperatureCell.tsx`),
  and that asymmetry is forced rather than chosen: the List's columns are built inside a
  `useMemo` that must stay stable (config identity keys every memo in the collection layer), and
  this repo's `react-hooks` v7 ruleset rejects even *referencing* a ref-reading function from a
  memo body — `writeDeal` reads five refs. Passing the callback, a latest-ref indirection and
  `useEffectEvent` were each tried and each rejected, the last with "cannot be assigned to a
  variable or passed down". An absent provider means read-only, so a future host that lists deals
  without a writer cannot advertise editing it cannot do. `deal_temperature` joins `DealPatch`
  but never `formFields`: it has its own one-click control, so it stays out of the sheet's dirty
  comparison, and the sheet's row renders **unconditionally** for the same reason #128's Owner
  row does. Rank 2 of #130's Today ladder was reserved for the hot+stale
  follow-up, and **#131 has now filled it** — reading `deal_temperature = 'hot'` in plain SQL
  beside `LAST_TOUCH_SQL`, and rendering this component read-only (no `onCycle`) in the panel's
  badge slot, because that panel is a list of what needs you rather than a place to re-triage
  the pipeline.
- **Dashboard Top deals are qualified-only and probability-weighted** (#240):
  `get_dashboard_stats`' `top_deals` keeps open, live deals past `lead`
  (`TOP_DEALS_QUALIFIED_D`) and orders them by `TOP_DEAL_SCORE_SQL`, `max(value,0) × (0.4
  + 0.6 × clamp(probability/100))`, then `updated_at`, then `id`. It is an attention
  score, not expected value: value stays the base, so $500K@20% beats $150K@99%, and a $0
  deal sorts behind every deal that has a value. The score runs in SQL BEFORE the `LIMIT`,
  because it is not monotonic in value and a value-ordered prefix would drop exactly the
  deals it exists to surface. The proactive digest's own top list
  (`proactive.collect_digest`, by `lead_score`) is a different question and is untouched.

- **Never build a `Date` from a TIMESTAMPTZ with the bare constructor** — use
  `crm/gtd/util.parseUTC` (#125). Two reasons, and the one this was originally filed under is
  **false**, recorded here so nobody re-derives it: every such column is written from
  `datetime.now(timezone.utc).isoformat()` and so carries **six** fractional digits where
  ECMA-262 defines three, and the claim was that JavaScriptCore rejects the extra ones, leaving
  a column showing "—" in Safari. Measured against WebKit 26.5 and the system `jsc` during #125's
  evidence run, it does not — the bare constructor parses that string correctly, and no column
  was ever broken there. What survives is that more than three digits is implementation-DEFINED
  rather than guaranteed, so the bare constructor bets on behaviour the spec does not require;
  and that a zone-LESS timestamp is read as LOCAL by the constructor and as UTC by `parseUTC`,
  a real divergence on every engine. The rule also covers SORTING, where the reason is
  engine-independent: a TIMESTAMPTZ is not lexicographically ordered, because the zone may be
  spelled `Z` or `+00:00` and `Z` sorts after `+`, so one instant written two ways compares
  unequal — sort on the parsed instant, with unparseable input yielding `null` so it sinks under
  the null convention rather than poisoning comparisons with NaN. A date-ONLY `YYYY-MM-DD` is
  the exception and keeps the local-parts constructor: it is a calendar date, and reading it as
  UTC midnight renders a day early west of Greenwich. **A test here must pin the zone-LESS case
  to be falsifiable at all** — every engine parses a microsecond string either way, so the
  obvious test passes against the code it is meant to reject.
