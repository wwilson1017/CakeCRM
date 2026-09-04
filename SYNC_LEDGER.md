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
| `Feature` | Short name of the capability. Prose detail lives in `CLAUDE.md`'s Source Map, not here. |
| `Source` | Where it came from upstream. For live rows: `PR #<n> (<short sha>)`. |
| `CakeCRM PR / issue` | The `#N` that landed it here. |
| `Date` | Merge date (`YYYY-MM-DD`), or `—` where it was never recorded. |

> **Privacy note.** The `Feature` cell is human prose written in a reviewed PR. That is
> review policy, not a structural guarantee — unlike the intake issues themselves, which
> physically cannot carry upstream text. Keep entries generic: no customer, staff, or
> product names. See `SECURITY.md`.

---

## Pre-ledger (seeded — before the sync bot existed)

Reconstructed from `CLAUDE.md`'s Source Map at the time issue #23 landed. These predate
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
| Reminders | `chatty/backend/core/agents/reminders/` | #6 | — |
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
| DB-backed login credential + password change | New capability (no blueprint) | #78 | — |
| Sync bot receiving half (this file) | New capability | #23 | — |

## Ledger (live — one row per synced upstream change)

Populated by port PRs from here on.

| Feature | Source | CakeCRM PR / issue | Date |
|---|---|---|---|
| Per-deal deep links (link-shape module + `url` on deal tool results) | PR #1542 (`732678bd2`) | #145 | 2026-09-04 |
