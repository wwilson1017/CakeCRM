---
title: Making two pooled reads describe one instant, without abandoning the pg helpers
date: 2026-09-04
category: database-issues
module: backend/crm/service.py, backend/core/postgres.py
tags: [postgres, repeatable-read, connection-pool, row_to_dict, hermetic-tests, consistency]
problem_type: pattern
---

## Context

`core.postgres.pg_fetchall` takes a **fresh pooled connection per call**. That is right for
one read and wrong for a pair the user sees side by side. On CakeCRM #146 the Weekly Touches
drill-down rendered a count and the rows behind it from two separate calls, so a write landing
between them produced "6 of 5 open deals touched" — a contradiction on one screen, from two
individually correct queries.

## Guidance

**Thread an optional cursor through the query builders and let ONE caller own the
transaction.** Each builder keeps its `pg_fetchall` fallback, so every existing caller is
untouched:

```python
def _touched_deal_rows(start, end, cur=None):
    sql, params = _touched_deal_sql(start, end)
    if cur is None:
        return pg_fetchall(sql, params)
    cur.execute(sql, params)
    return [row_to_dict(cur, r) for r in cur.fetchall()]
```

The composing caller opens one connection and sets the isolation level as the transaction's
**first** statement:

```python
with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        total = _touched_deal_count(start, end, cur=cur)
        rows  = _touched_deal_rows(start, end, cur=cur)
```

The transaction-scoped form resets at commit and is safe on a pooled connection, unlike
`SET SESSION`, which leaks across checkouts. `row_to_dict` is public for exactly this use.

## The trap that makes it untestable

**Convert rows to dicts BEFORE the next `execute()`.** `row_to_dict` derives column names from
`cursor.description`, which every `execute()` rebinds to its own statement's columns, so a late
conversion zips the first query's row against the second query's columns into a silently wrong
dict.

That bug class is invisible to a hermetic fixture whose fake cursor returns dicts directly —
the fake satisfies the real helper no matter when it is called. **The fake cursor must return
TUPLES and carry a real `description` that changes per `execute`.** Without that, the test
proves only that the code runs.

## When to Apply

When two reads a user sees together must agree, and the parent cannot simply derive one from
the other. Do not reach for it for a single read, and prefer computing a summary as its own
aggregate over the full table rather than reducing a capped list — a cap silently changes the
headline number, and a filter that widens the list makes it worse.
