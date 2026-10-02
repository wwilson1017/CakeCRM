# Sync ledger

Every feature that has crossed into CakeCRM from a blueprint repo, and the PR that
brought it. This is the idempotency record for the **cake_os → CakeCRM sync bot**
(`docs/SYNC.md`): before porting an upstream change, check here for whether it already
landed.

Rows are added **only by the port PR itself** — the intake bot never writes to this
file. That keeps the ledger something a human reviewed, rather than something a machine
asserted.

**Schema** — both tables below use the same four columns, so tooling parses one shape:

| Column | Contents |
|---|---|
| `Feature` | Short name of the capability. Prose detail lives in the Source Map (`docs/agents/source-map.md`), not here. |
| `Source` | Where it came from upstream. For live rows: `PR #<n> (<short sha>)`. |
| `CakeCRM PR / issue` | The `#N` that landed it here. |
| `Date` | Merge date (`YYYY-MM-DD`), or `—` where it was never recorded. |

> **Privacy note.** The `Feature` cell is human prose written in a reviewed PR. That is
> review policy, not a structural guarantee — unlike the intake issues themselves, which
> physically cannot carry upstream text. Keep entries generic: no customer, staff, or
> product names. See `SECURITY.md`.

---

## Pre-ledger (seeded — before the sync bot existed)

Reconstructed from the Source Map (then in `AGENTS.md`, now `docs/agents/source-map.md`) at the time issue #23 landed. These predate
any SHA-level tracking, so `Date` is `—` throughout: the merge SHAs were never recorded
and guessing them would be worse than leaving them blank.

