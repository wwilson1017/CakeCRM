# Todos and GTD mode

> Topic doc split out of `AGENTS.md` (former Product Rules). `AGENTS.md` keeps the enforceable
> invariants; this file is the full implementation record, moved verbatim. Add new
> implementation notes ("landed #N as …", design reasoning, divergences) HERE, not in
> `AGENTS.md`. Phrases like "the CRM bullet above" or "see the X bullet" refer to the
> rule bullets this file opens with, or to the topic doc that holds that rule; find
> other areas' docs through the Topic docs table in `AGENTS.md`.

- **Todos have two modes over ONE store** (#70), and **GTD is the default** (#102).
  `crm_meta.todo_mode` is `normal` or `gtd`; GTD is a presentation + tool surface over the *same* `todos` rows, never a
  second table — which is what keeps the dashboard counts, contact/deal rollups,
  `crm_get_stale_deals`' open-follow-up check, the heartbeat nudge and the CRM reset
  aware of GTD todos, and makes switching modes a **no-op** (nothing migrates,
  instantly reversible). `todos` gained `status` (7 GTD values), `star`, `context`,
  `tags` (JSONB — deliberately unlike `contacts.tags` TEXT, because the facet filters
  with `jsonb_exists`), `repeat` (+ `weekdays`/`every:N`), `auto_star_on_due`,
  `project_id` → new `todo_projects`, `completed_at`, `source`; plus the one
  invariant that makes one store safe — a DB CHECK `completed = CASE WHEN status =
  'done' THEN 1 ELSE 0 END`. **Every todo write funnels through
  `service._apply_todo_update_cur`** (`SELECT status … FOR UPDATE` → write both
  columns together → `_spawn_next_todo_occurrence_cur` on a real done-transition), so
  `complete_todo`/`update_todo` are thin adapters and completing a repeating todo from
  the plain normal-mode checkbox still spawns its next occurrence. The spawn reads the
  POST-update row (clearing `repeat` while completing must not spawn) and takes ONE
  clock read shared with the auto-star comparison. The migration backfills BEFORE
  adding the CHECK, and `seed_data.py` DERIVES `status`/`completed_at` from
  `completed` — hand-writing either would break first-run seeding. GTD's `dropped`
  status is a soft delete that is neither done nor open, so `NOT_DROPPED_TODO(_T)` is
  swept across the seven open-todo query sites exactly like `LIVE_TODO_PREDICATE`;
  the is-the-CRM-empty counts deliberately do NOT filter it. `todo_projects` is
  FK-referenced by `todos`, so it MUST stay in both TRUNCATE variants. Tool surfaces
  SWAP by mode: `crm.gtd_tools.get_gtd_tools()` returns `([], {})` in normal mode (the
  `get_gmail_tools` precedent) and `get_crm_tools()` hides its five todo tools in GTD
  mode — advertising both would give the model two vocabularies for one store — while
  executors stay reachable in both so a call proposed just before a flip still
  resolves. `crm_update_todo`/`crm_delete_todo` close the long-standing parity gap in
  BOTH modes. `identity.GTD_GUIDE` appends to the static prompt only in GTD mode (a
  rare, deliberate cache invalidation, same class as editing the personality — and since
  #102 that is the steady state for nearly every install rather than a flip-flop), and the
  heartbeat prompt names `todo_list` instead of `crm_list_todos`. Telegram gains a
  deterministic `capture …` intercept that runs BEFORE the model — zero AI cost, works
  with no provider configured.
  **Triage & edit-sheet parity** (#150) fixes four defects daily phone use exposed. A
  native `<input type="date">` ignores `placeholder`, so the empty due-date box carries an
  opaque **"Add due date" overlay**, cleared by a value or by focus — and by `onChange`
  too, because the write that follows a pick sets `busy`, React does not dispatch to a
  disabled target, and `onBlur` would therefore never run (the flag is invisible while a
  date is set and bites the moment it is cleared). **The date commits on BLUR, not on change**, and
  through its own write rather than `patch`'s. A date input reports a COMPLETE value the
  moment every segment parses, so it emits one on nearly every keystroke — `0002-12-24` after
  the year's first digit on an empty box, `2026-01-01` after the month's on a populated one —
  and `patch` sets `busy`, which DISABLES the input, so that first write ate every remaining
  keystroke and the truncated date was what reached the server, silently. Blur-committing
  means the field is never disabled while it still has focus, so what is written is what is on
  screen. The truncation predates this port and was found by the evidence run against the real
  app, which A/B'd it against `main`; the cue is what invites people to type in the box, so it
  is fixed alongside it. `useSerialCommit` is shared by the notes box and the date for the same
  reason both need it — two writes to one column, in flight together, land in whichever order
  the server picks — and a resolving write flushes both, so filing carries them. The trade-off
  commit-on-blur brings, measured rather than assumed: an entry that is never blurred is lost
  on a hard reload. Every path that ends the interaction inside the app — filing, Edit, Delete,
  promoting another card, Tab, clicking anywhere — blurs and commits, so only closing the tab
  with focus still in the box loses it. The same trade-off the notes box already makes. `pendingDue` mirrors the existing
  `pendingTitle` so the CONTROLLED field does not revert to the prop for the length of the
  refetch, and `current` — what the Edit sheet is handed — carries title, notes and date
  from the card's view, never the lagging prop. **The card renders its own view of the
  record, not the `todo` prop**, which lags: between a write being sent and the parent's
  refetch landing, the prop still describes the record as it was. That view plus the three
  optimistic overrides live in ONE `useReducer` (`CardState`), mirrored into a ref that
  `apply()` advances with the same pure reducer before dispatching. The row IS the notes
  baseline — dirty is `notesDraft !== row.notes` — because the only thing that moves the row
  is adopting a newer one, and a newer row is by definition what the server holds. Both halves are
  load-bearing. The reducer is what makes every decision read the state as it is NOW: these
  handlers run from asynchronous callbacks, and a callback closes over the render that
  created it, so a title save resolving after the user started typing notes would compare
  against the empty draft it captured, call the box clean, and **overwrite what was typed**.
  The synchronous ref is what lets a continuation read the card before React commits — the
  sheet payload is built from it when the sheet actually opens, never captured at click time,
  because opening waits on the notes flush and that flush can answer with fields someone else
  changed. `adopt()` takes whichever row is NEWER, ordered on `updated_at` via `isNewer` — a
  VERSION comparison, and it has to be: a write's response is newer than the prop the parent
  still holds, so comparing content would read that lagging prop as an outside change and
  rewind the notes box the instant a save succeeded, the next blur writing the pre-save text
  back over it. `isNewer` breaks a millisecond tie on the **fractional
  seconds**, because `Date.parse` truncates there while every todo write stamps `updated_at`
  from `datetime.now(timezone.utc).isoformat()` — microseconds — and equal means reject.
  Reading the fraction rather than the whole string is what keeps it independent of how the
  zone is spelled: `Z` sorts after `+`, so a lexical compare calls the same instant written
  two ways a newer version and adopts the card's own echo. **An override is released when its OWN write
  settles**, success or failure — never because an adopted row disagrees with it. A row is not
  evidence about a write still in flight, and the disagreement rule cannot tell "someone
  changed this elsewhere" from "this row was committed before my write was": an earlier write
  of the card's own, answering first, carries exactly that disagreement, so the rule flashed
  the field back to the value the override exists to hide and handed the Edit sheet the old
  one, whose full-row save then reverted the change. Releasing on settle also closes the
  original hole — a successful write pinning its own value for the life of the card, which
  `pendingTitle` had before this port. There is deliberately **no** "the write was
  acknowledged, so trust the text over the version" rule for notes: `_now()` is stamped under
  the row's own `FOR UPDATE` lock, so `updated_at` is monotonic per row and a held row newer
  than our response was committed AFTER our write. Either it already carries our text, making
  such a rule a no-op, or a later write replaced it — and there, marking the box clean would
  strand a paragraph the server does not have, silently. Leaving it dirty re-sends it, the
  same last-write-wins rule the unsaved-draft case follows. And `star`/`project_id`,
  written straight through and never rendered optimistically, stay current for the sheet only
  because every write path adopts the response it already gets back — `createAndAssign`'s own
  `project_id` write included; without that the sheet opens on pre-write values once `busy`
  clears and its full-row save reverts them. The known limit of ordering on `updated_at` is
  that JOINed columns (`project_name`, `deal_title`, `contact_name`) never bump it, so a
  rename made elsewhere reaches a mounted card only on the next reload — cosmetic, and the
  alternative is versioning rows this card does not own. Step 2 gains an inline **Notes** textarea
  committed on blur through `flushNotes`: single-flight with ONE trailing run (two writes
  to the same column, in flight together, land in whichever order the server picks), and
  deliberately NOT taking the card-wide `busy` — the click that files the item is what
  blurs the box, so a shared flag would swallow that very click. Any *resolving* write
  flushes it first, so the note is on the row before the item leaves the inbox, but is
  **never gated on the result**: filing is this card's one exit, and a note the server keeps
  rejecting would otherwise trap the item in the inbox forever. The textarea replaced the
  read-only preview under the title rather than joining it. Step headings move from
  `text-xs`/`text-muted` to `text-sm`/`text-charcoal` (`ck-ink`, the primary body ink
  `core/theme/inkContrast.test.ts` already pins at AA on `ck-card` in both themes). The
  edit sheet's Context field becomes a `<select>` over the known contexts plus a
  "+ New context…" hatch, matching the triage card: mobile browsers do not reliably render
  a `<datalist>` on a POPULATED text input, so the old picker was invisible until the field
  was cleared. Option values are **indices**, so a context literally named `__new__` stays
  selectable, and the SELECTED context always gets an option — the shared meta loads async
  and a refresh can drop a value, and a select matching no option while `save()` submits the
  hidden string is worse than an extra option. The one place this stays **simpler than
  the blueprint** is the commit primitive: the blueprint routes notes through a shared
  `useAutoSave` (its `shared/autosave`, reused across several surfaces), where this card has
  a local `useSerialCommit` — single-flight with one trailing run, queued on the **tail** of
  the chain so a third caller cannot wake alongside the second and fire a duplicate. Porting a
  shared cross-app primitive for two fields on one card was not worth it. The `updated_at` ordering was: an
  earlier cut of this port adopted only from the prop and compared content, and that produced
  the save-rewind, the pinned override and the stale-star defects the paragraph above
  describes.
  **#102 made GTD the default and the fail-safe.** Four readers resolve the mode —
  `service.get_todo_mode()` plus thin `_todo_mode()` wrappers in `assistant.identity`,
  `heartbeat.service` and `telegram.service` — and all four degrade to `gtd`, because a
  row we cannot read says nothing about what the user chose, so the honest guess is the
  experience a new install gets. A test asserts the four agree, so they cannot drift.
  **The migration's UPDATE is the whole mechanism, not a policy add-on layered on a
  default change — do not "simplify" it away.** `crm_meta`'s singleton row is inserted by
  the `crm_core` migration long before `todo_mode` exists, so `ADD COLUMN … DEFAULT` was
  consumed once at ADD COLUMN time and nothing ever inserts `crm_meta` again (every writer
  UPDATEs it; both TRUNCATE sweeps exclude it). Flipping only the column default would
  therefore change nothing on any install, fresh ones included — which is also why the
  issue's "fresh-installs-only, no backfill" option was unreachable without mutating an
  already-applied migration. `SET DEFAULT 'gtd'` is kept anyway so the schema does not
  contradict the product default for whoever next adds an inserter, and the integration
  test pins BOTH halves separately. Rows already at `gtd` are untouched; a user who
  deliberately chose `normal` in the four days since #70 is flipped once and re-toggles
  (Will's accepted trade on #102) — one click, since switching migrates nothing.
  The mode now has ONE owner in the UI: `CrmLayout` holds it and publishes both
  `TodoModeContext` and `TodoModeSetterContext`, so `TodoModeCard` writes through the
  setter instead of keeping a second copy. Before #102 the card's local state left the
  layout's context stale, so switching mode in Settings did not take effect on
  `/crm/todos` until a full page reload — which would have broken the very opt-out that
  makes flipping every existing install acceptable.
  Neither no-login surface consults `todo_mode` (they gate on `todo_capture_token` /
  `todo_web_enabled`), so this flip does not widen them.
- **A project's name and notes are click-to-edit on its detail page** (#232), through the same
  `InlineTitle` the triage card uses, whose `body` variant serves the notes (Enter is a
  newline; blur and Escape are the only exits; clearing notes is a real save; text is kept
  verbatim unless wholly blank). Notes always render, so a blank field shows an "Add notes…"
  placeholder — before #232 they could never be ADDED after creation. Each save PUTs only
  the field that changed (never `status`), an unchanged value sends nothing, and the page
  merges back only that field, since both editors can have a PUT in flight at once. A blank
  name is refused client-side: the editor closes on the previous name, since there is no typed
  text to keep, and a failure line says so. A server refusal (a duplicate name, its `detail`
  shown verbatim) keeps the editor open with the typed text. Either way there is ONE
  `role="alert"` line, TAGGED with its field, because both editors can be open at once and an
  untagged line would be cleared when the notes editor closes unchanged. Success calls `refreshMeta()`, which carries a rename to the Projects list,
  the todo rows and `TodoEditSheet`'s project picker.
- **A project carries a one-line purpose and outcome** (#262, port of todo-gtd `d6948f1`/
  `162dcb3`): `todo_projects.purpose`/`outcome`, `TEXT NOT NULL DEFAULT ''` like `notes` beside
  them (the issue said nullable; '' is the one unset state the service and UI already use for
  notes, so a NULL would be a second spelling of it). Both are in `PROJECT_FIELDS`, validated
  as ONE-LINE fields by `gtd_common.validate_line` (trimmed, `MAX_SHORT_CHARS`, every
  whitespace run — a newline included — collapsed to one space, so an API or tool caller cannot
  store a second line), and clearing either is a real save. They are edited on the project page
  by two `InlineTitle` editors under the notes in the new `line` variant (an `<input>`: Enter
  saves like a title, an emptied value saves like a body), through
  the same per-field `saveField` (which now merges back the server's stored value, since it
  trims), and shown READ-ONLY on the triage card under Step 2 once a project is picked —
  unlike the blueprint, which edits them (and an area, which CakeCRM has no table for) inline
  on the card. Read-only keeps every triage-card write a write to the todo. The assistant sets
  them through `todo_create_project`/`todo_update_project`, both still ROUTINE (neither field
  can hide a project), and `GTD_GUIDE` tells it to ask rather than invent them.
- **The project detail page steps to the previous / next project** (#264, port of todo-gtd
  `382ce21`): ‹ › links beside "All projects", and the Left / Right arrow keys, cycle with
  wrap-around through the projects sharing the viewed project's status. `crm/gtd/projectNav.ts`
  holds both rules. The order is the shared meta cache's, which is the same `listProjects()`
  read the Projects page renders with no `sort` declared, so a status filtered out of it IS that
  tab's order (`lower(name)`, a total order) and nothing re-sorts it; the Projects search box
  is deliberately not applied, since a stale query would silently shrink the cycle. The key
  handler ignores chords and any key aimed at an `<input>`, `<textarea>`, `<select>` or
  contenteditable element (the inline editors, the add box, Quick Add), and stands down while
  the edit sheet is open. Each link's accessible name names its destination ("Next project:
  Delta"). The page body is keyed by the route id, because moving between projects keeps the
  route matched and would otherwise carry the previous project's state, failure line and
  drafts onto the next until its fetch landed.
- **Marking a todo done and filing an inbox item are undoable for 7 seconds** (#231).
  `crm/gtd/undoQueue.ts` is a module-level store (the `shared/toast` shape), because
  `TodoShell` unmounts on every tab switch and component state could not survive the
  navigation; `UndoPill` reads it through `useSyncExternalStore` so a row whose timer
  lapses during a remount can never be served stale. Every write path that marks done
  queues a row carrying the PRE-write status (`useRowActions.toggleDone`,
  `TriageCard`'s `patch`, `TodoEditSheet.save`), and every later write that moves the
  todo off where the queued write put it calls `dropUndo` — un-checking, `setStatus`,
  a sheet save that changes the status, a sheet re-file under another context while a
  FILING is pending, and delete. That last rule is what stops the pill offering to redo
  a decision the user has since made themselves. A filing restores status AND context
  (an item that arrived as "@errands" from Quick Add goes back to "@errands"), and the
  revert asks the Inbox, via `notifyTodosChanged(focusInboxId)`, to promote it to the
  current triage card — clearing the filter bar, and re-keying the card with `focusSeq`
  so a spent card is never reused. A completion made from triage restores `inbox` too,
  so it gets the same promotion — the focus keys on where the revert lands, not on the
  kind. Pages that own their own fetch (Search, Projects, Project
  detail, Review) subscribe with `useTodosChanged`; everything on `useTodos` gets it
  free. **A new GTD list page that does not go through `useTodos` must call
  `useTodosChanged(reload)`**, or an undo clicked while standing on it leaves it stale.
  Rows are stamped with the session token they were queued under and a row from another
  session is neither shown nor reverted, because `AuthContext` adopts a token another tab
  broadcasts with no reload. The flow is described to users in the
  `backend/help/content/todos/gtd.md` topic.
  Undoing a repeating todo leaves its spawned successor in place and says so. There is
  no undo endpoint: the revert is the ordinary PUT, so the no-login `/todo/{token}` app
  gets the feature too.
  **Where the block renders has two answers, from one queue** (#265, port of todo-gtd
  `162dcb3`): every page gets the floating bottom-right block `TodoShell` mounts, except
  the Inbox, which passes `inlineUndo` to suppress it and renders `<UndoPill inline />`
  in its own flow between the triage card (or the empty state) and the queue — where the
  eye already is after filing. Same rows, timers, accessible name and buttons; an inline
  block publishes no `--ck-toast-bottom`, since it is not in the corner stack. While the
  Inbox's edit sheet is open the page hands back to the floating copy, because the sheet's
  `z-50` overlay would cover an inline row whose 7s window keeps running.
- **The capture page puts the caret in the box on open AND on resume** (#233, port of
  cake_os #3049). `autofocus` is one attempt at parse and never repeats, and a resumed
  home-screen app is not re-navigated, so the inline script in `crm/todo_capture._CAPTURE_HTML`
  retries `focus()` over the next frames (now, next rAF, 150 ms, 400 ms) and re-attempts on
  `pageshow`/`visibilitychange` **only under a standalone launch** (`navigator.standalone` or
  `(display-mode: standalone)`), so a browser tab never has its caret grabbed when the user
  switches back. No attempt takes focus from an element the user is already in (after a resume
  that could be Send). On a coarse pointer it also calls `navigator.virtualKeyboard.show()` —
  including when the textarea ALREADY has focus, since a resumed app usually does with the
  keyboard dismissed, so "focused" and "keyboard up" are separate questions. Best effort: WebKit
  lacks the API and iOS raises the keyboard only under user activation, so a cold iOS launch
  still lands with the caret placed and the keyboard down; a tap-to-start overlay is ruled out.
  **Tested by running the page's real script**: `vitest.config.ts` defines
  `__CAPTURE_PAGE_PY__` from the Python source (the `__INDEX_CSS__` pattern) and
  `frontend/src/crm/capturePageFocus.test.ts` executes the extracted `<script>` in jsdom — the
  repo's only JS runner — so the rules are pinned by behaviour rather than by source text.
- **Three GTD list pages run on the shared collection layer, and three deliberately do not**
  (#234, port of cake_os #1840's GTD half). **Someday** and **Done** are flat record lists
  rendered through `CollectionView` with ONE list column whose cell is the existing `TodoRow`
  (Someday's cell adds its `→ Next` button), so the checkbox, star and click-to-edit are
  unchanged; **Projects** is the layer's `cards` view with the page's own
  `components/ProjectCard.tsx` supplied through `renderCard`. **Inbox, Next Actions and Waiting
  stay bespoke** on `shared/search`'s `SearchFilterBar` (page-owns-state, `contextFacet.ts`):
  each depends on SECTIONS — Inbox is a triage instrument with one promoted card, Next Actions'
  context batching is the page's purpose, Waiting is two fixed sections from two fetches — and
  the layer's list view is a flat table with no sections primitive. That split is not a to-do;
  if the layer ever gains list sections, those three become ordinary adoptions. The configs
  live in `crm/gtd/collectionConfig.ts` and follow the layer's rules: no `sort` (no page ever
  had a sort control, and Done's newest-finished-first order is the SERVER's under a LIMIT),
  no `detail` (rows open `TodoEditSheet` themselves, a card is a real `<Link>`), and a context
  facet whose getter and options are both lower-cased through the ONE `contextOptions`
  mapping the bespoke pages use. Storage keys (`todo_someday`, `todo_done`, `todo_projects`)
  are constant rather than owner-scoped as upstream's are, because this store is install-wide:
  every seat and every `/todo` link reads the same list. Done's done|dropped toggle and
  Projects' status tabs stay page-owned FETCH keys, never facets. Each page still renders its
  own GTD `EmptyState` for a genuinely empty list and mounts `CollectionView` only when there
  is something to filter, so the layer's `emptyState` message only ever means "your filter
  hid everything". **The project card is a stretched link**: before #234 the card WAS the
  `<Link>` with its Complete button inside it, held back by `preventDefault` — interactive
  content inside an `<a>` is invalid and announces the button as part of the link. Now the
  link is an absolute overlay and only the button opts back into pointer events.
  **The no-login `/todo` app passes `savedViews={false}`** (a new optional `CollectionViewProps`
  switch, default on): the saved-views menu calls `/api/saved-views` through `api()`, whose 401
  sends the tab to `/login`, and saved views are team data an anonymous link must not touch.
  Signed in, the three surfaces get team saved views like every other collection surface. The
  public app's download grew by the layer and `@dnd-kit` — the measured delta is in
  `docs/agents/frontend-boot-split.md`.

- **Every row says who added it unless a person did** (#260). `todos.source` already
  recorded provenance; `crm/gtd/sourceLabel.ts` maps it to a visible word (`agent` →
  Baker, `observer` → Observer, `capture_web` → Capture link, `telegram` → Telegram) and
  returns null for `ui` and for any value it does not know, so a row never claims a
  provenance the server did not record. `components/SourceLabel.tsx` renders it on
  `TodoRow`'s meta line (so Today, To Do, Search, project pages, Someday and Done), the
  `TriageCard` line under the title, and the three bespoke rows that do not use `TodoRow`
  — the inbox's remaining-queue buttons, Waiting's rows and Review's stale list: a plain `<span>`
  (the row body is a `<button>`, so nothing interactive may nest there), named by a
  visually hidden "Added by " inside it rather than an `aria-label` (#162's rule), and
  text-only in `text-muted` with a border — no `tint()` background, so
  `inkContrast.test.ts` owes nothing. The observer previously stamped `agent`, the same
  value as Baker's `todo_create`, so a migration widened `todos_source_check` with
  `observer` and `memory/observer.py` now writes it; there is no backfill, because the
  only marker on an old observer row is free text in notes a user may have edited, and
  "Baker" is still true of it. Baker's two create tools (`todo_create`,
  `crm_create_todo`) now get `source='agent'` from `bind_server_args`, the same binding
  that already supplies `owner_id`, which DROPS a model-supplied `source` — tool arguments are not schema-validated at runtime, and `crm_create_todo`
  used to forward one straight into the INSERT (and otherwise stored `ui`, mislabelling
  Baker's normal-mode todos as a person's). The #204 fence is unaffected: it keys on
  `source='capture_web'` alone (`delimiters.PUBLIC_CAPTURE_SOURCES`), which no change here
  writes or widens.
- **On a phone the no-login `/todo` app gets a fixed bottom tab bar** (#266, port of
  todo-gtd `614faa2`): Inbox · Today · To Do · Projects · More, with Contexts, Waiting,
  Someday, Review and Done behind More. **Public mode ONLY** — `TodoShell` mounts
  `components/BottomBar.tsx` only when `isTodoPublicMode`, because under `CrmLayout` the
  bottom of the screen already belongs to the CRM's own mobile nav and the "Ask Baker"
  launcher; the CRM mount keeps its top strip unchanged, pinned by a test in both
  directions. Below `sm` the bar replaces the strip and from `sm` up it is `display: none`
  — CSS breakpoints, not a media-query hook, so crossing one never remounts a page. The
  bar carries `ck-has-bottom-bar`, which `index.css` reads with `html:has()` (phone widths
  only, since the hidden bar stays in the DOM) to set `--ck-bottom-bar` — its height plus
  `safe-area-inset-bottom` — and `--ck-stack-floor`, where the fixed bottom-right stack
  rests: 88px clears the launcher everywhere else, and on a phone with the bar it rises to
  `max(88px, bar + 12px)`. The undo block and `ToastViewport` both read that floor, which is
  why neither can land on the bar on a notched phone, and the public page's bottom padding
  and `scroll-padding-bottom` read `--ck-bottom-bar` so the last row and a keyboard-focused
  control stay above it. Not ported from the blueprint: its priority-plus top menu and the
  optional sidebar (`menuLayout`, a Connect > Appearance setting this repo has no page for),
  and its Contexts tab in the primary row — the issue names four primary lists, and 56px tap
  targets fit five cells at 390px with room for the inbox badge. Labels match the top strip
  (`To Do`, not the issue's "Next"), since one list should not carry two names.
  iOS reports every safe-area inset as 0 unless the page opts into `viewport-fit=cover`,
  so `todo_web._page` adds it to the served shell's viewport meta — for THIS surface only,
  since the CRM pads no insets and would slide under a landscape notch — and the public
  wrapper pads `max(1rem, safe-area-inset-left/right)` for the same reason.
- **Baker can run the weekly review** (#263, port of todo-gtd `mcp_server.py`'s
  `todo_weekly_review`). Three pieces, each in the layer the issue assigned it. The DATA is
  `todo_weekly_review`, a GTD-only `writes:False` read over `gtd_service.weekly_review()`:
  inbox, due today or overdue, stale next actions (14 days untouched, the Review page's
  `STALE_DAYS`), waiting-fors due a follow-up (7), someday items older than 30 days,
  completed in the last 7 days, and active projects with no next action — each a full
  `count`, a `truncated` flag and up to 25 `{id, title, source, days}` items. The payload is
  pinned BY TYPE in `tests/test_crm_weekly_review.py`, because the tool is
  background-callable. Each item keeps `source` on purpose: it is what lets #204's
  `fence_public_rows` find a capture-page title, and it is why the tool must NOT join
  `UNTRUSTED_SOURCE_TOOLS` (that set is the background exclusion list). The SCRIPT is the
  help topic `todos/weekly-review` — content Baker may read, never static prompt — and
  `GTD_GUIDE`'s weekly-review paragraph became a one-sentence pointer to it. The HABIT is
  `crm_meta.todo_last_review_at`: the Review page shows "Review due" when it is NULL or a
  week old, and **Mark review done** (`POST …/review/done`) stamps it. Baker reads the clock
  but cannot stamp it — unlike the blueprint, where starting a review quiets the hint —
  because a read that writes would let a heartbeat turn mark a review nobody did. The
  "Start my weekly review" chip rides the existing page seam rather than a second
  publisher: `PageContext` is now a discriminated union of `SettingsPageContext` and
  `TodoReviewPageContext` (`page: "todo_review"`), derived from the URL in
  `assistant/pageContext.pageContextFor`, with a fixed `identity.TODO_REVIEW_PAGE_NOTE` in
  the volatile half naming the tool and the topic. The chip is gated on `ai_ready` for free:
  it lives in the drawer, which only exists when AI is ready.
- **A todo can carry a bring-back date** (#261, port of todo-gtd `d6948f1`/`60b2d0b`):
  nullable `todos.bring_back_on DATE`, written ONLY through `service._apply_todo_update_cur`
  (in `_TODO_UPDATE_FIELDS` and GTD's `TODO_FIELDS`; `gtd_common.validate_bring_back`
  clears '' to NULL). No create path takes it, and a repeat's next occurrence does not
  inherit it. `service.brought_back_sql(column, params)` is the one "not waiting on a
  bring-back date" predicate; it binds `gtd_common.today_local_str()`, the same day #259
  made the GTD client use. It is applied to the WORKING lists and their counts — GTD
  `list_todos` (except a search, which is how a deferred todo is reached before its day),
  normal `list_todos` (open rows), `today_view`, the dashboard's overdue/pending counts, the
  proactive digest and the Today panel — and deliberately NOT to record views and follow-up
  checks (contact rollup, company report, deal health / stale-deal `has_open_todo`, a
  project's open count, the observer's dedupe): a scheduled return is still a follow-up.
  The same split reaches the GTD client through `GET /todos?include_deferred=true`: the
  project page and the stalled-project checks on Projects and Review pass it (a deferred
  next action still covers its project; Review drops it from the stale list instead), and
  `get_filters`' open-status counts apply the predicate so the Inbox badge never counts an
  item the Inbox hides.
  **Divergence from the blueprint:** upstream only SURFACES a revisit date on Today; here the
  todo is also hidden until then, per the issue. From its day it stays on Today (the
  "Brought back" section) until completed or cleared — no sweep job, same as upstream. Only a
  due date can make a todo overdue. The control is a native date input on the edit sheet
  (existing todos only, committed with Save) and on the triage card (the #150 blur-commit,
  its own `useSerialCommit`, flushed by a resolving write, carried in `payload()`); a row
  shows "Back <day>" while waiting and "Brought back" from its day. `todo_update` and
  `crm_update_todo` stay routine, but a call that SETS a bring-back date keeps its Approve
  card (`confirm_tier._HIDING_ARGS`, beside `status='dropped'`): it takes the todo off
  every working list with no upper bound on the date. Clearing one stays routine.

- **The two no-login todo surfaces are asymmetric, and only ONE of them is opt-in** (#70,
  ported from chatty — the heading used to say both were, which the body below has always
  contradicted). Neither consults `todo_mode`, so #102's default flip leaves both exactly
  as they were. `/capture[/{token}]` is **write-only** (creates one inbox row, returns only
  its id — no read endpoint exists on it) and is reachable while no token is set;
  `/todo[/{token}]` serves the **whole todo app read+write** and is **off** until
  `todo_web_enabled`, which mints a token in the same action rather than publishing
  the list at a guessable address. Both mount ONLY `gtd_router.build_router` — the
  token reaches todos and nothing else — and both carry `core/ratelimit.IPRateLimiter`
  (a separate strict budget burned only by WRONG tokens), 404-never-401,
  `hmac.compare_digest` on **bytes** (a non-ASCII probe must be a 404, not a 500),
  `no-store` + `noindex` on every response *including* the unbuilt-frontend 503, and a
  body-size check BEFORE JSON parsing. All four routers mount before the SPA catch-all
  in `main.py` or the catch-all swallows them. Tokens are clamped to `[A-Za-z0-9_-]`
  and rejected if they equal a page slug (`RESERVED_TODO_WEB_SLUGS`) — `todos` is
  reserved because `/api/todo-web/todos/…` would otherwise shadow the API mount.
  Documented in SECURITY.md.

- **The link tokens never reach a log line the app writes** (#267, ported from todo-gtd's
  `logscrub.py`). `core/logscrub.ScrubTokens`, installed by `main.py` right after
  `basicConfig`, replaces the segment after `/todo/`, `/capture/` or `/todo-web/` with
  `<redacted>` in a record's message, its tuple or dict args and its rendered traceback.
  It sits on the five server loggers by NAME (`uvicorn.access`/`.error`/`.asgi`, the last being
  the trace-level ASGI-scope dump, and `gunicorn.access`/`.error` — a logger filter survives
  gunicorn's UvicornWorker swapping their handlers) and on root's HANDLERS, because a logger filter never sees a record
  propagating through it, which is how every `getLogger(__name__)` line gets out. Two rules
  worth knowing: a template that spells the path itself (`"refused /todo/%s"`) is rendered
  before it is scrubbed, since scrubbing the template alone eats the `%s` and breaks
  formatting (the blueprint has that bug); and a token logged WITHOUT its path prefix is
  invisible to it, so never log a bare token. It redacts the segment whether or not it is a
  token (`/todo/manifest.webmanifest` reads `/todo/<redacted>` too). `tests/test_logscrub.py`
  serves `main.app` through a real uvicorn server, since a TestClient writes no access line,
  and attaches its capture straight to the uvicorn loggers without re-running `install()`, so
  the suite fails if `main` stops installing it. Railway's edge HTTP log is out of its reach;
  SECURITY.md says what that log keeps and names the cookie redesign as the follow-up.

- **The follow-up feature is Todos in every layer** (#169), and "task" is not a synonym
  for it anywhere: tables `todos`/`todo_projects`, column `crm_meta.todo_mode`, REST
  `/api/crm/todos*` + `/api/crm/todo-mode`, SPA `/crm/todos*`, `crm_*_todo(s)` tools in
  normal mode and `todo_*` in GTD mode. In this tree the word "task" is reserved for
  asyncio/queue/scheduler machinery, the `memory_facts.memory_type` taxonomy value,
  frozen migrations and plain English — so reintroducing it for the feature re-splits the
  vocabulary the rename closed. The ONE deliberate survivor is
  `<Route path="tasks/*">` in `App.tsx` (`crm/legacyTodoRedirect.tsx`), which redirects
  every pre-rename URL; there is no REST or tool-name compatibility shim, by decision,
  because the frontend is the only consumer of both.
- **Every GTD "today" on the client is the INSTALL's day** (#259, port of todo-gtd's
  list-timezone fix). `today_view` picks rows by `TIMEZONE`, but the page used to split them
  into Overdue vs Due today on the BROWSER's date, so an evening user in another zone saw
  today's todos filed as Overdue; the row chips and Quick Add's "tomorrow" split the same way.
  `gtd_service.get_filters` now returns `tz` (`localtime.tz().key`, so a bogus `TIMEZONE`
  reports the same UTC fallback the server uses), and `gtd/util.ts` derives the day from it:
  `todayStr(now, tz?)` (built from `Intl.DateTimeFormat` parts, browser date when `tz` is
  absent or unknown to the engine), `zonedNow(tz?)` (a Date whose LOCAL fields are that day,
  at noon, for date math written against local getters — the quick-add parser), and
  `formatDay(iso, tz?)` for the edit sheet's Created/Completed dates. Components that need
  only the zone read it with `useTodoMeta.useListTz()`, a `useSyncExternalStore` over the
  shared meta cache that never fetches (every GTD page sits inside `TodoShell`, whose
  `useTodoMeta` does). **The rule: a new client-side day decision in GTD takes
  `todayStr(new Date(), useListTz())`, never a bare `todayStr()`.** The public `/todo/<token>`
  app inherits it, since `build_router` serves the same filters route there. `dueLabel`'s
  "Tomorrow" moved from `+24h` to `day + 1` in the same pass, because the day the clocks
  change is 23 or 25 hours long. `listTimeZone.test.tsx` pins the issue's acceptance case
  verbatim (browser zone switched to UTC through `process.env.TZ`, install in Chicago, 23:30)
  and the opposite direction (install in Tokyo, runner in Chicago).
