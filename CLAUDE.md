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
- **Multi-provider AI** via the `AIProvider` ABC (Anthropic, OpenAI, Gemini, Ollama,
  Together). Never call a provider SDK directly from feature code. Cheap background
  AI work (touch counts, classification) uses the light tier via
  `resolve_tier_model()`.
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
- **API keys are entered in-app, encrypted at rest** (Fernet; key from env →
  OS keychain → file fallback) — never as env vars.

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
- Issue eligibility for the automated loop: open, unassigned, no `no-auto` label.
  Self-assign (or label `no-auto`) before working an issue manually.
- Respect "Blocked by: #N" lines in issue bodies — don't start an issue whose
  blockers aren't merged.
- Keep this CLAUDE.md updated in the same PR as any change to architecture,
  conventions, or the rules above.

## Source Map (where ported things come from)

| CakeCRM area | Source |
|---|---|
| Product shell (run.py, auth, 2FA, encryption, config, Railway) | `chatty/backend/` + `chatty/run.py` |
| Postgres pool + migration runner | `cake_os/backend/core/postgres.py` |
| AI providers + pricing + setup wizard | `chatty/backend/core/providers/`, `chatty/frontend/src/setup/` |
| CRM core (schema, router, tools, smart import) | `chatty/backend/integrations/crm_lite/`, `chatty/frontend/src/crm/` |
| Assistant engine (chat loop, tools, memory, dreaming, heartbeat, reminders, notifications) | `chatty/backend/core/agents/` |
| Telegram | `chatty/backend/integrations/telegram/` |
| Gmail (reduced to read + draft) | `chatty/backend/integrations/google/` |
| Kanban drag-and-drop | `cake_os/frontend/src/shared/dnd/` |
| Companies, chatter, scoring, custom fields, analytics, provenance, touch counts | `cake_os/backend/apps/crm/*_service.py` |
| Assistant tool set (~43 tools) + sales behaviors | `cake_os/backend/apps/crm/tools/` + Casey's agent config |
| Pipeline facet filtering | `cake_os/docs/CRM_FILTER_DESIGN.md` |
