# Dashboard Today panel

> Topic doc split out of `AGENTS.md` (former Product Rules). `AGENTS.md` keeps the enforceable
> invariants; this file is the full implementation record, moved verbatim. Add new
> implementation notes ("landed #N as …", design reasoning, divergences) HERE, not in
> `AGENTS.md`. Phrases like "the CRM bullet above" or "see the X bullet" refer to the
> rule bullets this file opens with, or to the topic doc that holds that rule; find
> other areas' docs through the Topic docs table in `AGENTS.md`.

- **The dashboard leads with a Today panel** (#130, `backend/crm/today_service.py` +
  `GET /api/crm/dashboard/today` + `frontend/src/crm/{todayPanel.ts,components/TodayPanel.tsx}`):
  ONE ranked list of what needs the viewer today, above the stat row, capped at 5 with a
  "+N more today" expander. Pure SQL and pure Python — identical with zero AI providers.
  The ladder is `1 starred · 2 hot+stale deals · 3 overdue · 4 due today`, plus an
  **unranked tail**. Rank 2 was reserved by #130 and emitted by nothing until **#131**
  filled it, which is what let that land as an insertion rather than a renumbering of
  every rank below it. **#188** then removed reminders, which had held rung 4, and
  renumbered due-today into it — the numbers are a wire contract, so the client's
  `CrmTodayTodoItem.rank` union moved with them.
  **#131's rows are the panel's third source, and the one rule worth stating is that its
  second bucket carries NO rank at all.** Eligibility is one human-set column — #125's
  `deals.deal_temperature = 'hot'`, so a never-triaged deal is never hot and there is no
  `lead_score` threshold and no Settings knob. A hot deal that has also gone **stale** takes
  rank 2, at most `HOT_DEAL_SLOTS` (2) of them, most-idle first: the issue's anti-flood cap,
  because a neglected pipeline must never bury the calls and promises the user made
  themselves. A hot deal touched **recently** must appear "only in the +N more today expanded
  list", and a sixth rung cannot enforce that — the collapsed card slices the first five
  ITEMS, so one overdue todo beside one recent hot deal would put both on screen. So those
  rows are emitted with `rank: null` and `collapseToday` fills its five visible slots from
  **ranked rows only**; `hiddenCount` is then computed the same way whether or not the card
  is expanded, since it answers "what would the expander reveal" and so drives the Show less
  control too. A payload of nothing but unranked deals therefore reads "Nothing needs you
  today." above a live expander — the empty line keys on `visible.length`, not on
  `items.length`. Stale rows past the two slots are **demoted into that same tail rather than
  dropped** (a panel silently hiding the most neglected deals in the CRM is the worse
  failure), and they keep `why: 'hot_stale'`: `why` describes the deal, `rank` decides the
  slot.
  **Staleness is imported, never re-typed** — `LAST_TOUCH_SQL`, `OPEN_PREDICATE_D`,
  `LIVE_PREDICATE_D` and `DEFAULT_DEAL_STALE_DAYS`, the same four
  `analytics_service.get_stale_deals` uses, so the Today panel and the "Needs a touch" list
  cannot call one deal stale and fresh. The test is evaluated **in SQL** and projected as
  `is_stale` rather than recomputed in Python from a day count: `FLOOR(epoch/86400) >= N` and
  `last_touch < now() - N days` disagree at the exact boundary instant, and two definitions
  of one word is what the imports exist to prevent. Ordering is exact `idle_seconds`, not
  whole days — 14d1h and 14d23h floor to the same integer, and with only two slots the id
  would otherwise decide which deal a rep actually sees — and that key is deleted before the
  row is emitted — an implementation detail, not payload. Because stale means idle past the threshold, a fresh deal can never out-idle a
  stale one, so the tail needs no second sort key.
  Deal rows wear #125's `DealTemperatureIcon` **read-only** (no `onCycle`, so it renders a
  labelled `role="img"` span rather than a button — a tab stop there would sit inside the
  row's own open-the-deal button) plus `idle 12d · $30K`, and they open the board through
  `crm/dealDeepLink.ts`, becoming the first in-app consumer that module's docstring was
  waiting for. There is deliberately **no server-side `url`**: #145's rule is about agent
  TOOLS (`with_deal_url` is applied in the executors) and no REST deal surface carries one.
  `whyBadge`'s parameter type EXCLUDES deals so the compiler proves it is never asked about
  one — with `CrmTodayItem` it would answer from its `default` branch and label a deal
  "DUE TODAY". `_fetch_hot_deals` also made `test_crm_deal_links` scan `crm.today_service`,
  and its detector self-test now names one reader per scanned module: dropping a module from
  that tuple used to be silent, because the classification test flags only the names it
  FOUND and left unclassified.
  **One clock, and that is the mechanism, not a convention:** `get_today()` reads
  `gtd_common.today_local_str()` — the identical call `gtd_service.today_view()` makes —
  and `next_refresh_at` is derived FROM that captured day via the new
  `core.localtime.local_day_bounds()`, never a second clock read, so a request crossing
  local midnight cannot bound todos to one day and arm the client's reload for another.
  Those bounds are
  computed in Python rather than with SQL's `AT TIME ZONE` so `zoneinfo` stays the single
  timezone authority (Postgres ships its own tz database, and two copies of one rule drift).
  Building this exposed a real split it would otherwise have sat beside, so **#130 also
  moved three UTC-day decisions onto the configured day** — `get_dashboard_stats`
  (the "Overdue todos" stat inches below the panel), `analytics_service.get_deal_health`
  (whose comment cited the dashboard as precedent) and `proactive.collect_digest` (which
  told a 6pm Central reader that tomorrow's todos were due today). One defect, three
  sibling sites, one sweep; Weekly Touches deliberately stays UTC. There were no existing
  assertions to update — none of the three pinned the day at all, which is why the split
  survived — so `test_crm_today.py` adds deterministic ones and keeps all three together,
  because "is this todo overdue" must not depend on which report asked.
  **Owner scope is a panel-local Mine/Everyone control**, hidden on a single-seat install:
  the issue named `OwnerScopeToggle`, but #77 deleted it and its replacement is a
  list-page facet, wrong shape for a compact card. Mine sends `owner_id=<me>`, Everyone
  omits the param (the `list_todos` idiom — no flag, no magic value), and the todos
  predicate is deliberately WIDER than `list_todos`' strict `owner_id = %s`:
  `(owner_id = %s OR owner_id IS NULL)`, because unassigned work must appear in "my" view
  — someone has to catch it — badged with `useUsers`' existing `UNASSIGNED_LABEL` (#128's
  label convention).
  Counts stay honest **structurally** rather than by a shared filter: the
  endpoint returns the FULL ranked list uncapped (`today_view()`'s precedent) and the
  client derives both the five visible rows and the "+N" from that ONE array, so there is
  no rows-vs-COUNT seam to disagree across. The ceiling is stated in the module docstring;
  the upgrade path is a probe-row `truncated` flag (#56's idiom), not pagination.
  The ladder is a **pure function** (`build_today_items`) with no I/O and no clock, so it
  is unit-tested with zero mocks while SQL keeps only membership. On the client, the panel
  re-sorts nothing — it renders `items` in server order — and even due labels compare
  against the payload's own `date`. It reloads on the **server's** next midnight, carried
  as an absolute `next_refresh_at`: `useLocalDay` fires at the BROWSER's midnight, which
  on a default install (`TIMEZONE` unset ⇒ server on UTC) is hours away, so keying on it
  would strand a tab on yesterday's list — the very failure that hook exists to prevent,
  merely relocated. That timer re-arms on a monotonic tick for #77's bail-out reason.
  Todo rows navigate to `/crm/todos` because **no todo-detail URL exists** (the route is
  mode-routed); the row's complete-checkbox is how you act on one, and it calls the normal
  `PUT /api/crm/todos/{id}/complete` so `_apply_todo_update_cur`'s invariants (repeat-spawn,
  the GTD CHECK) hold for free. Checkbox and row-open are **sibling** controls, not a
  button nested in a `role="button"` row: nesting puts a control inside a control for AT,
  and Space on the checkbox would bubble a keydown and navigate away, which an `onClick`
  `stopPropagation` cannot prevent. Badges are text-only in existing tokens — deliberately
  no `tint()` background, which would owe an entry in `inkContrast.test.ts`'s surface
  registry (#68).
