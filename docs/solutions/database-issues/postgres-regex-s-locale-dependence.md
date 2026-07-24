---
title: Postgres regex \s is locale/libc-dependent — use btrim for cross-language normalization keys
date: 2026-07-24
category: database-issues
module: backend/migrations, backend/crm/service.py
tags: [postgres, migration, unique-index, whitespace, normalization, locale, musl, alpine]
problem_type: bug
---

## Problem
When a company's name is normalized identically on two sides — a Postgres case-insensitive UNIQUE index / backfill dedup, and a Python `.strip()` on every service write — the two normalizations must agree byte-for-byte or you get phantom duplicate-name errors. Using regex `\s` on the Postgres side makes them disagree per deployment image.

## Symptoms
- A company create/rename returns `400 "A company with that name already exists"` with **no visible duplicate** in the UI.
- The bug appears only on some deployments (musl/alpine Postgres) and not others (glibc) — or only for names containing a non-breaking space / other Unicode whitespace.

## What Didn't Work
1. `TRIM(x)` in SQL vs Python `str.strip()` — `TRIM` removes only ASCII spaces; `.strip()` removes tabs/newlines/Unicode. Diverges on tab/newline.
2. `regexp_replace(x, '^\s+|\s+$', '', 'g')` in SQL, `str.strip(" \t\n\r\f\v")` in Python (an ASCII-only Python strip to "match `\s`"). This *looks* consistent, but Postgres `\s` is `[[:space:]]`, which is **libc/locale-dependent**: on `postgres:*-alpine` (musl) it trims NBSP (U+00A0), EM-space (U+2003), ideographic space (U+3000), etc.; on glibc it's roughly ASCII-only. So the index key still diverges from the ASCII-only Python value — on musl a ` Acme` row indexes as `acme` while the service stores/looks-up ` acme`, and two "Acme"s collide or duplicate depending on the image.

## Solution
Normalize with an **explicit fixed ASCII byte set** on both sides:

- SQL (migration index + every backfill step): `btrim(x, E' \t\n\r\f\x0b')` — trims exactly space, tab, LF, CR, FF, VT; a fixed byte set, libc-independent.
- Python (service create/update storage): `name.strip(" \t\n\r\f\v")` — the same six bytes.
- Emptiness/blank-name checks can still use Python's Unicode `.strip()` (so a lone NBSP is rejected as blank), while the STORED value uses the ASCII `.strip(_WS)` — keep the two purposes distinct.

```sql
CREATE UNIQUE INDEX uq_companies_name_ci
    ON companies (LOWER(btrim(name, E' \t\n\r\f\x0b')));
-- and the same btrim(...) in the backfill INSERT DISTINCT ON / UPDATE ... FROM join
```

## Why This Works
`btrim(string, characters)` trims a caller-supplied literal byte set — it never consults `[[:space:]]` / the locale — so the DB and Python agree on exactly which bytes are "outer whitespace" on every deployment image. Non-ASCII whitespace (NBSP etc.) is then treated as part of the name on both sides, consistently.

## Prevention
Any time a normalization key is computed in BOTH SQL and application code (unique indexes, dedup keys, join-by-normalized-value backfills), use an explicit character set on both sides, never regex `\s` / `[[:space:]]`. Verify empirically against the ACTUAL deployment Postgres image (alpine vs debian), not just your dev DB — `docker run postgres:16-alpine` and run the expression against tricky inputs (NBSP, ideographic space).