Note that `chatty` rows are historical only. chatty was **retired as a sync source**
(issue #24, closed at the #23 gate); cake_os is the single live sync path going forward.

| Feature | Source | CakeCRM PR / issue | Date |
|---|---|---|---|
| Product shell (run.py, auth, 2FA, encryption, config, Railway) | `chatty/backend/` + `chatty/run.py` | seed | — |
| Postgres pool + migration runner | `cake_os/backend/core/postgres.py` | seed | — |
| AI providers + pricing + setup wizard | `chatty/backend/core/providers/` | seed | — |
| CRM core (schema, router, tools, smart import) | `chatty/backend/integrations/crm_lite/` | #3 | — |
| Assistant engine (chat loop, tool registry, confirmations, uploads) | `chatty/backend/core/agents/` | #4 | — |
| Assistant memory + dreaming | `chatty/backend/core/agents/` | #5 | — |
| Heartbeat + background AI turn | `chatty/backend/core/agents/background_runner.py` | #6 | — |
| Reminders (landed #6, removed in #188) | `chatty/backend/core/agents/reminders/` | #6 | — |
| Notifications + system alerts | `chatty/backend/core/agents/notifications/` | #6 | — |
| Telegram integration | `chatty/backend/integrations/telegram/` | #7 | — |
| Gmail (read + draft only) | `chatty/backend/integrations/google/` | #8 | — |
| CRM-first shell | `cake_os` CRM UX | #9 | — |
| Companies (first-class entity) | `cake_os/backend/apps/crm/company_service.py` | #13 | — |
| Context-aware assistant drawer | `cake_os` CRM UX | #14 | — |
| Chatter / notes | `cake_os/backend/apps/crm/chatter_service.py` | #15 | — |
| Touch counts + field provenance | `cake_os/backend/apps/crm/touch_count_service.py`, `provenance_service.py` | #16 | — |
| Gmail touch-scan heartbeat job | New capability (no blueprint) | #17 | — |
| Lead scoring | `cake_os/backend/apps/crm/scoring_service.py` | #18 | — |
| Custom fields (EAV) | `cake_os/backend/apps/crm/field_service.py` | #19 | — |
| CRM analytics | `cake_os/backend/apps/crm/*_service.py` | #20 | — |
| Pipeline facet filtering | `cake_os/docs/CRM_FILTER_DESIGN.md` | #21 | — |
| Assistant sales tool set + behaviors (phases 1–3) | `cake_os/backend/apps/crm/tools/` | #22 | — |
| Company link coherence | New capability | #35 | — |
| Gmail connection-race hardening | Follow-up to #8 (no blueprint) | #43 | — |
| Kanban drag-and-drop | `cake_os/frontend/src/shared/dnd/` | #12 | — |
| Theme + dark mode (fixed palette) | `cake_os/frontend/src/index.css` | #54 | — |
| Shared collection layer | `cake_os/frontend/src/shared/{search,listview,collection,overlay}/` | #73 | — |
| Dashboard parity (stat row + Weekly Touches) | `cake_os/frontend/src/apps/crm/components/DashboardTab.tsx` | #76 | — |
| Pipeline parity (board + list on the collection layer) | `cake_os/frontend/src/apps/crm/components/PipelineTab.tsx` (+ `StageChipBar.tsx`, `pipelineListColumns.tsx`, `collectionConfig.ts`, `pipelineSort.ts`, `pipelineBoard.ts`) | #74 | — |
| DB-backed login credential + password change | New capability (no blueprint) | #78 | — |
| Sync bot receiving half (this file) | New capability | #23 | — |

## Ledger (live — one row per synced upstream change)

Populated by port PRs from here on.

| Feature | Source | CakeCRM PR / issue | Date |
|---|---|---|---|
| Todo-GTD undo pill — a 7s undo block for marking a todo done anywhere and for filing an inbox item under a context | PR #2904 (`77a3337ff`), PR #2939 (`3b262b0d0`) | #231 | — |
| Todo GTD Someday, Done and Projects on the shared collection layer (per-page configs, a stretched-link project card, the public todo app's boot-split deny entry retired) | PR #1840 (`2a639f836`), GTD half only | #234 | — |
| Todo-GTD copy buttons + long-title wrapping | `e00e05cde` | #151 | — |
| Pipeline board bounded to the window (its scrollbars stay on screen), the first corpus sweep gated on tab visibility, and a last-contact line on Won cards | PR #1956 (`e605b49c4`) for the bounding — the half #147 deferred; PR #1828 (`029983b4f`) and PR #1872 (`25d45eb9a`) consulted and diverged from, see the Source Map | #129 | — |
| Todo GTD triage & edit-sheet parity (due-date cue, inline Notes, real Context picker, legible step headings) | PR #2424 (`e3c06bba`), PR #2539 (`b936ac29`) | #150 | 2026-09-04 |
| Keyboard-reachable `shared/listview` rows (WCAG 2.1.1) — every collection adopter inherits it, plus the consumer-side convention of a real link in a route-shaped surface's title cell | PR #1979 (39d1033) | #148 | — |
| Route-level code splitting of the frontend boot path (`Root.tsx` dispatch with both branches lazy, lazy route table + one shared GTD `pages.ts`, lazy assistant drawer, `ChunkErrorBoundary` + `BootFallback`, `bootSplit` source guard + `bootSplitBuild` chunk-graph guard) | PR #2150 (`1c10ccf`) | #149 | 2026-09-04 |
| Kanban pointer-based drop-target resolution (short/empty columns accept a drop at any height) | PR #1813 (`f367e7cf8`), plus the board-box clip from PR #1956 (`e605b49c4`) — whose board-bounding half landed separately in #129 | #147 | 2026-09-04 |
| Weekly Touches grouped per sales rep, plus a per-rep page listing that rep's touched open deals in full | `backend/apps/crm/dashboard_service.py` + `frontend/src/apps/crm/components/{WeeklyTouchesCard,WeeklyTouchesDetailPage}.tsx` @ `8e4d202f9` (cake_os #602) | #146 | 2026-09-04 |
| Reports tab — single-page company rollup with a merged notes+activity timeline | `backend/apps/crm/report_service.py` + `frontend/src/apps/crm/{companyRollup.ts,components/{ReportsTab,CompanyRollupReport,CompanyTimeline}.tsx}` @ `74f902684` | #144 | — |
| Per-deal deep links (link-shape module + `url` on deal tool results) | PR #1542 (`732678bd2`) | #145 | 2026-09-04 |
| Todo GTD project name + notes click-to-edit on the detail page; `InlineTitle` `body` variant + `onCancel(reason)` | PR #3201 (`6270fa963`, cake_os #3198) | #232 | — |
| Capture page retries focus on open and re-attempts on resume, gated to a standalone (PWA) launch — inline script in the backend-rendered `_CAPTURE_HTML`, not the blueprint's React effect | PR #3051 (`d49442c6e`) | #233 | 2026-09-29 |
| Bare deal ids in outbound assistant text rewritten to `Title (url)` (Telegram replies + `notify_user`) | PR #3175 (`173693bd2`) | #238 | — |
| Assistant company tools take an owner (`owner` word on `crm_create_company`/`crm_update_company`) | PR #3184 (`150982d02`) | #237 | — |
