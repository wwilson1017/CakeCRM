# CakeCRM

Self-hostable CRM with one built-in AI sales assistant, Baker (a fixed brand, not a setting). FastAPI + PostgreSQL
backend (`backend/`), React/Vite/TypeScript frontend (`frontend/`), static marketing site
(`website/`). License AGPL-3.0; contributions under the DCO. Default branch `main`.

## Commands

Setup is in `CONTRIBUTING.md` (`docker compose up -d`, `cp .env.example .env`, Python 3.12
venv, `npm ci`). Run the app with `python run.py`; `npm run dev` in `frontend/` for hot reload.

Before pushing, run what CI runs:

```bash
# backend/ (venv active)
ruff check --config ruff.toml . ../scripts
python -m pytest -q                  # hermetic: no database needed
python -m pytest -m integration      # real Postgres; TEST_ADMIN_DSN defaults to the compose DB

# frontend/
npm run build && npm run lint && npm test
```

## Topic docs

Before changing code in an area, read its doc in `docs/agents/`. Keep these as plain paths,
not `@` imports (imports load eagerly).

| Area | Doc |
|---|---|
| Gmail (`backend/gmail*/`, `GmailCard`) | `gmail.md` |
| Providers, assistant engine/prompt/confirm tiers, background turns, memory, context files, help library, playbooks | `assistant.md` |
| Users, roles, auth, ownership, conversation access, Settings visibility | `accounts-and-ownership.md` |
| Migrations, secrets, login credential, deploy storage | `database.md` |
| CRM records, pipeline, forms, chatter, custom fields, lead score, theme tokens | `crm-core.md` |
| Date-range facets, saved views | `saved-views.md` |
| Contacts/Companies/Todos list pages, corpus sweep | `list-pages.md` |
| Reports | `reports.md` |
| Dashboard Today panel | `today-panel.md` |
| Todos / GTD, no-login todo surfaces | `todos-gtd.md` |
| Routes, lazy loading, error boundaries | `frontend-boot-split.md` |
| Tests, guard tests, CI, secret scanning | `testing.md` |
| Logo and brand assets | `brand-mark.md` |
| `website/` | `website.md` |
| Porting from upstream, or where a feature came from | `source-map.md`, `docs/SYNC.md` |

New implementation notes, design reasoning and "landed #N" records go in the matching topic
doc (create one for a new area and add a row here); a port updates its `source-map.md` row.
Change this file only when a core rule changes.

## Hard lines

These are product trust guarantees; each is also stated in `SECURITY.md` or pinned by a test.

- Never add an email-send capability. Gmail stays read + create-draft: scopes
  `gmail.readonly` + `gmail.compose`, tools `gmail_search` / `gmail_read_thread` /
  `gmail_create_draft`, no new ops in `gmail/client.py`'s allow-list. `test_gmail_guard.py` fails CI on any send surface.
- Unattended (background) turns get read tools + `notify_user` only, minus
  `UNTRUSTED_SOURCE_TOOLS` (Gmail and other connection reads); a new connection-gated read
  tool joins that set.
- Protected context files (`soul.md`, `MEMORY.md`) always confirm and are admin-only writes.
- The CRM must work fully with zero AI keys: AI affordances hide (key off
  `GET /api/setup/status` `ai_ready`), they never error.
- Never commit TN Cheesecake internals or any real customer data — no real names, no
  TNC staff or product names, no internal hostnames, no secrets. History is public and permanent.
  `test_prompt_genericization.py` scans every committed file and filename; blueprint names
  (`cake_os`, `casey`) are fine in prose but banned from anything the model sees (prompts,
  tool schemas, help topics). Its per-file allowances are exact counts, so an edit that adds
  or removes a denylisted word in an allowed file (including this one) must update the count.
- Routes added to `crm/gtd_router.build_router` are public: that factory is also mounted
  as the no-login todo web app. Put authenticated-only routes on the module-level `router`.

## Backend rules

- PostgreSQL is the only datastore — no SQLite, Redis or ad-hoc schema. Schema changes are
  new files in `backend/migrations/` named `YYYYMMDDHHMMSS_<name>.sql` (`date +%Y%m%d%H%M%S`),
  applied in lexicographic order. Use the `core/postgres.py` helpers. Durable state is a
  Postgres row; on Railway only `backend/data/` survives a redeploy.
- A check-then-write that spans reads and updates is one transaction with
  `SELECT … FOR UPDATE` (pattern: `core/auth_2fa.py`).
- Every `ORDER BY` ends on a unique term (usually `id`, same direction as the preceding
  key), capped or not — `created_at` ties within a transaction. A `UNION` reader also sorts
  on a `source` discriminator. `test_query_determinism.py` enforces this; its failure message
  says how to update `UNDECIDABLE_SITES`.
- List endpoints that a client sweeps use `sort=id` keyset paging (`after_id` refused with
  any other sort) and derive `hasMore` from an extra row, not a separate `COUNT`.
- The deploy runs one worker (`gunicorn --workers 1`); in-process caches rely on it.
- Authorization lives only in `core.auth.get_current_user` and per-route `require_admin`;
  `test_route_authz.py` pins the admin route set. The JWT never carries a role.
- Record `owner_id` is not access control — any member may edit any CRM record. Assistant
  conversations are owner-only with no admin override (cross-seat reads 404).
