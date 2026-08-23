# CakeCRM — Project Instructions

## What This Is

Free, open-source, self-hostable CRM with a built-in AI sales assistant. Seeded from
Chatty's product shell and agent engine; CRM features ported from the CAKE OS CRM;
assistant capability bar is Casey (CAKE OS's sales agent). **Multi-user since #60**
(accounts, admin/member roles, record ownership; assistant + channel isolation is
Phase B). PostgreSQL, FastAPI, React/Vite.
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
  oversize; file attachments are NEVER fetched (guarded on `filename` OR a
  `Content-Disposition: attachment` header — a nameless attachment is still a file).
  Fetching is deferred until after the inline walk and resolves plain-before-HTML, so
  a discarded alternative never spends a budget slot, and the budget is spent
  newest-message-first so long threads don't return the latest replies blank. Note `store.get_row()` swallows
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
  and gated off `ai_ready`. The assistant's sales working practices live in
  `identity.SALES_GUIDE` — a **static** constant appended alongside
  `CONFIRMATION_NOTE`/`MEMORY_NOTE`, deliberately NOT inside `DEFAULT_PERSONALITY`,
  because a user-written personality replaces that string wholesale and would silently
  switch off every CRM discipline with it. The assistant's **second execution mode** (landed #6,
  `backend/assistant/background.py`) is a non-SSE `run_background_turn` for
  autonomous work (the heartbeat + reminder firing): it reuses the same
  `ToolRegistry`/`build_tool_turn` loop but, having no human to confirm writes,
  runs under a **server-enforced tool allowlist of READ tools + `notify_user` only**
  (no CRM writes at all — enforced at both advertisement and execution) plus a
  `WRITE_BUDGET_BACKGROUND`, with untrusted reminder/CRM text kept in the user
  message, never the system prompt. So a prompt injection via reminder/CRM content
  can at worst send one notification, never create/log/update/delete a record.
  **Since #22 that ceiling is unchanged but the on-ramp is wider, deliberately:** the
  new read tools put raw per-record free text in front of the unattended turn for the
  first time — `crm_scan_gaps` returns `crm_field_provenance.value_snapshot` verbatim
  and `crm_find_duplicates` returns deal titles / contact names, where the earlier
  background-callable reads (`crm_dashboard`, `crm_analytics`) exposed only structured
  aggregates. Accepted because the blast radius is still exactly one `notify_user` and
  the alternative — a second, narrower payload shape per tool for background turns —
  buys nothing against a ceiling that already holds. Mitigated in
  `heartbeat.service._heartbeat_prompt`, which states plainly that everything a CRM
  tool returns is DATA the user or a third party typed, never instructions. Any future
  background-callable read should assume its payload can carry hostile text.
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
  Assistant chat history and memory are still install-wide — Phase B of #60.
