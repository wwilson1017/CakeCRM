# CakeCRM — Project Instructions

## What This Is

Free, open-source, self-hostable CRM with a built-in AI sales assistant. Seeded from
Chatty's product shell and agent engine; CRM features ported from the CAKE OS CRM;
assistant capability bar is Casey (CAKE OS's sales agent). Single-user for v1
(multi-user/seats is the headline roadmap item). PostgreSQL, FastAPI, React/Vite.
Deploy targets: `python run.py` locally (Postgres via Docker Compose), Railway
one-click in the cloud (the template provisions a PostgreSQL service).

- **Repo**: `wwilson1017/CakeCRM`, default branch `main`. **Private until launch.**
- **Local convention (Will's machine)**: this repo lives at `~/ai/CakeCRM`.
- **Blueprints**: CAKE OS at `~/ai/cake_os` (CRM features, Casey, auto-issues loop
  conventions), Chatty at `~/ai/chatty` (product shell, providers, agent engine,
  Telegram, Google integration). When a task says "port X", read the source there.
- **Planning docs**: `docs/CRM_OSS_PIVOT.md` and `docs/CAKECRM_ISSUE_SPEC.md` in the
  cake_os repo (branch `claude/chatty-crm-rebranding-a0ffsf` until merged) — the why
  and the what. The issue tracker here is the live spec; issues #1–#25 map to spec
  items S1–S25.

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
  Landed #8 as `backend/gmail/` (mounted `/api/gmail`) + `frontend/src/crm/components/GmailCard.tsx`:
  BYO Google OAuth app (client_id/secret + tokens Fernet-encrypted in the
  `gmail_connection` singleton), scopes `gmail.readonly` + `gmail.compose` only, the
  three tools `gmail_search`/`gmail_read_thread`/`gmail_create_draft` collected via
  `gmail.tools.get_gmail_tools()` (defs gated on connection, executors follow),
  `gmail_create_draft` marked `writes:true`. Enforcement artifacts: the guard test
  `backend/tests/test_gmail_guard.py` (CI fails if any send surface appears), a
  runtime op-allow-list in `gmail/client.py`, and `SECURITY.md`. Untrusted email read
  into the assistant taints the turn (power→normal confirmation) to blunt prompt
  injection. A **read-only Gmail touch scan** (#17, `backend/gmail_scan/`) runs as its
  own `gmail_scan` heartbeat job (60s due-check, gated only on Gmail being connected,
  no AI keys needed): it lists recent inbox mail via the *existing* approved
  `list_messages_op` (no new op/scope — still read+draft-only), matches senders to
  contacts by exact case-insensitive email, and logs `email` touches to `activity_log`
  so #16's counts see exchanges nobody logged. Idempotent per Gmail message-id via the
  `gmail_scanned_messages` ledger (one txn per message; audit columns carry NO FK so the
  CRM-reset TRUNCATE still works); a deal is attributed only when the contact has exactly
  one open deal (never fabricated). Senders recurring ≥3 times with no contact raise one
  deduped "create contact?" alert (`source=gmail_touch_scan`).
  **Connection-race hardening** (#43) adds `gmail_connection.connection_generation`, an
  optimistic-lock counter bumped by every mutation that changes WHICH connection is live
  (`save_app_credentials` / `clear_connection` / `save_tokens`) and deliberately NOT by
  `update_access_token` or `mark_broken` (same account; bumping there would invalidate
  in-flight reconnects and every pending draft on each hourly refresh). Two CAS mechanisms
  coexist **by design**, each matching its invariant: generation-CAS guards connection
  *identity* — `claim_oauth_state()` returns the generation (`int | None`, so callers test
  `is None`, never truthiness) and `save_tokens(..., expected_generation)` CASes on it
  (miss → revoke the fresh grant, redirect `reason=conflict`); ciphertext-CAS guards
  *credential material* — `update_access_token` (unchanged) and now `mark_broken(prev_refresh_enc)`.
  A pending `gmail_create_draft` is bound to the connection it was proposed against: the
  engine stamps `gmail_generation` into the **pending-result placeholder** (safe — history's
  status helpers read only `"status"`) via `_pending_placeholder()`, and
  `resolve_confirmation` refuses a stale one via `_binding_conflict()` →
  `gmail.tools.binding_conflict()`, keyed off the hand-maintained
  `engine._CONNECTION_BOUND_WRITE_TOOLS` (same shape as `_UNTRUSTED_SOURCE_TOOLS`);
  `claim_pending_tool` now also returns the pre-claim `content`. Disconnect/app-replace are
  one atomic statement (CTE `SELECT … FOR UPDATE` → clear → `RETURNING` the OLD ciphertext;
  plain `UPDATE … RETURNING` yields post-update values), so the router revokes exactly the
  grant it ended. Read fidelity: a large text body Gmail stored under `attachmentId` is
  recovered inside the **existing** `get_thread_op` (no new `_APPROVED_OPS` entry, no scope
  change) under a 256 KB pre-check that **fails closed on an undeclared `body.size`** (Gmail
  has no ranged read) plus a **thread-scoped** 4-fetch budget (per-message would scale with
  message count, letting a sender shape one read into dozens of round-trips), HTML flattened
  like inline HTML, degrading to `""` on failure and `[body too large to display]` when
  oversize; parts carrying a `filename` are NEVER fetched. Note `store.get_row()` swallows
  read errors and returns `{}`, so `gmail.tools._live_generation()` distinguishes
  "unreadable" from generation 0 — reading it as `... or 0` would mint a bogus binding and
  falsely refuse a valid draft. The two-step thread fetch was evaluated and
  **declined** (it doubles common-case calls/latency/quota to bound memory only for rare
  long threads).
- **Multi-provider AI** via the `AIProvider` ABC (Anthropic, OpenAI, Gemini, Ollama,
  Together). Never call a provider SDK directly from feature code. Cheap background
  AI work (touch counts, classification) uses the light tier via
  `resolve_tier_model()`. The layer lives in `backend/providers/` (ABC
  `providers/base.py`, factory `providers.get_ai_provider()`, key-based only — no
  OAuth). Provider SDKs (`anthropic`/`openai`/`google-generativeai`/`httpx`) are
  imported **lazily inside methods only, never at module top level**, so a missing
  SDK or absent key never breaks import/startup. Keys are stored **single-tenant,
  admin-global** in Postgres — `ai_providers` (Fernet-encrypted `api_key_enc`),
  `ai_settings` (active provider/model singleton), `ai_model_tiers`
  (overrides/inferred JSONB). Keys enter in-app via `POST /api/providers/{provider}/
  connect-key` (validated live); `GET /api/setup/status` returns `ai_ready` — the
  degradation gate the CRM UI keys AI affordances off. The chat-loop surface
  (`stream_turn`/`add_tool_results`/`build_tool_turn`) is consumed by the built-in
  assistant engine (`backend/assistant/`, landed #4): an SSE streaming tool loop
  with write-tool confirmation modes and file uploads, mounted at `/api/assistant`
  and gated off `ai_ready`. The assistant's **second execution mode** (landed #6,
  `backend/assistant/background.py`) is a non-SSE `run_background_turn` for
  autonomous work (the heartbeat + reminder firing): it reuses the same
  `ToolRegistry`/`build_tool_turn` loop but, having no human to confirm writes,
  runs under a **server-enforced tool allowlist of READ tools + `notify_user` only**
  (no CRM writes at all — enforced at both advertisement and execution) plus a
  `WRITE_BUDGET_BACKGROUND`, with untrusted reminder/CRM text kept in the user
  message, never the system prompt. So a prompt injection via reminder/CRM content
  can at worst send one notification, never create/log/update/delete a record.
  The assistant has a **long-term memory + nightly dreaming**
  (landed #5, `backend/memory/` + `backend/dreaming/`, **pure-algorithmic — no AI
  calls**): temporal facts in Postgres (`memory_facts`, generated `tsvector` + GIN,
  searched via an OR-of-keywords `to_tsquery('simple', …)` tokenizer) with four `memory_*` tools carrying
  the `writes` flag; relevant facts are injected into the **volatile** half of the
  system prompt each turn (never the cached static half) with once-per-hour retrieval
  tracking. **Dreaming** adapts chatty's file-archival to the single-assistant layout —
  the unit is the fact, so it soft-archives dormant non-tier-1 facts (`archived_at`, never
  deleting; `decision`/`preference` are never archived), scored purely from usage signals
  (14-day-half-life recency, recency-gated frequency, age, confidence; active/stale/dormant
  at 0.4/0.1) and audited in `dreaming_runs`. Its entrypoint
  `dreaming.processor.run_dreaming_if_due()` is scheduler-agnostic (advisory-lock +
  due-guard) and is driven by **#6's 60s `reminder_tick`** (via
  `heartbeat.service._maybe_run_dreaming`) — #5's interim lifespan task was absorbed
  when #6 landed, exactly as planned.
  Multi-user is future work (authz/ownership), not just a `user_id` column.
- **One database: PostgreSQL, and it's mandatory** — the backend refuses to start
  without `DATABASE_URL` (decided 2026-07-18; single engine, ready for multi-user
  growth). Locally `docker compose up -d`; on Railway the template provisions
  Postgres and injects `DATABASE_URL`. No Redis or other external services.
  Required env vars: `AUTH_PASSWORD` + `DATABASE_URL`; `JWT_SECRET` and
  `ENCRYPTION_KEY` auto-generate. Schema is owned by `backend/migrations/*.sql`,
  applied automatically at startup in lexicographic order — name migrations
  `YYYYMMDDHHMMSS_<name>.sql` (use `date +%Y%m%d%H%M%S`), never sequential
  prefixes. Access Postgres through `core/postgres.py` helpers
  (`pg_fetchall`/`pg_fetchone`/`pg_execute`/`get_connection`/`row_to_dict`).
- **The CRM is first-class core** (`backend/crm/`, mounted at `/api/crm`; frontend
  `frontend/src/crm/` + `frontend/src/shared/`) — always-on, no enable flag. Ported
  from chatty's `crm_lite` and translated to Postgres (companies/contacts/deals/tasks/
  activity_log, plus `crm_chatter` — editable/archivable notes threads on deals and
  contacts, landed #15; query idioms follow the matching `cake_os/backend/apps/crm`
  services so later feature ports diff cleanly). Companies are a first-class entity (#13):
  contacts/deals carry a nullable `company_id` FK and a company detail page rolls up
  the linked contacts/deals/activity. User-defined **custom fields** (#19) add a
  two-table EAV (`crm_field_definitions` + `crm_field_values`) on contacts/companies/
  deals, managed in `/crm/settings`, rendered in the entity forms and detail pages, and
  exposed to the assistant via `crm_{get,set}_{contact,company,deal}_fields`;
  `crm_field_values` is polymorphic (no entity FK), so it is cleaned at every
  entity-delete + `_truncate_all` site (definitions survive demo-clear, wiped only by
  `clear_all`), and `is_required` is advisory-only (never enforced server-side). The
  ~31 `crm_*` agent tools + executors are
  collected UNCONDITIONALLY via `crm.tools.get_crm_tools()` — each def carries a
  `"writes"` flag (the single source of truth for the assistant's confirmation gate),
  consumed by `assistant.registry.ToolRegistry` (landed #4). **AI touch counts +
  field provenance** (#16) are the two zero-keys-degrading AI reads: an in-process
  daemon worker (`crm/touch_count_service.py`, event-driven off note/activity writes,
  light tier via `get_ai_provider(agent_model_tier="light")`, prompt-injection-hardened,
  never-fabricate) stores an estimated touch count in `deals.ai_touch_*` for a pipeline
  nudge pill; and `crm/provenance_service.py` (`crm_field_provenance`) records which
  standard fields the assistant wrote (recorded inside the assistant's write-tool
  executors — human router edits don't), badged until confirmed or overwritten. Both are
  invisible with zero keys (no count is computed, no provenance is written). Contact
  import is keyless for CSV/vCard; the AI smart-import path (`get_ai_provider()`)
  degrades to a warning when no provider is configured and its UI affordance keys off
  `ai_ready`. First-run offers to load fictional sample data (prompt tracked on the
  `crm_meta` singleton, not a per-integration flag). The **CRM-first shell** (#9) leads
  nav with Dashboard/Pipeline/Contacts/Tasks, surfaces the assistant as a persistent
  launcher (never the home page), and shows a **dismissible** "add an AI key" nudge —
  never a gate, gated on `!credentials_present`, dismissal tracked on
  `crm_meta.ai_key_prompt_dismissed`. Branding (company name / accent / logo) is edited
  at `/crm/settings`, consuming the existing `/api/branding`; the accent is applied
  app-wide by setting the `--brand-color` CSS variable (`index.css` routes the whole
  theme's accent through it), so the CRM stays fully usable — and re-themable — with
  zero AI keys. The launcher opens a **context-aware slide-over drawer** (#14): the
  open deal/contact/company is published through a shared record context
  (`frontend/src/crm/RecordContext.tsx`, set by the detail pages + `DealDetailSheet`)
  and injected **per-turn** into the assistant's **volatile** system prompt as a
  server-built sentence from a validated `{record_type, record_id}`
  (`assistant/router.ChatContext` → `identity.build_context_note`) — never persisted,
  never client free text — with record-aware quick actions rendered in the drawer.
- **API keys are entered in-app, encrypted at rest** (Fernet; key from env →
  OS keychain → file fallback) — never as env vars.
- **Backend tests** live in `backend/tests/` (config in `backend/pytest.ini`,
  `asyncio_mode = auto`). The default `pytest` run is **hermetic** — pg helpers and
  provider SDKs are mocked, encryption runs against a per-test key — so the CI gate
  needs no database. Tests that need a real PostgreSQL are marked
  `@pytest.mark.integration` and deselected by default (`addopts = -m "not
  integration"`); run them with `pytest -m integration` and a reachable
  `TEST_ADMIN_DSN`. No `skip`/`xfail`/`# noqa`/`eslint-disable` — fix root causes.

## Don't Do This

- Never add an email-send tool or widen Gmail scopes/capabilities beyond read +
  create-draft (see above).
- Never create runtime SQLite stores or ad-hoc schema — Postgres migrations own
  the schema. When a check-then-write spans reads and updates, do it in one
  transaction with `SELECT ... FOR UPDATE` (see `core/auth_2fa.py`).
- Never commit TN Cheesecake internals: no real prospect/customer data, no TNC
  staff/product names, no internal hostnames or secrets. Ported prompts (Casey's)
  must be genericized. This repo goes public at launch and history is forever.
- Never import git history from cake_os or chatty — code arrives as clean snapshots
  in ordinary commits.
- Never merge a pull request — Will merges all PRs manually. Push feature branches
  and open PRs to `main`; never push directly to `main` after the initial seeding
  phase.
- Don't add per-agent/per-integration enable flags for core features — the CRM and
  its tools are always on; only AI features key off provider configuration.
- Don't hand-build what the blueprints already have — check `~/ai/chatty` and
  `~/ai/cake_os` first; port and adapt, don't reinvent.

## Issue Loop Conventions (mirrors CAKE OS)

- Work starts from a GitHub issue. Branch `feature/issue-N-<slug>` from `main`, PR
  back to `main`, human merge only.
- Issue eligibility for the automated loop: open, unassigned, no `no-auto` label, and
  human-approved via the `greenlit` label (default-deny — an un-`greenlit` issue never
  enters the loop). Self-assign (or label `no-auto`) before working an issue manually.
- Respect "Blocked by: #N" lines in issue bodies — don't start an issue whose
  blockers aren't merged.
- See `docs/AUTO_ISSUES.md` for the operator guide (label vocabulary, terminal
  outcomes, bring-up sequence, and repo prerequisites like branch protection).
  `scripts/seed-labels.sh` idempotently creates/normalizes the loop's labels.
- Keep this CLAUDE.md updated in the same PR as any change to architecture,
  conventions, or the rules above.

## CI & Contributing

- **CI** (`.github/workflows/ci.yml`) runs on every PR to `main` and on `push` to
  `main`, in three jobs: **backend** (`ruff check .` → import check → `python -m
  pytest -q`, from `backend/`), **frontend** (`npm ci` → `npm run build` → `npm run
  lint`), and **secret-scan** (gitleaks). Backend lint config is `backend/ruff.toml`
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

| CakeCRM area | Source |
|---|---|
| Product shell (run.py, auth, 2FA, encryption, config, Railway) | `chatty/backend/` + `chatty/run.py` |
| Postgres pool + migration runner | `cake_os/backend/core/postgres.py` |
| AI providers + pricing + setup wizard | `chatty/backend/core/providers/`, `chatty/frontend/src/setup/` |
| CRM core (schema, router, tools, smart import) — **landed #3** as `backend/crm/` + `frontend/src/crm/` + `frontend/src/shared/` | `chatty/backend/integrations/crm_lite/`, `chatty/frontend/src/crm/` |
| Assistant engine — **chat loop, tool registry, confirmations, uploads landed #4** as `backend/assistant/` + `frontend/src/assistant/`; **memory (facts + FTS) + dreaming (pure-algorithmic usage scoring + fact soft-archival) landed #5** as `backend/memory/` + `backend/dreaming/` (dreaming's archival unit is the fact row, not context files — CakeCRM has no file store; driven by #6's reminder tick) | `chatty/backend/core/agents/` |
| Heartbeat + background AI turn — **landed #6** as `backend/heartbeat/` (60s APScheduler tick) + `backend/assistant/background.py` (non-SSE `run_background_turn`: auto-approved writes under a server-enforced tool allowlist + `WRITE_BUDGET_BACKGROUND`) | `chatty/backend/core/agents/background_runner.py` + `main.py` scheduler wiring |
| Reminders (own table, recurrence math, agent tools + **net-new full CRUD REST/UI**) — **landed #6** as `backend/reminders/` + `frontend/src/crm/RemindersPage.tsx` | `chatty/backend/core/agents/reminders/` |
| Notifications (Web Push VAPID keys persisted in Postgres, `notify_user` tool, bell) + system alerts — **landed #6** as `backend/notifications/` + `backend/alerts/` + `frontend/src/crm/components/{NotificationsBell,NotificationSettings}.tsx` + `frontend/public/sw.js`. Telegram delivery goes out through `telegram.service.notify_linked_user` (the pure-sync channel #7 landed), via `_send_telegram`; WhatsApp not ported. Chatty's user-configurable `scheduled_actions` subsystem (leases/active-hours/triage/dashboards) deliberately deferred | `chatty/backend/core/agents/notifications/` + `alerts/` |
| Telegram — **landed #7** as `backend/telegram/*` + `frontend/src/crm/components/TelegramSettings.tsx`: single-assistant long-polling (one main-loop asyncio task offloads `getUpdates` via `to_thread` and drives `engine.chat` on the SAME loop as the SSE endpoint — provider async clients are loop-bound), Fernet-encrypted bot token on a `telegram_settings` singleton, one linked user via a single-use `link_code` (Telegram deep link), CRM write confirmations as inline-keyboard Approve/Deny buttons (mapped onto `engine.resolve_confirmation` + an empty-messages continuation, batched so it continues only once every write is resolved), and `telegram.service.notify_linked_user(text)->bool` as the pure-sync outbound channel #6 consumes. No webhooks, no group chat (deliberately cut). | `chatty/backend/integrations/telegram/` |
| Gmail (read + draft only: `gmail_connection` singleton, BYO OAuth at `/api/gmail`, tools `gmail_search`/`gmail_read_thread`/`gmail_create_draft`, guard test + SECURITY.md) — **landed #8** as `backend/gmail/` + `frontend/src/crm/components/GmailCard.tsx` | `chatty/backend/integrations/google/` |
| Gmail connection-race hardening (`connection_generation` optimistic lock + CAS on token persist; pending-draft binding through the shared confirm flow; ciphertext CAS on `mark_broken`; atomic clear-and-capture on disconnect/app-replace; capped recovery of attachment-stored text bodies) — **landed #43** across `backend/gmail/*` + `backend/assistant/{engine,history}.py` | Follow-up to #8 (no blueprint — back-port candidate to CAKE OS) |
| Gmail touch-scan heartbeat job (read-only inbox scan → sender→contact match → idempotent `email` touch logging feeding #16; `gmail_scan_state`/`gmail_scanned_messages`/`gmail_unmatched_correspondents` tables; own `gmail_scan` scheduler job; "create contact?" alerts) — **landed #17** as `backend/gmail_scan/` | New capability (no blueprint — back-port candidate to CAKE OS) |
| Kanban drag-and-drop | `cake_os/frontend/src/shared/dnd/` |
| Companies (first-class entity: `companies` table, `company_id` FKs, rollup detail page, text→FK backfill migration) — **landed #13** | `cake_os/backend/apps/crm/company_service.py` |
| Chatter/notes (`crm_chatter`) — **landed #15** as `backend/crm/chatter_service.py` + `frontend/src/crm/components/NotesThread.tsx` | `cake_os/backend/apps/crm/chatter_service.py` |
| Custom fields (EAV `crm_field_definitions`/`crm_field_values`, Settings editor, entity-form + detail-page value inputs, 6 `crm_*_fields` tools) — **landed #19** as `backend/crm/field_service.py` + `frontend/src/crm/components/{CustomFieldSettings,CustomFieldsSection,CustomFieldInputs}.tsx` | `cake_os/backend/apps/crm/field_service.py` |
| Touch counts + field provenance (`deals.ai_touch_*` cols + in-process recompute worker; `crm_field_provenance` + `AiBadge`/`ProvenanceBadge`/`TouchCountPill`) — **landed #16** as `backend/crm/touch_count_service.py` + `provenance_service.py` | `cake_os/backend/apps/crm/touch_count_service.py`, `provenance_service.py` |
| Scoring, analytics — analytics **landed #20** as `service.get_analytics()`/`summarize_analytics()` + `GET /api/crm/analytics` + `crm_analytics` tool + enriched `CrmDashboardPage` (win/loss, activity volume, read-time deal aging from existing timestamps — no migration; stage-duration metrics dropped, no stage-change audit trail; scoring still pending) | `cake_os/backend/apps/crm/*_service.py` |
| Assistant tool set (~43 tools) + sales behaviors | `cake_os/backend/apps/crm/tools/` + Casey's agent config |
| Pipeline facet filtering (client-side, no backend query params: `frontend/src/crm/pipelineFilters.ts` pure predicate + `components/PipelineFilterBar.tsx`, spliced into `PipelinePage`'s useMemo seam as `deals`→`filteredDeals`→`grouped`; facets = keyword/stage/value/close-date/last-activity; sessionStorage `crm_pipeline_filters`) — **landed #21**. Owner facet dropped (single-tenant); `get_pipeline()` gains a derived `last_activity_at` = MAX(deal `activity_log` rows + un-archived deal `crm_chatter` notes) via one UNION-ALL/GROUP BY join (NULL = no activity), plus `company_name`. Drag stays enabled while filtering (board is stage-only, index-safe). | `cake_os/docs/CRM_FILTER_DESIGN.md` + `cake_os/docs/solutions/architecture-patterns/client-side-facet-filtering.md` |