- Handlers and dependencies doing blocking psycopg2 or bcrypt work are sync `def`.
- Never call a provider SDK from feature code — go through the `AIProvider` ABC, import
  SDKs lazily inside methods, and use `resolve_tier_model()` for cheap background work.
- The assistant's static system prompt carries no per-turn data, and its block order is
  pinned by tests.
- Don't mark a tool `ROUTINE` (no confirmation) if it notifies, deletes/archives/merges,
  bulk-writes, or sends data off the install.
- Company names from free text go through `crm.service.resolve_or_create_company_ids()`.
  Deal column writes go through `service._write_deal_update`; `_DEAL_USER_WRITABLE` is an
  explicit allowlist. Deal reads carry `LIVE_PREDICATE` unless deliberately including archived.
  Deal-returning tools attach a `url` via `crm.links.with_deal_url`.
- Never sum money across currencies.
- The local day comes from `gtd_common.today_local_str()` / `core.localtime.local_day_bounds()`;
  `zoneinfo` is the only timezone authority.
- Todo `completed`/`status` are written only through `service._apply_todo_update_cur`
  (a DB CHECK binds them); open-todo readers include `NOT_DROPPED_TODO`. There is one
  `todos` table for both modes.
- The feature is "todos" in every layer (tables, routes, tools, UI). Don't reintroduce
  "task" for it — that word is reserved for async/scheduler machinery.
- New CRM data tables (audit tables included, FK or not) join both branches of `crm.service._truncate_all`;
  `saved_views` is deliberately outside them.
- A new upload route needs a row in `main._ROUTE_REQUEST_LIMIT_SPECS`.
- API keys are entered in the app and encrypted via `core/secret_store.py`, never read from env.
- A PR that changes a user-facing flow updates its `backend/help/content/` topic.

## Frontend rules

- Build a `Date` from a TIMESTAMPTZ with `crm/gtd/util.parseUTC`, never the bare
  constructor (zone-less strings parse as local). Sort timestamps on the parsed instant,
  not the string. Date-only `YYYY-MM-DD` values keep the local-parts constructor.
- Pages and heavy panels load lazily: add a module-scope `lazy()` const in `App.tsx`, never
  a static page import in `App.tsx`/`Root.tsx`/`main.tsx`, never `lazy()` inside a component.
  `bootSplit.test.ts` and `bootSplitBuild.test.ts` enforce this.
- Renaming, removing or changing the value shape of a facet `key` requires bumping that
  surface's `CollectionStorage.version` — saved views are shared server-side and would
  silently go empty. Adding a facet needs no bump.
- Pagination is transport only: list pages sweep the whole corpus and filter client-side.
- Colours come from `--color-ck-*` tokens, no literal fallbacks.
- Settings card visibility is decided only in `crm/settingsSections.ts`; never show a
  control that can only 403.
- A new nav destination goes in both `CrmLayout.NAV_ITEMS` and `shared/MobileMenuDrawer`.
- Tests are vitest, co-located (`*.test.ts[x]`), default environment `node`; opt into DOM per
  file with `// @vitest-environment jsdom`. No `@testing-library`. Don't remove
  `passWithNoTests: false`, `allowOnly: false`, `requireAssertions: true` or the pinned `TZ`.
- The mark is `frontend/public/logo-mark.svg`. Its byte-identical copies (favicon,
  `docs/brand/`, `website/`) and the PNG exports in `docs/brand/` are NOT built — re-copy and
  regenerate them with any edit. It sits LEFT of the "CakeCRM" wordmark, never above it and
  never on a badge — with ONE exception by Will's decision: the mycakecrm.com hero, where the
  animated slice stacks above a spaced "CAKECRM" wordmark, and is never named after the dessert (a denylisted word, filenames
  included). Full detail: `docs/agents/brand-mark.md`.

## Tests and CI

- No `skip`, `xfail`, `# noqa` or `eslint-disable` — fix the cause.
- A new repo-wide guard test needs a self-test on synthetic input (one case it must flag,
  one it must not) and an assertion that it reached the real code, per module. See
  `docs/agents/testing.md`.
- CI runs gitleaks over full history. For a verified false positive, add its exact
  fingerprint to `.gitleaksignore` with a reason; never a path-keyed `.gitleaks.toml` allowlist.
- Workflows trigger on `pull_request`, never `pull_request_target`; third-party actions are
  pinned to a commit SHA.
- `website/` is the static explainer site, FTP-deployed by `.github/workflows/deploy-website.yml`,
  whose third-party action stays **pinned to a commit SHA**, never a tag. Screenshots come only
  from the demo instance after a reset to the fictional sample data; marketing copy about safety
  must match `frontend/src/assistant/` and `backend/assistant/{engine,confirm_tier}.py`; a new
  font family ships its notice in `website/fonts/OFL.txt`. The todo and Baker demos on the page
  are browser-only fakes (`website/js/`): the Baker one is scripted, labelled "Scripted, not AI",
  and its Read/Ask/Auto behaviour is a safety claim held to the same code. Full detail:
  `docs/agents/website.md`.

## Scope

- One built-in assistant; it serves the CRM, not the other way round. No multi-agent
  roster, training mode or knowledge-import adapters.
- No per-feature enable flags for core CRM features; only AI features key off provider config.

## Commits and PRs

- Branch from `main`, PR to `main`; never push to `main` directly. Sign off every commit
  (`git commit -s`). No `Co-Authored-By` trailers or tool-generated footers. Don't merge your
  own PR.