- **Accounts, roles and record ownership** (#60 Phase A) — the install has real
  `users` (email + bcrypt + `admin`/`member` + `is_active` + per-user `token_epoch`),
  and the shared `AUTH_PASSWORD` login is gone. Authorization has exactly **two**
  enforcement points: `core.auth.get_current_user` (authentication AND liveness — one
  indexed PK lookup per request, so a deactivation, a demotion or a password change
  bites on the *next* request, not at token expiry) and `require_admin`, applied
  per-route to an enumerated set that `backend/tests/test_route_authz.py` pins **in
  both directions** — adding an ungated admin route fails CI, and so does removing a
  gate. The JWT carries `sub` (user id) and `pwd_epoch` and deliberately **not**
  `role`: a role in a token can only ever be stale. `get_current_user` is a sync
  `def` on purpose — it does blocking psycopg2 I/O, and an `async` dependency would
  run it on the event loop and stall every SSE stream.
  **Ownership is NOT access control.** `owner_id` on contacts/companies/deals/tasks
  is an assignment, a filter and an analytics dimension; any member can read, edit,
  delete and reassign any record (no per-object ACLs — a product decision, stated
  rather than implied). It is nullable forever: `NULL` = unassigned, a real state the
  Gmail scan, the assistant and #61's importer all legitimately produce. Owner
  filters live in the **shared** WHERE builders so they reach the COUNT and the page
  query together — a one-sided filter doesn't fail, it silently reports a total that
  disagrees with the rows. The pipeline board deliberately has **no** server-side
  owner param: it is unpaginated, every other #21 facet is client-side, and
  `get_pipeline` returns deals alongside a separately-computed `stage_summary` that a
  one-sided filter would put out of step with the cards.
  **Ownership and authorship are different columns** (the cake_os #1454/#1532
  lesson): `activity_log.actor_id` and `crm_chatter.author_id` record who DID the
  work, so per-rep activity credits a rep for work on a colleague's record. Only the
  human REST paths stamp them — the assistant's tool executors don't thread identity
  in Phase A, so their writes stay NULL and roll up as "Unattributed". Phase A
  therefore **undercounts** assistant-delegated work but never **misattributes** it.
  The per-rep query excludes `provenance_service.confirm`'s housekeeping notes and
  `merge_deals`' copies (the copies leave the originals on the archived source, so
  both read `archived = 0` and one note would count twice).
  **Bootstrap** (`users/bootstrap.py`, lifespan, after migrations, before
  `apply_password_reset_env`) seeds the first admin when `users` is empty and, on an
  upgrade, **carries the bcrypt hash out of the pre-#60 `auth_credential` singleton**
  — an owner who changed their password in-app doesn't know `AUTH_PASSWORD` any more
  (#78 made it inert), so re-deriving from the env var would lock them out. It also
  claims the orphaned `totp_config`/`trusted_devices` rows and backfills `owner_id`,
  all in one transaction under an advisory lock. It does **not** clear
  `auth_credential`: pre-#60 code reads a NULL hash as permission to fall back to
  `AUTH_PASSWORD`, so clearing it would let a rolled-back build accept the superseded
  env password. That table is vestigial and a later release drops it.
  `AUTH_PASSWORD_RESET` now also re-activates the target admin and **clears their
  2FA** — a password-only rescue cannot help an operator who lost their
  authenticator. The same reasoning gives `POST /api/users/{id}/password` an **opt-in**
  `clear_two_factor`, which is the only recovery for a MEMBER who lost both their
  authenticator and their backup codes; it is opt-in rather than automatic because it
  genuinely weakens that account. That route also **refuses a self-reset** — it skips
  the current-password and 2FA checks `/api/auth/change-password` enforces, which is
  right for helping a colleague and wrong as a second, weaker self-service path. 2FA
  itself is re-keyed per user, and enabling or disabling it wipes that user's trusted
  devices in the SAME transaction — as does a password change, which is one
  transaction covering the hash, the epoch bump and the revocation.
  **The upgrade is a one-way door.** The migration drops `totp_config.id`, so a
  pre-#60 binary cannot complete a login for a 2FA account afterwards; rolling back
  means restoring a `pg_dump`, and the README says so. Preserving `auth_credential`
  buys only that an old process would still check the credential the user chose
  rather than the stale `AUTH_PASSWORD` — it does not make the schema reversible.
  Login is rate-limited **per account and per IP** (10/5min and 50/5min): a single
  per-IP bucket is a denial of service on your own team, since one office shares a
  NAT address. `login`, `/api/me`, change-password and the `/api/users` handlers are
  sync `def`s for the same reason `get_current_user` is — they do blocking psycopg2
  and bcrypt work and would otherwise run it on the event loop.
  In the UI, `SettingsPage` hides the install-configuration cards (branding,
  Telegram, custom fields, Gmail) from members, the way the Team card hides itself;
  Notifications and Change password stay, because they configure the person, not the
  install.
  **Still install-wide, deliberately (Phase B):** assistant chat history and memory,
  the Gmail connection, the Telegram binding, reminders, notifications and alerts.
  Every active seat gets the assistant (Will's §15 ruling — no temporary admin gate
  someone has to remember to remove), so a member can have it read the admin's
  connected mailbox. `GET /api/telegram/status` redacts the link code for members,
  since that code claims the one binding and would otherwise defeat the admin gate on
  connect/disconnect. The dead `MULTI_USER_ENABLED` flag was deleted — grep found
  only its own definition and the docstring advertising it.
- **One database: PostgreSQL, and it's mandatory** — the backend refuses to start
  without `DATABASE_URL` (decided 2026-07-18; single engine, ready for multi-user
  growth). Locally `docker compose up -d`; on Railway the template provisions
  Postgres and injects `DATABASE_URL`. No Redis or other external services.
  Required env vars: `AUTH_PASSWORD` + `DATABASE_URL`; `ADMIN_EMAIL`/`ADMIN_NAME`
  seed the first admin's identity; `JWT_SECRET` and `ENCRYPTION_KEY` auto-generate. **The login credential is DB-backed** (#78): the
  `auth_credential` singleton holds a bcrypt hash the logged-in user changes from
  `/crm/settings`, and `core.auth.verify_password()` resolves DB-hash-first, falling
  back to `AUTH_PASSWORD` only while that hash IS NULL — so the env var is a
  *bootstrap* value that goes inert once the user sets their own password, and can
  never silently override it on a later boot. Every credential check in the app routes
  through that one function (login, the three 2FA confirmation endpoints, and
  change-password's pre-check), so the resolution order has exactly one definition.
  `POST /api/auth/change-password` verifies the current password and writes the new
  hash in ONE `SELECT … FOR UPDATE` transaction (`set_password`); the migration
  **seeds** the singleton row with a NULL hash so that lock always has a row to hold
  — locking an absent row is a no-op, which would let two concurrent first-time
  changes both pass. The endpoint also runs a **non-consuming `verify_password`
  pre-check before the 2FA code**, because verifying a code spends it (`verify_totp_code`
  burns the timeslot, `consume_backup_code` destroys a single-use code) and a typo in the
  current-password field must not cost the user a recovery code; `set_password`'s locked
  re-check stays authoritative. It answers a wrong current password with **400, not 401**,
  because the frontend `api()` wrapper treats every 401 as an expired session and
  ejects the user to `/login`. On success it revokes trusted 2FA devices and returns
  a fresh token. **Changing the password ends every other session immediately**:
  `auth_credential.token_epoch` is bumped in the *same statement* as the hash and
  stamped into every JWT as `pwd_epoch` (injected centrally in `create_access_token`,
  so all four mint sites carry it), and `get_current_user` rejects a token whose epoch
  is stale. The epoch is cached in-process — the deploy pins `gunicorn --workers 1` and
  the **happy path does zero DB reads**; it is loaded once in the lifespan. A *mismatch*
  re-reads before rejecting, so a stale cache (a multi-worker fork) self-heals instead of
  spuriously signing valid sessions out. A token predating the feature has no claim and
  reads as epoch 0 — deploying this signs nobody out; the first password change does.
  `AUTH_PASSWORD_RESET` bumps the epoch too (a rescue must end the sessions that may have
  caused the lockout). `AUTH_PASSWORD_RESET` is the operator's
  recovery lever, consumed in the lifespan right after `run_migrations()`: it
  overwrites the stored hash on **every** boot while set (a lever that disarms itself
  can only be pulled once) and logs a loud warning to remove it. Login **fails
  closed** — an unreadable credential is a 503, never a fallback to the env var.
  Schema is owned by `backend/migrations/*.sql`,
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
  `contacts.company` column stays in place, non-authoritative (the freetext↔link
  combobox merge is deliberate follow-up); a second one-shot backfill migration
  (`20260816203810_company_link_backfill.sql`) repairs installs that imported between
  #13 and #35 by linking contacts that are still `company_id IS NULL` with matching
  text. Unlike #13's it deliberately does **not** inherit company onto deals: `NULL`
  no longer unambiguously means "never set" (a user can clear a deal's company), and
  no ingestion path creates deals anyway. User-defined **custom fields** (#19) add a
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
  archived deal's open tasks drop out of `list_tasks` (archiving is the user's "stop
  nagging me" gesture, and the heartbeat is told to read that list), while `activity_log`
  is never filtered — it records what actually happened, and you need it to decide
  whether to restore. Deliberate exceptions: `get_deal` (fetch-by-id must still resolve
  an archived deal so it can be shown/restored/merged), the is-the-CRM-empty counts, and
  `crm_search_deals(include_archived=true)` — the ONE read that can surface an archived
  deal, so an accidental archive or a wrong merge stays recoverable (there is no
  archived-deals UI yet). A stage change on an archived deal is refused outright: won +
  archived would book revenue no report can see.
  `deal_stage_events` is the one CRM table with a real FK to `deals`, so it MUST stay in
  every `TRUNCATE` sweep or the CRM reset errors out. `merge_deals` repoints
  activity/tasks, copies notes with a `[Merged from deal #N]` marker, gap-fills custom
  fields (the target's own values always win), and archives — never deletes — the
  source. Chatter now also attaches to **companies** (zero-migration: `entity_type` is
  free TEXT), cleaned in `delete_company`. **Phase 2** adds the two composing reads:
  `crm_get_deal_health` (one deal — #18's `score_deal()` plus days-in-stage,
  days-since-touch, open/overdue tasks and missing links, reduced to a `flags` list;
  it composes and never recomputes the scoring model) and
  `crm_get_pipeline_analytics` (time-in-stage, per-stage conversion and velocity read
  from `deal_stage_events` — precisely the half #20 had to drop for want of a
  stage-change trail, so the two analytics tools are complementary, not overlapping).
  Because that log only began at Phase 1, every response carries `history_since` /
  `history_covers_window` and `SALES_GUIDE` tells the assistant to state the real span
  rather than present a partial funnel as the whole picture. The ~44 `crm_*` agent tools + executors are
  collected UNCONDITIONALLY via `crm.tools.get_crm_tools()` — each def carries a
  `"writes"` flag (the single source of truth for the assistant's confirmation gate),
  consumed by `assistant.registry.ToolRegistry` (landed #4). The four read-only
  intelligence tools (`crm_get_stale_deals`, `crm_get_contact_staleness`,
  `crm_find_duplicates`, `crm_scan_gaps`, all in `crm/analytics_service.py`) are pure
  SQL — keyless — and because `writes:False` derives the background allowlist they are
  heartbeat-callable for free. **AI touch counts +
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
  `crm_meta.ai_key_prompt_dismissed`. Branding (company name / logo) is edited at
  `/crm/settings`, consuming the existing `/api/branding`. The **theme itself is fixed**
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
  **ink**, not the accent: a single dark tint would vanish on a dark surface. The brand
  red is identical in both themes as a **fill**, but accent used as *text or an icon*
  routes through `--color-ck-accent-text` (`ACCENT_TEXT`), which the `.dark` block
  lightens — the fixed red is only 3.15:1 on the dark card and fails WCAG AA as body
  text, the same reason the status and stage hues lighten. Fonts are **self-hosted**
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
  (`frontend/src/crm/RecordContext.tsx`, set by the detail pages + `DealDetailSheet`)
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
  contacts have a server-sorted `lead_score` column (`DESC NULLS LAST`).
- **API keys are entered in-app, encrypted at rest** (Fernet; key from env →
  OS keychain → file fallback) — never as env vars.
- **Backend tests** live in `backend/tests/` (config in `backend/pytest.ini`,
  `asyncio_mode = auto`). The default `pytest` run is **hermetic** — pg helpers and
  provider SDKs are mocked, encryption runs against a per-test key — so the CI gate
  needs no database. Tests that need a real PostgreSQL are marked
  `@pytest.mark.integration` and deselected by default (`addopts = -m "not
  integration"`); run them with `pytest -m integration` and a reachable
  `TEST_ADMIN_DSN`. No `skip`/`xfail`/`# noqa`/`eslint-disable` — fix root causes.
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

## Don't Do This

- Never add an email-send tool or widen Gmail scopes/capabilities beyond read +
  create-draft (see above).
- Never create runtime SQLite stores or ad-hoc schema — Postgres migrations own
  the schema. When a check-then-write spans reads and updates, do it in one
  transaction with `SELECT ... FOR UPDATE` (see `core/auth_2fa.py`).
- Never commit TN Cheesecake internals: no real prospect/customer data, no TNC
  staff/product names, no internal hostnames or secrets. Ported prompts (Casey's)
  must be genericized. This repo goes public at launch and history is forever.
  Enforced by `backend/tests/test_prompt_genericization.py` (#22), which scans the
  **model-facing payload** — the assembled system prompt, every tool
  name/description/schema, the heartbeat prompt, and the UI starter chips — and fails
  CI on any company/product/vertical token. Source *comments* may still cite the
  blueprint by name; shipped prompt text may not.
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
- Keep this CLAUDE.md updated in the same PR as any change to architecture,
  conventions, or the rules above.

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

| CakeCRM area | Source |
|---|---|
| Product shell (run.py, auth, 2FA, encryption, config, Railway) | `chatty/backend/` + `chatty/run.py` |
| Accounts, roles, per-user 2FA, record ownership + per-rep analytics — **landed #60 (Phase A)** as `backend/users/` (`service`/`router`/`bootstrap`) + reworked `core/{auth,auth_2fa,config}.py` + `20260821100126_multi_user.sql` + `frontend/src/crm/{useUsers.ts,components/{TeamSettings,OwnerSelect,OwnerScopeToggle}.tsx}`. Phase B (assistant memory/chat partitioning, per-user Telegram, owner-routed notifications, owner-aware agent tools) is a separate plan. Corrects the issue's premise: cake_os uses `owner_email` TEXT with no FK, so this is an FK design, not a carry | New capability (no blueprint — `cake_os/backend/apps/crm/analytics_service.get_rep_performance` for the per-rep shape only) |
| DB-backed login credential + in-app password change (`auth_credential` singleton, `POST /api/auth/change-password`, `AUTH_PASSWORD_RESET` recovery lever) — **landed #78** as `backend/core/auth.py` + `frontend/src/crm/components/ChangePasswordCard.tsx` | New capability (no blueprint — back-port candidate to CAKE OS) |
| Postgres pool + migration runner | `cake_os/backend/core/postgres.py` |
| AI providers + pricing + setup wizard | `chatty/backend/core/providers/`, `chatty/frontend/src/setup/` |
| CRM core (schema, router, tools, smart import) — **landed #3** as `backend/crm/` + `frontend/src/crm/` + `frontend/src/shared/` | `chatty/backend/integrations/crm_lite/`, `chatty/frontend/src/crm/` |
| Assistant engine — **chat loop, tool registry, confirmations, uploads landed #4** as `backend/assistant/` + `frontend/src/assistant/`; **memory (facts + FTS) + dreaming (pure-algorithmic usage scoring + fact soft-archival) landed #5** as `backend/memory/` + `backend/dreaming/` (dreaming's archival unit is the fact row, not context files — CakeCRM has no file store; driven by #6's reminder tick) | `chatty/backend/core/agents/` |
| Heartbeat + background AI turn — **landed #6** as `backend/heartbeat/` (60s APScheduler tick) + `backend/assistant/background.py` (non-SSE `run_background_turn`: auto-approved writes under a server-enforced tool allowlist + `WRITE_BUDGET_BACKGROUND`). The scheduler now runs **four** jobs, split by one rule the code states explicitly: **local SQL rides `reminder_tick`** (#5 dreaming, #18's score refresh), **network- or AI-bound work gets its OWN `add_job`** (`heartbeat_turn`, #17's `gmail_scan`, #22 Phase 3's `proactive`) so a hung request can never delay reminder delivery | `chatty/backend/core/agents/background_runner.py` + `main.py` scheduler wiring |
| Reminders (own table, recurrence math, agent tools + **net-new full CRUD REST/UI**) — **landed #6** as `backend/reminders/` + `frontend/src/crm/RemindersPage.tsx` | `chatty/backend/core/agents/reminders/` |
| Notifications (Web Push VAPID keys persisted in Postgres, `notify_user` tool, bell) + system alerts — **landed #6** as `backend/notifications/` + `backend/alerts/` + `frontend/src/crm/components/{NotificationsBell,NotificationSettings}.tsx` + `frontend/public/sw.js`. Telegram delivery goes out through `telegram.service.notify_linked_user` (the pure-sync channel #7 landed), via `_send_telegram`; WhatsApp not ported. Chatty's user-configurable `scheduled_actions` subsystem (leases/active-hours/triage/dashboards) deliberately deferred | `chatty/backend/core/agents/notifications/` + `alerts/` |
| Telegram — **landed #7** as `backend/telegram/*` + `frontend/src/crm/components/TelegramSettings.tsx`: single-assistant long-polling (one main-loop asyncio task offloads `getUpdates` via `to_thread` and drives `engine.chat` on the SAME loop as the SSE endpoint — provider async clients are loop-bound), Fernet-encrypted bot token on a `telegram_settings` singleton, one linked user via a single-use `link_code` (Telegram deep link), CRM write confirmations as inline-keyboard Approve/Deny buttons (mapped onto `engine.resolve_confirmation` + an empty-messages continuation, batched so it continues only once every write is resolved), and `telegram.service.notify_linked_user(text)->bool` as the pure-sync outbound channel #6 consumes. No webhooks, no group chat (deliberately cut). | `chatty/backend/integrations/telegram/` |
| Gmail (read + draft only: `gmail_connection` singleton, BYO OAuth at `/api/gmail`, tools `gmail_search`/`gmail_read_thread`/`gmail_create_draft`, guard test + SECURITY.md) — **landed #8** as `backend/gmail/` + `frontend/src/crm/components/GmailCard.tsx` | `chatty/backend/integrations/google/` |
| Gmail connection-race hardening (`connection_generation` optimistic lock + CAS on token persist; pending-draft binding through the shared confirm flow; ciphertext CAS on `mark_broken`; atomic clear-and-capture on disconnect/app-replace; capped recovery of attachment-stored text bodies) — **landed #43** across `backend/gmail/*` + `backend/assistant/{engine,history}.py` | Follow-up to #8 (no blueprint — back-port candidate to CAKE OS) |
| Gmail touch-scan heartbeat job (read-only inbox scan → sender→contact match → idempotent `email` touch logging feeding #16; `gmail_scan_state`/`gmail_scanned_messages`/`gmail_unmatched_correspondents` tables; own `gmail_scan` scheduler job; "create contact?" alerts) — **landed #17** as `backend/gmail_scan/` | New capability (no blueprint — back-port candidate to CAKE OS) |
| Kanban drag-and-drop | `cake_os/frontend/src/shared/dnd/` |
| **Shared collection layer** (the CRM UI's interaction substrate) — **landed #73** as `frontend/src/shared/{search,listview,collection,overlay,hooks}/` with their co-located tests, plus the vitest harness. `search` (SearchInput/SearchFilterBar/SortControl + `match`/`persist`/`sort`), `listview` (ListView/ViewSwitcher + `headerSort`/`sortRows`), `collection` (CollectionView, `facets`, `useCollectionState`, `usePageAssembly`, `visibleOrder`, `views/{Cards,CollectionList,Kanban}`, `detail/CollectionDetail`, `closePolicy`), and `overlay/DetailModal` (pulled in because `CollectionDetail` wraps it). **#73 landed the layer ONLY — no CRM surface was rewired**; Pipeline/Contacts/detail adopt it in their own issues. Adaptations from the blueprint: `lucide-react` swapped for the in-repo `shared/icons.tsx` (no new dependency; `IconChevronLeft` added); the blueprint's `corrections` dependency reduced to a local 3-line `collection/voidedRowClass.ts` (the `voided` tri-state itself is generic and inert unless a config supplies `getVoided`); `shared/pagination` is NOT reachable from the layer and was not ported; the app-local `detailClosePolicy.ts` became `collection/closePolicy.ts` since CakeCRM has one CRM app; and the ported code was modernized for CakeCRM's stricter `eslint-plugin-react-hooks` v7 ruleset (`configs.recommended`, which the blueprint does not enable) — ref-writes-during-render and setState-in-effect were removed rather than suppressed. Styling: the layer keeps the blueprint's Tailwind utility classes, wired to CakeCRM's theme by **semantic aliases** in `index.css`'s `@theme static` (`cream`→`ck-card`, `sand`→`ck-bg`, `charcoal`→`ck-ink`, `muted`→`ck-ink-mute`, `line`→`ck-line-strong`, `brand`→`ck-accent`, `font-heading`→`font-display`) — declared as `var(...)` so `.dark` re-resolves them and the layer inherits dark mode with no `dark:` variants. Accent-as-TEXT deliberately routes through `text-ck-accent-text` per #54's WCAG rule, never `text-brand`. The `dock:` custom variant is defined in `index.css` for `DetailModal`'s takeover-vs-centred switch. | `cake_os/frontend/src/shared/{search,listview,collection,overlay}/` |
| Theme + dark mode (fixed `--color-ck-*` palette, `.dark` semantic-token override, self-hosted Montserrat/Open Sans, `useTheme` + `ThemeToggle`, accent-picker removal) — **landed #54** as `frontend/src/index.css` + `core/theme/useTheme.ts` + `crm/components/ThemeToggle.tsx` | `cake_os/frontend/src/index.css` + `core/theme/useTheme.ts` (read from `origin/master`) |
| Companies (first-class entity: `companies` table, `company_id` FKs, rollup detail page, text→FK backfill migration) — **landed #13** | `cake_os/backend/apps/crm/company_service.py` |
| Company link coherence (shared batched `resolve_or_create_company_ids()` resolve-or-auto-create on every ingestion path; contact list/search LEFT JOIN + `company_name`; second one-shot backfill) — **landed #35** | New capability (gate decision on issue #35; shared with the #61 importer) |
| Chatter/notes (`crm_chatter`) — **landed #15** as `backend/crm/chatter_service.py` + `frontend/src/crm/components/NotesThread.tsx` | `cake_os/backend/apps/crm/chatter_service.py` |
| Custom fields (EAV `crm_field_definitions`/`crm_field_values`, Settings editor, entity-form + detail-page value inputs, 6 `crm_*_fields` tools) — **landed #19** as `backend/crm/field_service.py` + `frontend/src/crm/components/{CustomFieldSettings,CustomFieldsSection,CustomFieldInputs}.tsx` | `cake_os/backend/apps/crm/field_service.py` |
| Touch counts + field provenance (`deals.ai_touch_*` cols + in-process recompute worker; `crm_field_provenance` + `AiBadge`/`ProvenanceBadge`/`TouchCountPill`) — **landed #16** as `backend/crm/touch_count_service.py` + `provenance_service.py` | `cake_os/backend/apps/crm/touch_count_service.py`, `provenance_service.py` |
| Lead scoring (pure-algorithmic `lead_score` 0-100 on deals+contacts; event-triggered inline recompute serialized by a per-entity advisory lock + a bounded daily heartbeat refresh + backfill endpoint/tools `crm_get_lead_score`/`crm_recompute_lead_scores`; sortable contact list + `ScorePill`) — **landed #18** as `backend/crm/scoring_service.py`. Since the #22 merge the write-event chokepoint for deal-column writes is `service._write_deal_update` (one hook covers the #22 lifecycle verbs too), with `archive_deal`/`merge_deals` hooked separately; archived deals are excluded from the contact deal-linkage aggregate | `cake_os/backend/apps/crm/scoring_service.py` |
| Scoring, analytics — analytics **landed #20** as `service.get_analytics()`/`summarize_analytics()` + `GET /api/crm/analytics` + `crm_analytics` tool + enriched `CrmDashboardPage` (win/loss, activity volume, read-time deal aging from existing timestamps — no migration; stage-duration metrics dropped, no stage-change audit trail; scoring landed separately in #18 above) | `cake_os/backend/apps/crm/*_service.py` |
| Dashboard parity (stat row + Weekly Touches) — **landed #76** as `service.get_weekly_touches()` + `GET /api/crm/dashboard/weekly-touches` + `frontend/src/crm/components/WeeklyTouchesCard.tsx`, plus `total_companies` on `get_dashboard_stats()` and a four-tile stat row on `CrmDashboardPage`. Ported for CONTENT parity, **additively** — the blueprint component is written against Tailwind classes (`bg-cream`/`text-charcoal`/`font-heading`) that #54 removed, and a literal replacement would have deleted #20's analytics sections. The blueprint's PER-REP grouping collapses to per-DEAL (no owner columns, single-user); the envelope keeps `window`/`total_touches`/`total_open_deals` with `deals` where it had `reps`, so a later multi-user port is a re-grouping. **Two separate signals, deliberately:** window MEMBERSHIP is `LAST_TOUCH_SQL` — the same keyless GREATEST(edit, newest activity, newest live note) expression `analytics_service.get_stale_deals` uses, so the card and the "Needs a touch" panel on the same page can never disagree about what a touch is — while the per-deal NUMBER is #16's `ai_touch_count`, which is what supplies the zero-keys gate (no provider ⇒ every count NULL ⇒ `computed_deals == 0` ⇒ the card renders `null`; it owns its own wrapper padding, so hiding leaves no gap). Membership is emphatically NOT `deals.ai_touch_count_at`: that column is #16's stale-write-guard watermark (it only advances when a provider answered and the CAS accepted, and falls back to the deal's `created_at`), so keying a window off it made every provider timeout silently drop a deal from an accountability number — and left numerator and denominator with different coverage on a half-backfilled install. Because membership is keyless, both sides of the ratio are coverage-independent. The touch-count colour ramp moved to `crm/constants.ts` and is shared with `TouchCountPill` (one number, one colour, app-wide). Window math mirrors the blueprint but on UTC calendar days — no CT convention here (and `get_dashboard_stats` already decides overdue against a UTC day), so the inclusive end-day bound is a plain +1 day, guarded against the `datetime.max` OverflowError that is not a `ValueError`; the filter is labelled UTC rather than converting per viewer. Drill-down deliberately omitted (issue #56). | `cake_os/frontend/src/apps/crm/components/DashboardTab.tsx` + `WeeklyTouchesCard.tsx` + `backend/apps/crm/dashboard_service.py` |
| Assistant tool set + sales behaviors — **Phase 1 landed #22**: 9 new tools (`crm_search_deals`, `crm_mark_deal_won`/`_lost`, `crm_archive_deal`, `crm_merge_deals`, `crm_get_stale_deals`, `crm_get_contact_staleness`, `crm_find_duplicates`, `crm_scan_gaps`) in `backend/crm/analytics_service.py` + `service.py`, parity closes (embedded `custom_fields`, tool-side `limit_per_stage`, `limit` on find/search, company chatter), the genericized static `identity.SALES_GUIDE` prompt block + sales `QuickActions`. **Phases 2 + 3 landed together** once #17/#18/#20 all merged (the three-PR split was dependency ordering, and every dependency cleared at once): **Phase 2** = `crm_get_deal_health` + `crm_get_pipeline_analytics` in `analytics_service.py` (see the CRM bullet above); **Phase 3** = `backend/proactive/` — a daily pipeline digest and stale-deal / untouched-contact nudges on their own `proactive` scheduler job. Both are **keyless-first**: the digest is deterministic SQL and the nudges read Phase 1's pure-SQL detectors, with an optional single `run_background_turn` (read tools + `notify_user`, digest numbers in the USER message) adding at most one extra notification when a provider exists. Every send **claims before it delivers** — the digest via a one-statement rowcount UPDATE on `heartbeat_state` (so two ticks can't both push), each nudge via a conditional upsert on `proactive_nudges` — because a crash that loses one notification beats one that re-sends every tick. `proactive_nudges` is polymorphic and FK-less, so it MUST stay in the `_truncate_all` sweep. NOT ported: `get_rep_performance` (no owner columns), `enrich_field` (no web tools), lead-import tools (own issue) | `cake_os/backend/apps/crm/tools/` + the blueprint sales agent's config |
| Pipeline facet filtering (client-side, no backend query params: `frontend/src/crm/pipelineFilters.ts` pure predicate + `components/PipelineFilterBar.tsx`, spliced into `PipelinePage`'s useMemo seam as `deals`→`filteredDeals`→`grouped`; facets = keyword/stage/value/close-date/last-activity; sessionStorage `crm_pipeline_filters`) — **landed #21**. Owner facet dropped (single-tenant); `get_pipeline()` gains a derived `last_activity_at` = MAX(deal `activity_log` rows + un-archived deal `crm_chatter` notes) via one UNION-ALL/GROUP BY join (NULL = no activity), plus `company_name`. Drag stays enabled while filtering (board is stage-only, index-safe). | `cake_os/docs/CRM_FILTER_DESIGN.md` + `cake_os/docs/solutions/architecture-patterns/client-side-facet-filtering.md` |
