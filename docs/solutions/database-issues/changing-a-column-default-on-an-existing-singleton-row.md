---
title: Changing a column DEFAULT is a no-op when the table's singleton row already exists
date: 2026-08-26
category: database-issues
module: backend/migrations, backend/crm/service.py
tags: [postgres, migrations, singleton, defaults, crm_meta, product-defaults]
problem_type: logic-error
---

## Context

CakeCRM issue #102 asked to flip the default task mode from `normal` to `gtd`. The issue
itself proposed two options and left the choice to plan time:

> Recommendation: a one-shot migration flips rows still at `'normal'` to `'gtd'` … If we'd
> rather never override a possible explicit choice, skip the backfill and only change the
> default for fresh installs — decide at plan time.

The second option reads as the conservative, obviously-available choice. It is not
implementable, and the reason generalizes to every settings singleton in this codebase
(`crm_meta`, `ai_settings`, `gmail_connection`, `auth_credential`, `telegram_settings`).

## Guidance

**When a column was added by `ALTER TABLE … ADD COLUMN … NOT NULL DEFAULT <x>` to a table
whose singleton row already existed, the column default is consumed exactly once — at
`ADD COLUMN` time — and never again. Changing it later changes nothing on any install,
fresh installs included. The row `UPDATE` is the entire mechanism.**

The ordering that causes this in CakeCRM:

```sql
-- 20260723221920_crm_core.sql  (early)
CREATE TABLE IF NOT EXISTS crm_meta (id INTEGER PRIMARY KEY CHECK (id = 1), …);
INSERT INTO crm_meta (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

-- 20260821100625_todo_gtd_task_mode.sql  (later)
ALTER TABLE crm_meta
    ADD COLUMN IF NOT EXISTS task_mode TEXT NOT NULL DEFAULT 'normal' …;
```

A **fresh install replays this same sequence**: the row is inserted first, then `ADD COLUMN`
backfills `'normal'` into it. So a fresh install is in exactly the same state as an
upgrading one, and a later forward migration containing only `ALTER COLUMN … SET DEFAULT
'gtd'` would leave every install on `'normal'`.

Nothing re-inserts the singleton either — worth verifying explicitly, because it is the
load-bearing half of the claim:

- every other writer is an `UPDATE … WHERE id = 1`;
- both `_truncate_all` variants and `is_crm_empty` deliberately exclude `crm_meta`;
- the CRM reset (`clear_all` / `demo_clear`) UPDATEs the row, never TRUNCATEs it.

So the correct migration ships both statements, with the UPDATE as the operative one:

```sql
ALTER TABLE crm_meta ALTER COLUMN task_mode SET DEFAULT 'gtd';
UPDATE crm_meta SET task_mode = 'gtd', updated_at = now() WHERE task_mode = 'normal';
```

Keep the `SET DEFAULT` anyway — not because it does anything today, but so the schema does
not contradict the product default for whoever next adds an inserter. Say so in a comment,
or the next reader will "simplify" the backfill away as redundant.

The only way to get "fresh installs only" would be to edit the already-applied
`20260821100625` in place. Existing installs skip it (the runner records applied filenames
in `_migrations_applied`), so they keep `'normal'` while fresh installs get `'gtd'` — but
that makes the schema history lie about what ran, and this repo does not do it.

## Why This Matters

The failure mode is silent and survives review. A migration containing only `SET DEFAULT`
is valid SQL, applies cleanly, and looks exactly like a default change. It ships, the
deploy is green, and every install still behaves the old way. Nobody gets an error.

It also inverts how the decision reads. "Skip the backfill" sounds like the cautious option
that respects a user's explicit choice; in fact it is the option that does nothing at all,
while the "aggressive" backfill is the only one that implements the request. Knowing this
turns an open product question into a forced move — worth establishing at plan time, since
it is the difference between one migration and a follow-up bug report.

## Testing it

Neither half is provable from a freshly-migrated database alone: the row is `'normal'` when
the migration runs, so the skip case never occurs, and the flip case is only visible as an
end state. Re-execute the **real migration file** and cover both directions:

```python
sql = (Path(__file__).resolve().parents[1]
       / "migrations" / "20260825222000_default_task_mode_gtd.sql").read_text()

# skip case: an already-'gtd' row must not even have updated_at bumped
before = pg_fetchone("SELECT task_mode, updated_at FROM crm_meta WHERE id = 1")
pg_execute(sql)
after = pg_fetchone("SELECT task_mode, updated_at FROM crm_meta WHERE id = 1")
assert after["updated_at"] == before["updated_at"]

# flip case: an upgrading install still on the DDL default gets moved
pg_execute("UPDATE crm_meta SET task_mode = 'normal' WHERE id = 1")
pg_execute(sql)
assert pg_fetchone("SELECT task_mode FROM crm_meta WHERE id = 1")["task_mode"] == "gtd"
```

**Read the file; do not retype the statement.** The first version of this test executed a
hand-copied `UPDATE … WHERE task_mode = 'normal'` literal. Mutating that literal turned it
red, so it looked verified — but deleting the `WHERE` clause from the actual migration left
the whole suite green, which is exactly the regression the test claimed to catch. A guard
that replays a copy tests the language, not the repo.

Also assert the DDL half separately, since the row value proves only the UPDATE:

```python
assert pg_fetchone(
    "SELECT column_default FROM information_schema.columns "
    "WHERE table_name = 'crm_meta' AND column_name = 'task_mode'"
)["column_default"].startswith("'gtd'")
```

## When to Apply

Any time you change the default of a column on one of this repo's settings singletons, or
add a column to one and expect the default to govern later rows. Also whenever a migration's
only statement is `ALTER COLUMN … SET DEFAULT` — ask what row it is supposed to affect and
whether anything will ever insert one.

The inverse case is fine and needs no backfill: a column on a table with real ongoing
inserts (`contacts`, `deals`, `tasks`) genuinely uses its default for every new row.
