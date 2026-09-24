# Soft-archive is a sweep, not a column

**Tags:** `[postgres, soft-delete, archived_at, crm, invariants, review]`
**Origin:** CakeCRM issue #22 / PR #62 (`deals.archived_at`), 2026-08-16.

## The problem

Adding `archived_at` (or `deleted_at`, `is_active`) to a table looks like a one-column
migration. It isn't. The column's entire value is the **completeness of the sweep** over
every reader, and the misses are invisible: an unfiltered reader doesn't fail, it quietly
inflates a count or shows a row that should be gone.

On #22, four separate review stages each found a *different* missed reader class — after
the sweep already looked done:

| Missed reader | Symptom |
|---|---|
| `list_todos` | an archived deal's todo still listed, with its title |
| `get_dashboard_stats` (overdue counter) | count disagreed with the todo list |
| `get_dashboard_stats` (pending counter) | same |
| `get_contact_detail` | archived deal's todo on the contact page |

The last one mattered most: `crm_list_todos` and `crm_dashboard` are `writes:False`, so
they are in the unattended heartbeat's allowlist. The user archives a junk deal and the
autonomous assistant nags them about its follow-up todo — the "archive stops the noise"
promise, inverted.

## What actually fixed it

Not another grep. **A stated rule:**

> Work items follow the deal. History does not.

Once that sentence existed, every remaining site classified itself in seconds. Todos and
open work are swept; `activity_log` and win/loss history deliberately are not.

## The pattern

1. **Grep every reader before writing any predicate.** `FROM|JOIN|UPDATE <table>` across
   the whole backend, not just the module you're editing.
2. **State the inclusion rule in one sentence** and put it where the constant lives.
3. **Export a named predicate constant** (`_LIVE_PREDICATE`, `LIVE_TODO_PREDICATE`) from
   the owning service and have siblings **import** it. A sibling that re-types
   `archived_at IS NULL` as a literal is a future miss — CakeCRM's `analytics_service.py`
   did exactly this and a reviewer caught it.
4. **Name the deliberate exemptions** in the same place, with why. On #22: `get_deal(id)`
   (a direct id lookup must still resolve an archived row) and `is_crm_empty` (archived
   rows still count as "not empty").
5. **Provide a discovery path, or don't call it restorable.** #22 shipped archive with no
   REST endpoint and no list surface, so `get_deal(id)` with a remembered integer was the
   only way back — and the "archived" banner in the detail sheet was unreachable dead UI.
   Either add `include_archived` / an Archived filter, or defer it explicitly AND remove
   the UI that implies the path exists.

## The trap that follows the first fix

Landing the fix in ONE reader makes the sweep look complete. On #22 that partial fix left
CLAUDE.md asserting "an archived deal disappears from EVERY list, board, rollup, count and
aggregate" while three readers still leaked — so the fix round made the **documentation
more wrong than before**. An invariant asserted in a comment that the code does not hold is
worse than no comment.

## Related

- The lifecycle guard that refuses a stage change on an archived row is an API change.
  Adding a `raise` to a shared function is always one: check every caller at every layer
  that surfaces it (service, router, agent tool, background job) before landing it.
- Same-PR doc updates describe the plan, not the shipped code — re-read them against the
  final diff.
