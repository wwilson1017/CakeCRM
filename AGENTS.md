# CakeCRM — Project Instructions

## What This Is

Free, open-source, self-hostable CRM with a built-in AI sales assistant. Seeded from
Chatty's product shell and agent engine; CRM features ported from the CAKE OS CRM;
assistant capability bar is Casey (CAKE OS's sales agent). **Multi-user since #60**
(accounts, admin/member roles, record ownership; assistant + channel isolation is
Phase B). PostgreSQL, FastAPI, React/Vite.
Deploy targets: `python run.py` locally (Postgres via Docker Compose), Railway
one-click in the cloud (the template provisions a PostgreSQL service).

- **Repo**: `wwilson1017/CakeCRM`, default branch `main`. **Public since v0.1 (2026-09-26).**
- **Local convention (Will's machine)**: this repo lives at `~/ai/CakeCRM`.
- **Blueprints**: CAKE OS at `~/ai/cake_os` (CRM features, Casey, auto-issues loop
  conventions), Chatty at `~/ai/chatty` (product shell, providers, agent engine,
  Telegram, Google integration). When a task says "port X", read the source there.
- **Planning docs**: `docs/CRM_OSS_PIVOT.md` and `docs/CAKECRM_ISSUE_SPEC.md` in the
  cake_os repo (branch `claude/chatty-crm-rebranding-a0ffsf` until merged) — the why
  and the what. The issue tracker here is the live spec; issues #1–#25 map to spec
  items S1–S25.

## Topic docs (read on demand)

This file is the always-loaded core. Detailed implementation records live in
`docs/agents/`; read the matching file BEFORE changing code in its area. These are plain
paths on purpose — never turn them into `@` imports, which load eagerly.

| When you are… | Read |
|---|---|
| touching Gmail (`backend/gmail/`, `backend/gmail_scan/`, `GmailCard`) | `docs/agents/gmail.md` |
| touching providers, the assistant engine/prompt/confirm tiers, background turns, memory/dreaming/observer, context files, compaction, the help library or playbooks (`backend/{providers,assistant,memory,dreaming,context_files,help}/`, `frontend/src/assistant/`) | `docs/agents/assistant.md` |
| touching users, roles, auth routes, ownership, conversation access or Settings visibility (`backend/users/`, `core/auth*`, `crm/settingsSections.ts`) | `docs/agents/accounts-and-ownership.md` |
| touching migrations, secrets, the login credential or deploy storage (`backend/migrations/`, `core/{postgres,secret_store,auth}.py`) | `docs/agents/database.md` |
| touching CRM records, deals/the pipeline board, contact/company forms, chatter + attachments, custom fields, lead score/temperature, touch counts, deal links, the Settings shell or theme tokens (`backend/crm/`, `frontend/src/crm/`, `frontend/src/shared/`, `index.css`) | `docs/agents/crm-core.md` |
| touching date-range facets or saved views (`shared/collection/dateRange*`, `backend/saved_views/`) | `docs/agents/saved-views.md` |
| touching the Contacts/Companies/Todos list pages or the corpus sweep | `docs/agents/list-pages.md` |
| touching Reports (`crm/report_service.py`, `ReportsPage`) | `docs/agents/reports.md` |
| touching the dashboard Today panel (`crm/today_service.py`, `TodayPanel`) | `docs/agents/today-panel.md` |
| touching todos / GTD (`crm/gtd_*`, `crm/todo_*`, `frontend/src/crm/gtd/`) | `docs/agents/todos-gtd.md` |
| touching routes, lazy loading or error boundaries (`App.tsx`, `Root.tsx`, `CrmLayout`) | `docs/agents/frontend-boot-split.md` |
| porting from cake_os or chatty, or asking where a feature came from | `docs/agents/source-map.md` (and `docs/SYNC.md`) |
| editing the logo or brand assets | `docs/agents/brand-mark.md` |
| touching `website/` (mycakecrm.com) | `docs/agents/website.md` |
| running the issue loop | `docs/AUTO_ISSUES.md` |

## Product Rules

- **The agent is a feature of the CRM, not the product.** If a capability doesn't
  make the CRM or its assistant better, it doesn't ship. One built-in assistant —
  no multi-agent roster, no training mode, no knowledge-import adapters.
- **The CRM must be fully usable with zero AI keys.** Every AI feature degrades
  gracefully when no provider is configured — hidden affordances, never errors.
- **Gmail is read + draft only, forever.** There is no email-send tool anywhere in
  the codebase, and none may be added. Google scopes can't express "draft but not
  send", so the guarantee is enforced at the tool layer: the registry exposes read
  and create-draft tools only. This is a documented trust guarantee (SECURITY.md).
  Invariants: scopes stay `gmail.readonly` + `gmail.compose`; the tools stay
  `gmail_search`/`gmail_read_thread`/`gmail_create_draft`; `backend/tests/test_gmail_guard.py`
  fails CI on any send surface and `gmail/client.py` keeps a runtime op allow-list — new
  read fidelity goes inside an existing approved op, never a new op or scope. The tools are
  offered only to admin seats (or every seat with `share_with_all_seats`) and NEVER to an
  unattended turn; Gmail reads are untrusted (`UNTRUSTED_SOURCE_TOOLS`, excluded from
  background turns). The HTTP transport is ours: `build(http=…)`, never `credentials=`, under a
  per-call budget, and `GmailTimeoutError` stays a plain `Exception` so the SDK never retries it.
  Full detail: `docs/agents/gmail.md`.
- **Multi-provider AI** via the `AIProvider` ABC (Anthropic, OpenAI, Gemini, Ollama,
  Together). Never call a provider SDK directly from feature code; SDKs are imported
  lazily inside methods, never at module top level; cheap background work uses the light
  tier via `resolve_tier_model()`; AI affordances key off `GET /api/setup/status`'s `ai_ready`.
  The assistant is **Baker — a brand, not a setting** (`identity.NAME`). The static prompt
  carries **no per-turn entropy** and its block ORDER is load-bearing (tests pin positions).
  Background turns get READ tools + `notify_user` only, minus `UNTRUSTED_SOURCE_TOOLS` — adding a
  connection-gated read means adding it to that set. A `ROUTINE` confirm tier never goes on a
  tool that notifies, deletes/archives/merges, bulk-writes or leaves the install. Protected
  context files (`soul.md`, `MEMORY.md`) always confirm and are admin-only writes on both doors.
  A PR that changes a user-facing flow updates its `backend/help/content/` topic.
  Full detail: `docs/agents/assistant.md`.
- **Accounts, roles and record ownership** (#60 Phase A). Authorization has exactly
  **two** enforcement points — `core.auth.get_current_user` and per-route `require_admin` —
  and `backend/tests/test_route_authz.py` pins the admin set in both directions. The JWT
  never carries a role. Dependencies/handlers doing blocking psycopg2 or bcrypt work are sync
  `def`. **Ownership is NOT access control** for CRM records: any member can edit any record,
  `owner_id` is nullable forever (`NULL` = unassigned), and owner filters live in the shared
  WHERE builders so a page and its COUNT agree. **Assistant conversations ARE access-controlled**:
  owner-only, no admin override, cross-seat answers 404; `history.py` takes a required
  keyword-only `user_id`. Settings card visibility is decided only in `crm/settingsSections.ts`
  (`adminOnly`) — never offer a control that can only 403.
  Full detail: `docs/agents/accounts-and-ownership.md`.
- **One database: PostgreSQL, and it's mandatory** — the backend refuses to start
  without `DATABASE_URL`. No Redis or other external services. New durable state defaults to
  a Postgres row; on Railway only `backend/data/` (the required volume) survives a redeploy.
  Schema is owned by `backend/migrations/*.sql`, applied at startup in lexicographic order —
  name migrations `YYYYMMDDHHMMSS_<name>.sql` (use `date +%Y%m%d%H%M%S`), never sequential
  prefixes. Access Postgres through `core/postgres.py` helpers
  (`pg_fetchall`/`pg_fetchone`/`pg_execute`/`get_connection`/`row_to_dict`). Every credential
  check routes through `core.auth.verify_password()`; login fails closed (503). The deploy
  pins `gunicorn --workers 1`, which in-process caches rely on.
  Full detail: `docs/agents/database.md`.
- **The CRM is first-class core** (`backend/crm/`, mounted at `/api/crm`; frontend
  `frontend/src/crm/` + `frontend/src/shared/`) — always-on, no enable flag. Invariants: free-text
  company names resolve through `crm.service.resolve_or_create_company_ids()` on every path.
  Every deal column write goes through `service._write_deal_update`; `_DEAL_USER_WRITABLE` is
  the default-CLOSED allowlist (never derived from `_DEAL_COLUMN_TYPES`; no `lead_score`, no
  `archived_at`). Every deal read carries `LIVE_PREDICATE` except the named opt-in holes — an
  archived deal must be findable and never money. FK'd and FK-less audit tables MUST stay in
  both `_truncate_all` sweeps. Every deal-returning agent tool returns a `url` via
  `crm.links.with_deal_url` (guarded by `test_crm_deal_links.py` — never a hand-written list).
  A new upload route needs a row in `main._ROUTE_REQUEST_LIMIT_SPECS`. Colours come from
  `--color-ck-*` tokens (FILL vs `-text` split, no literal fallbacks, no `opacity` on a chip
  container); a new `tint()` background under ink text joins `inkContrast.test.ts`.
  Full detail: `docs/agents/crm-core.md`.
- **Custom date ranges + team-visible saved views** (#181) are both built in
  `shared/collection`. `dateRangeFacet` is a factory over the existing `custom` facet kind —
  never a new kind. Saved views are the one server-side preference store (`backend/saved_views/`,
  table `saved_views`), deliberately outside both `_truncate_all` branches; only the creator or
  an admin may mutate a view, decided in the service under `SELECT … FOR UPDATE`. A view captures
  facets, search, sort and view mode — never toggles — and is stamped with
  `CollectionStorage.version` (see "Don't Do This" on bumping it).
  Full detail: `docs/agents/saved-views.md`.
- **The three list pages run on the #73 collection layer** (#77 — Contacts, Companies,
  Todos; the pipeline board sweeps too since #59). Pages sweep the WHOLE corpus and filter
  client-side — pagination is transport, never a second filtering model — on the keyset rules
  in "Don't Do This". Writes patch from the server's response body through
  `crm/usePatchableAssembly.ts` (merge patches); completing a repeating todo re-sweeps. The
  grouped and LATERAL last-activity twins in `get_pipeline` must change together (an
  integration test pins them). Contacts/Companies keep routed detail pages (the route is the
  selection); the local day comes from `useLocalDay`.
  Full detail: `docs/agents/list-pages.md`.
- **Reports is a top-level surface with one report: the company rollup** (#144) —
  keyless and read-only. Any `UNION` reader needs a `source` discriminator in its ORDER BY
  (`created_at DESC, source DESC, id DESC`) — `test_query_determinism` cannot catch that tie.
  Archived is opt-in (the third sanctioned `LIVE_PREDICATE` hole); headline chips are their own
  aggregates, never reductions of capped lists; never sum values across currencies. A nav
  destination is added to BOTH `CrmLayout.NAV_ITEMS` and `shared/MobileMenuDrawer`.
  Full detail: `docs/agents/reports.md`.
- **The dashboard leads with a Today panel** (#130, `backend/crm/today_service.py`) —
  pure SQL + pure Python, identical with zero AI keys; the ladder is the pure
  `build_today_items`. Staleness is IMPORTED from `analytics_service` and evaluated in SQL, never
  re-typed. One clock: `gtd_common.today_local_str()` plus `core.localtime.local_day_bounds()`,
  with `zoneinfo` the only timezone authority. The endpoint returns the full ranked list and the
  client slices it; unranked rows never fill the collapsed five.
  Full detail: `docs/agents/today-panel.md`.
- **Todos have two modes over ONE store** (#70), and **GTD is the default** (#102).
  One `todos` table, never a second. Every todo write funnels through
  `service._apply_todo_update_cur` (a DB CHECK binds `completed` and `status`); open-todo
  readers carry `NOT_DROPPED_TODO`; `todo_projects` stays in both TRUNCATE variants. Tool
  surfaces SWAP by mode while executors stay reachable in both. All four mode readers degrade
  to `gtd` (a test pins their agreement), and the #102 migration UPDATE is the mechanism — do
  not "simplify" it away. `CrmLayout` is the one owner of the mode in the UI.
  Full detail: `docs/agents/todos-gtd.md`.
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
- **API keys are entered in-app, encrypted at rest** (Fernet; key from env →
  OS keychain → file fallback, through the shared `core/secret_store.py` ladder the
  JWT secret also uses, minus that middle rung — #222) — never as env vars.
- **Backend tests** live in `backend/tests/` (config in `backend/pytest.ini`,
  `asyncio_mode = auto`). The default `pytest` run is **hermetic** — pg helpers and
  provider SDKs are mocked, encryption runs against a per-test key — so the CI gate
  needs no database. Tests that need a real PostgreSQL are marked
  `@pytest.mark.integration` and deselected by default (`addopts = -m "not
  integration"`); run them with `pytest -m integration` and a reachable
  `TEST_ADMIN_DSN`. No `skip`/`xfail`/`# noqa`/`eslint-disable` — fix root causes.
  A repo-wide **guard** test — one that sweeps the tree and asserts a property
  (`test_route_authz`, `test_gmail_guard`, `test_prompt_genericization`,
  `test_query_determinism`, `test_help_library`) — must itself be falsifiable, because a sweep that quietly
  stops matching is a permanent green, and a permanent green is worse than no guard
  since it reads as coverage. So a new one owes two things beyond the property itself:
  a self-test pinning its detector on synthetic input (both a case it must flag and a
  case it must not), and an assertion that it still reached the real code — per module
  or per registered item, never one repo-wide total, which the largest package satisfies
  on its own. `test_query_determinism` and `test_prompt_genericization` carry both
  (`test_the_guard_actually_catches_a_leak` is the latter's detector self-test);
  `test_gmail_guard`'s source sweep currently has neither and is worth hardening the
  next time it is touched.
- **Frontend tests** are **vitest** (`npm test` → `vitest run`), landed with #73. Config
  is a STANDALONE `frontend/vitest.config.ts` — vitest reads it *instead of*
  `vite.config.ts`, so the production build config stays untouched and tests skip the
  react/tailwind plugin pipeline. Tests are **co-located** with the code
  (`src/**/*.{test,spec}.{ts,tsx}`), not in a separate tree. The default environment is
  `node`; a DOM test opts in with a per-file `// @vitest-environment jsdom` docblock —
  add them that way, never by flipping the default. Components are rendered with plain
  `react-dom/client` `createRoot` + React's `act`; there is deliberately **no
  `@testing-library`** dependency (`vitest` + `jsdom` are the only test devDeps). Three
  fail-closed guards are load-bearing and must not be deleted as "defaults":
  `passWithNoTests: false`, `allowOnly: false`, and `expect.requireAssertions: true`
  (the last is NOT a vitest default) — a runner that reports "0 tests / exit 0" is a
  permanent silent green, which is worse than no runner because it looks like coverage.
  `env: { TZ: 'America/Chicago' }` pins the timezone so local and CI agree — deliberately
  **not** UTC: `crm/pipelineFilters.ts` derives `ymd` from local-date getters because
  `toISOString()` drifts a day in US evening time, and under a UTC runner that distinction
  disappears, so a test pinning it could never fail.
- **The frontend boot path is code-split, and the split is structural** (#149).
  `main.tsx` renders `Root.tsx`, whose `/todo` and CRM branches are both `lazy()`. Adding a
  route means a module-scope `lazy()` const in `App.tsx`; GTD pages come through
  `crm/gtd/pages.ts`; `AssistantPanelBody` loads lazily by its leaf path. `CrmLayout`'s Suspense
  wraps only the `<Outlet />` — nav, sign-out and launcher stay outside. `vite.config.ts`
  declares no `manualChunks`, and `src/bootSplit.test.ts` + `src/bootSplitBuild.test.ts` guard
  the split (eager allowlists name leaf modules only, never barrels).
  Full detail: `docs/agents/frontend-boot-split.md`.

## Don't Do This

- Never add an email-send tool or widen Gmail scopes/capabilities beyond read +
  create-draft (see above).
- Never rename, remove or repurpose a facet `key` (or a facet's value shape) without
  bumping that surface's `CollectionStorage.version`. Saved views (#181) are stamped with
  that number server-side, and at the same version a repurposed key turns a shared view into
  a silently-empty filter. ADDING a facet is safe and needs no bump.
- Never create runtime SQLite stores or ad-hoc schema — Postgres migrations own
  the schema. When a check-then-write spans reads and updates, do it in one
  transaction with `SELECT ... FOR UPDATE` (see `core/auth_2fa.py`).
- Never page a full-corpus sweep on a mutable order. `sort=id` is the assembly key on
  every list endpoint, `after_id` is refused with any other sort, and `hasMore` comes from
  an extra row rather than a `total` computed in a separate transaction (#77). A new list
  endpoint needs all three before a page assembles it. **#59 put the pipeline board on the
  same three rules**, and added the fourth that a *board* needs: a swept corpus is
  delivered in the server's PRESENTATION order, not the cursor order it arrived in —
  `PipelinePage` sorts columns by `lead_score` with a STABLE sort and relies on recency
  surviving beneath equal scores, which is the common case because `lead_score` is NULL
  until something recomputes it. Handing back `id ASC` would silently flip most columns to
  oldest-first, so `sweepPipelineDeals` re-sorts before it resolves.
- Never write `todos.completed` or `todos.status` outside
  `service._apply_todo_update_cur` — a DB CHECK binds them, so any other writer is a
  constraint violation waiting to happen (#70). Adding a todo READER means adding
  `NOT_DROPPED_TODO` to it too, unless it is deliberately counting every row.
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
- **Never cap a reader whose `ORDER BY` isn't a TOTAL order** (#58). A `LIMIT`/`OFFSET`
  over a non-unique sort key has no defined result — Postgres may break the tie
  differently on each execution, so a row shows up on two pages or on none. End every
  such `ORDER BY` on a unique term: `id` (matching the preceding key's direction, so
  the tiebreak reads the way the sort does), or a column that is already UNIQUE
  (`assistant_context_files.filename`, `assistant_messages.seq` within one
  conversation), or — for a grouped reader — the rest of the GROUP BY key
  (`find_duplicate_deals` orders by `title` **and** `contact_id`, because the group is
  the pair). Ties are the normal case, not an edge: `created_at`/`updated_at` default
  to `now()`, which is **transaction-start** time, so every row written in one
  transaction is byte-identical — a CSV import, `seed_data`, `merge_deals`' note
  copies. Uncapped readers carry the term too, so adding a `LIMIT` later can't quietly
  reintroduce the bug — which is why #59, server-side pipeline pagination, was
  `Blocked by: #58`, and #59 has since cashed that promise in: `get_pipeline`'s keyset
  page is a real capped reader now, and its **default** read stays uncapped and stays in
  `test_hardened_uncapped_readers_keep_their_tiebreaker`.
  Enforced by `backend/tests/test_query_determinism.py`, which AST-scans every non-test
  backend module (it reads f-strings and implicitly-concatenated literals). An ORDER BY
  assembled at RUNTIME is reported as `unknown`, never waved through: the exact set is
  pinned in `UNDECIDABLE_SITES`, keyed by enclosing function, and each entry owes a
  behavioral test on the SQL that reader really emits — so a reader cannot opt out of
  the guard by moving its ordering into a variable. Expect to edit that registry when a
  reader starts or stops interpolating its ORDER BY (#59 and #77 both touch such
  readers); the failure message says which way it moved and what to do.
- Never add a static page import to `App.tsx`, `Root.tsx` or `main.tsx`, and never import
  `AssistantPanelBody` or `TodosPage` statically from the shell — each puts that whole graph
  back in front of every visitor, and `src/bootSplit.test.ts` + `src/bootSplitBuild.test.ts`
  fail CI on all of them (#149). Add a module-scope `lazy()` const instead, never one inside
  a component body (that mints a new component type per render and remounts the subtree).
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
- Never add a route to `crm/gtd_router.build_router` that should stay private: that
  factory is mounted TWICE, and its second mount is the no-login public web app.
  Authenticated-only routes belong on the module-level `router` instead.
- Never commit TN Cheesecake internals: no real prospect/customer data, no TNC
  staff/product names, no internal hostnames or secrets. Ported prompts (Casey's)
  must be genericized. This repo goes public at launch and history is forever.
  Enforced by `backend/tests/test_prompt_genericization.py` (#22, widened repo-wide in
  #90), which scans **two** surfaces against **two** denylists, split by what a token
  IS rather than by which file holds it. `_FORBIDDEN` (= `_COMPANY + _VERTICAL +
  _BLUEPRINT`) covers the **model-facing payload** — the assembled system prompt,
  every tool name/description/schema, the heartbeat prompt, the UI starter chips, and —
  since #143 — every `backend/help/content/` topic, which becomes payload the moment a
  help tool returns it, so blueprint names are banned there as they are everywhere else a
  provider can see. The
  narrower `_REPO_FORBIDDEN` (= `_COMPANY + _VERTICAL`) covers **every committed text
  file**, enumerated by `git ls-files`, so the scope is a file CLASS: a new `docs/`,
  `scripts/` or `.github/` file is guarded the moment it is *staged* — there is no
  directory list to remember to update, which is the whole point (the gap #90 closed
  let a hardcoded upstream org URL and six real upstream directory names reach CI
  green). `_BLUEPRINT` (`cake_os`, `casey`, `cake_crm_`) is the deliberate asymmetry: banned from
  the payload — a shipped product must not name what it was ported from — but
  legitimate in committed prose, since the Source Map and every port comment cite the
  blueprint by name. Deliberate exemptions live in `_REPO_ALLOW` as path → **{pattern:
  exact expected count}** + a written reason, and the count is the whole point: a
  file-keyed exemption would repeat the mistake the gitleaks bullet under "CI &
  Contributing" already records — it "exempts every finding in that file, including a
  real one" — and AGENTS.md is the most-edited file in the repo, so an unbounded
  exemption *here* would be the widest hole of all. Entries exist ONLY for text that
  must talk *about* the denylist: this rule and a sibling guard's own literals. A count that stops matching reality
  fails CI **in both directions** — a stale or inflated allowance is caught as surely
  as a new occurrence — so turning the guard down takes a visible edit to that list
  rather than a bumped number. Scrub the file instead whenever scrubbing is possible.
  **No file is exempt, the guard included** — it holds a counted allowance for its own
  denylist literals like everything else, so the one file with the most licence to
  carry these strings is not also the one place nobody is watching. Surfaces beyond
  plain file *content* are covered because they leak just as permanently: every
  committed **filename** (for a compressed container like a .xlsx, whose bytes no
  decoder can read, that is the only surface there is), **invisible characters** (a
  name pasted out of Word or a PDF can carry a soft hyphen or zero-width space inside
  it and match nothing while reading perfectly — stripped from paths as well as
  bodies), and text in encodings a naive reader drops. On that last: a NUL-byte "is
  this binary?" probe silently skips **UTF-16**, exactly the shape a spreadsheet or
  CSV export of real customer names arrives in, while BOM-less UTF-16 of ASCII content
  is byte-wise *valid UTF-8* and so decodes "successfully" into NUL-interleaved mush
  that matches nothing. The decoder therefore never gives up, and covers the whole
  family in **one** move instead of guessing an encoding: it takes the BOM'd reading
  (UTF-32 tested before UTF-16, which share a two-byte prefix) or falls back UTF-8 →
  latin-1, and for anything NUL-bearing it *additionally* scans the bytes with the
  NULs removed. That one extra reading catches every fixed-width encoding of ASCII at
  once — UTF-16 and UTF-32, either byte order, BOM or none — plus a plain ASCII name
  sitting inside an otherwise-binary blob. Guessing instead meant a NUL-density
  heuristic, and that had a hole: most real binaries are NUL-dense too. Boundaries are
  `(?<![0-9a-z])`, **not
  `\b`** — `\b` counts `_` as a word character, so a token went invisible the moment an
  underscore followed it (`cake_os\b` misses `cake_os_prompt`; the company abbreviation
  vanished the same way inside `<abbrev>_internal`), which are precisely the shapes
  these names take in identifiers, filenames and env vars (six such bypasses were
  measured, and this very bullet tripped the guard by naming one). The scan
  reads the working tree, **not history**: tokens committed before a scrub stay in the
  log. `test_sync_intake.py` consumes `_REPO_FORBIDDEN` **by name** for the same
  reason: it used to hand-copy the blueprint regex in a `pattern != …` exclusion, so
  widening that pattern turned the exclusion into a no-op and failed that test — loudly
  and fail-closed, but on a file nothing was wrong with. Naming the class instead means
  both of #88's scans inherit every future widening automatically. It deliberately
  narrows what they scan (blueprint tokens are legitimate outside the payload) and
  keeps the rendered-issue-body scan, which is coverage no file scan can provide.
- Never import git history from cake_os or chatty — code arrives as clean snapshots
  in ordinary commits.
- Never merge a pull request — with exactly ONE exception, the **operator ship lane**:
  the `/auto-issues-ship-loop(-team)` skills may squash-merge `ready-to-ship` PRs and
  deploy-verify the Railway demo instance, under the grant registered **operator-side**
  in `~/.claude/ship-repos.json`. That grant is deliberately NOT in this repo — repo
  files are PR-writable and grant nothing; this bullet *describes* the lane, the
  registry *grants* it (see `docs/AUTO_ISSUES.md` → ship lane). Outside that lane, Will
  merges all PRs manually. Push feature branches and open PRs to `main`; never push
  directly to `main` after the initial seeding phase.
- Don't add per-agent/per-integration enable flags for core features — the CRM and
  its tools are always on; only AI features key off provider configuration.
- Don't hand-build what the blueprints already have — check `~/ai/chatty` and
  `~/ai/cake_os` first; port and adapt, don't reinvent.

## Issue Loop Conventions (mirrors CAKE OS)

- Work starts from a GitHub issue. Branch `feature/issue-N-<slug>` from `main`, PR
  back to `main`. The issue loop itself never merges; merges happen only by Will's
  click or the sanctioned ship lane (see "Don't Do This").
- Issue eligibility for the automated loop: open, unassigned, no `no-auto` label, and
  human-approved via the `greenlit` label (default-deny — an un-`greenlit` issue never
  enters the loop). Self-assign (or label `no-auto`) before working an issue manually.
- Respect "Blocked by: #N" lines in issue bodies — don't start an issue whose
  blockers aren't merged.
- See `docs/AUTO_ISSUES.md` for the operator guide (label vocabulary, terminal
  outcomes, bring-up sequence, and repo prerequisites like branch protection).
  `scripts/seed-labels.sh` idempotently creates/normalizes the loop's labels.
- **`sync-intake` issues are ordinary issues.** The cake_os sync bot (#23,
  `docs/SYNC.md`) files them when upstream CRM code changes, un-`greenlit` like
  everything else — default-deny holds, and the bot never marks its own work
  eligible. There is **no special case anywhere in the loop**: a greenlit intake
  runs the normal pipeline, and the port worker reads cake_os from the local clone
  at `~/ai/cake_os`, never from the issue body. Ports add a `SYNC_LEDGER.md` row.
- **Porting from cake_os: both sides are Postgres.** cake_os's CRM is PostgreSQL
  (no `apps/crm/db.py`; `core.postgres` helpers; `%s`), so the SQLite items in
  `docs/solutions/database-issues/cakecrm-sqlite-to-postgres-crm-port.md` apply only
  to the chatty-origin port, not to cake_os ports. What does bite every time:
  tenancy stripping (`user_email`/`owner_email` threads ~25% of upstream CRM
  functions and has nowhere to land here), the module-topology collapse (~20 cake_os
  service modules → CakeCRM's single `crm/service.py`, so a same-named file is a
  *candidate*, never a destination), the `apps.todo_gtd`/`apps.dimm` call sites, and
  the PII scrub. Full playbook in `docs/SYNC.md`.
- **Where documentation goes.** `AGENTS.md` is the always-loaded core and changes only
  when a core invariant, rule or convention changes. Implementation notes — the "landed #N
  as …" record, design reasoning, divergences from a blueprint — go in the matching
  `docs/agents/*.md` topic doc in the same PR as the change (create one for a new area and
  add it to the Topic docs index above); porting provenance goes in
  `docs/agents/source-map.md`. Never `@`-import a topic doc: an import loads eagerly and
  undoes the split.

## Brand mark

- The mark is `frontend/public/logo-mark.svg`. Its byte-identical copies (favicon,
  `docs/brand/`, `website/`) and the PNG exports in `docs/brand/` are NOT built — re-copy and
  regenerate them with any edit. It sits LEFT of the "CakeCRM" wordmark, never above it and
  never on a badge — with ONE exception by Will's decision: the mycakecrm.com hero, where the
  animated slice stacks above a spaced "CAKECRM" wordmark, and is never named after the dessert (a denylisted word, filenames
  included). Full detail: `docs/agents/brand-mark.md`.

## Website (mycakecrm.com)

- `website/` is the static explainer site, FTP-deployed by `.github/workflows/deploy-website.yml`,
  whose third-party action stays **pinned to a commit SHA**, never a tag. Screenshots come only
  from the demo instance after a reset to the fictional sample data; marketing copy about safety
  must match `frontend/src/assistant/` and `backend/assistant/{engine,confirm_tier}.py`; a new
  font family ships its notice in `website/fonts/OFL.txt`. The todo and Baker demos on the page
  are browser-only fakes (`website/js/`): the Baker one is scripted, labelled "Scripted, not AI",
  and its Read/Ask/Auto behaviour is a safety claim held to the same code. Full detail:
  `docs/agents/website.md`.

## CI & Contributing

- **CI** (`.github/workflows/ci.yml`) runs on every PR to `main` and on `push` to
  `main`, in three jobs: **backend** (`ruff check .` → import check → `python -m
  pytest -q`, from `backend/`), **frontend** (`npm ci` → `npm run build` → `npm run
  lint` → `npm test`), and **secret-scan** (gitleaks). The scan runs `gitleaks git .` over FULL history, so
  a false positive stays found forever once committed and cannot be fixed by editing
  the tip. `.gitleaksignore` records verified-false findings by exact
  `commit:file:rule:line` fingerprint, with the reasoning written down. Use that file,
  NOT a `.gitleaks.toml` allowlist keyed on commit+path — the latter exempts every
  finding in that file in that commit, including a real one. Validate any new entry
  with a **randomly generated** token, never a canonical doc example
  (`AKIAIOSFODNN7EXAMPLE` and friends are allowlisted by default and prove nothing).
  The alternative is a force-push, which this repo does not do. Backend lint config is `backend/ruff.toml`
  (select `F,E,W,I`; `E501` ignored); dev/CI tooling is pinned in
  `backend/requirements-dev.txt`; tests live in `backend/tests/`. The import check
  imports the app with no `DATABASE_URL` (the Postgres pool inits in the lifespan
  handler), so CI needs no database.
- **AI Code Review** (`.github/workflows/pr-review.yml`) is an **optional, advisory**
  check on PRs to `main`: it posts a review comment (marker `<!-- ai-code-review-bot -->`)
  when an `ANTHROPIC_API_KEY` Actions secret is set (model overridable via the
  `AI_REVIEW_MODEL` repo variable), and is a **green no-op** otherwise (forks, keyless
  repos). It must **not** be configured as a required check. It uses only the GitHub API
  (no checkout) and `on: pull_request` (never `pull_request_target`).
- **Contributing** — `CONTRIBUTING.md` covers dev setup and the checks. Contributions
  are under the **DCO** (`Signed-off-by`, via `git commit -s`), not a CLA, licensed
  AGPL-3.0 (inbound = outbound). DCO enforcement is the DCO GitHub App (installed once,
  manually, on the repo). Issue/PR templates live in `.github/`.

## Source Map (where ported things come from)

Where every ported area comes from, and what was deliberately not ported, is in
`docs/agents/source-map.md`. Add or update its row in the same PR as a port.
