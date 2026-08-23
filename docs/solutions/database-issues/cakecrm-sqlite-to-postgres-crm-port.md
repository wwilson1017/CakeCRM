---
title: Porting chatty's SQLite crm_lite to CakeCRM Postgres — translation gotchas
date: 2026-07-24
category: database-issues
module: backend/crm, frontend/src/crm
tags: [sqlite, postgres, psycopg2, port, timestamptz, model_dump, migration, crm]
problem_type: convention
---

## Context

Issue #3 ported chatty's SQLite `integrations/crm_lite/` into CakeCRM's always-on Postgres core
(`backend/crm/`) plus its React frontend. The straight code-move is easy; the bugs all live in the
SQLite→Postgres *semantic* gaps, and none of them are caught by `ruff` / `tsc` / build / a green unit
suite — several only surfaced under multi-stage review or a real-browser run. This is a reference for
the remaining cake_os feature ports (companies, chatter, scoring, custom fields, touch counts).

## Symptoms (each shipped-looking but wrong)

- Every activity/timeline timestamp renders the literal text **"Invalid Date"** in the browser.
- Deal/task edit forms can't **unlink** a contact — the UI shows "No contact" but the row keeps its old FK.
- Contact **search silently caps at 20** results with a wrong total (no "load more").
- A deal created with an out-of-range `stage` is **summed into pipeline value but invisible** in every column.
- Money loses cents on larger values; a bulk CSV import **freezes the whole single-process app**.

## Solution — the translation checklist

**Placeholders / ids / concurrency.** `?`→`%s`; `cursor.lastrowid`→`INSERT ... RETURNING id` then re-select
to hydrate; drop SQLite's `threading` write-lock (the pooled Postgres handles it); multi-statement writes go
in one `with get_connection() as conn:` transaction. Case-insensitive search: SQLite `LIKE`→Postgres `ILIKE`.
`COUNT(*)` → alias it (`AS cnt`) and read the dict key (the dict-returning helpers don't do `fetchone()[0]`).

**Timestamps (the "Invalid Date" trap).** SQLite `created_at` is naive TEXT (`datetime('now')`, no zone).
Postgres columns are `TIMESTAMPTZ`, and psycopg2 returns tz-aware datetimes that `core/postgres`'s
`_postprocess_value` `.isoformat()`s to `...+00:00`. Any ported frontend helper doing `new Date(iso + 'Z')`
now produces `...+00:00Z` → **Invalid Date** (no throw). Fix: `new Date(/[Zz]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z')`.
Seed data: emit UTC with an explicit offset (`strftime('%Y-%m-%d %H:%M:%S+00')`) so TIMESTAMPTZ parses unambiguously.

**Money.** SQLite `REAL` is float8; Postgres `REAL` is float4 and loses cents on large values. Use
`DOUBLE PRECISION` (preserves the source; psycopg2 returns `float`, so no Decimal/JSON-serialization change —
`NUMERIC` would return `Decimal` and break `json.dumps` unless you extend `_postprocess_value`).

**Partial-update null handling.** The ported `{k: v for k, v in body.model_dump().items() if v is not None}`
filter drops client-sent explicit `null`s, so a nullable FK can never be cleared. Use
`body.model_dump(exclude_unset=True)` and re-allow `None` only for the nullable columns
(`if v is not None or k in ("contact_id", "deal_id")`) — a blanket switch would let a client null a
`NOT NULL` column and 500.

**Validation asymmetry.** chatty validates only *some* enums (deal-stage on update). On Postgres, an unknown
`stage`/`status`/`priority` isn't rejected and becomes invisible in the UI's filter tabs (grouping is by a
fixed list). Coerce unknown enum values to their default in the service layer (covers the API *and* the
always-on `crm_*` tool executors, which pass `**kwargs` straight through). Add a `CHECK (completed IN (0,1))`
+ coerce truthy→1. Catch `psycopg2.errors.ForeignKeyViolation` on create/update → 400, not a raw 500.

**Pagination.** Forward `limit`/`offset` into the search branch and return a real `COUNT(*)` total — the
ported `search_contacts` defaulted to `limit=20` with no offset, silently truncating.

**Async + bulk work.** Every route is `async def` but the service layer is sync psycopg2. Harmless per query,
but a bulk CSV `/import` loop is thousands of blocking round-trips that freeze the single-process app (and its
Railway health checks): run the row loop via `fastapi.concurrency.run_in_threadpool`, and cap the row count.

**First-run state.** chatty's per-integration `enabled`/`demo_mode` credential flags are banned (CRM is
always-on core) — model first-run/sample-data state on a `crm_meta` singleton row instead, and guard
"clear example data" (only wipe when `sample_data_loaded`, and confirm in the UI since it also removes
anything the user added).

## Why This Works

Each item closes a place where SQLite's and Postgres's (or psycopg2's) semantics diverge — timezone-aware
datetimes, float4 vs float8, `exclude_unset` vs a None-filter, engine-side vs app-side concurrency. The green
unit suite doesn't catch them because they need either a real Postgres type round-trip, a real browser, or a
production-shaped latency model.

## Prevention

Audit every ported date helper, money column, partial-update filter, and enum against this list before
declaring a port done; drive the four acceptance surfaces (empty install, keyless CSV, AI smart-import, first-run
prompt) through a real booted app, not just TestClient.

## When to Apply

Any SQLite→Postgres migration of code that keeps its original query/serialization idioms.

**For a cake_os→CakeCRM port, apply only the second half.** cake_os's CRM is **PostgreSQL**, not
SQLite — there is no `backend/apps/crm/db.py`, and its services use the same `core.postgres` helpers
with `%s` placeholders that CakeCRM does. So the placeholder / `lastrowid` / `LIKE`→`ILIKE` /
threading-lock items above are inert for that direction and applying them is wasted motion. The
CakeCRM-side items still bite, because they are about *this* repo's conventions rather than the source
engine: the `new Date(iso + 'Z')` "Invalid Date" trap, `DOUBLE PRECISION` vs `REAL` for money, the
`exclude_unset` partial-update filter, enum coercion in the service layer, real pagination totals, and
`run_in_threadpool` for bulk loops. See `docs/SYNC.md` for the full cake_os port playbook (tenancy
stripping, module-topology collapse, the `apps.todo_gtd`/`apps.dimm` couplings, and the PII scrub).
