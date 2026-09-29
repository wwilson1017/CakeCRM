# Todos and GTD mode

> Topic doc split out of `AGENTS.md` (Product Rules). `AGENTS.md` keeps the enforceable
> invariants; this file is the full implementation record, moved verbatim. Add new
> implementation notes ("landed #N as …", design reasoning, divergences) HERE, not in
> `AGENTS.md`. Phrases like "the CRM bullet above" or "see the X bullet" refer to the
> matching Product Rules bullet — find its topic doc through the index at the top of
> `AGENTS.md`.

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
