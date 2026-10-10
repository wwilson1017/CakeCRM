---
title: Moving deals in bulk
description: Select several deals and move them to another stage in one go, with per-deal error reporting.
aliases: bulk, multi select, select all, move deals, mass update, batch
admin: false
---
## What exists

Exactly one bulk operation: **moving deals to another stage**. There is no bulk archive, no
bulk delete, no bulk close, no bulk reassign and no bulk merge.

## Doing it

1. Tick the checkbox on each card you want. Archived cards have no checkbox and cannot be
   included.
2. A bar appears reading how many are selected, with a **Move to…** menu and **Apply**.
3. Pick a stage and apply. There is **no confirmation dialog** — the action is reversible by
   moving them back.

The selection only ever covers deals currently on screen, so what the bar counts and what
Apply moves are the same set.

## Limits and what happens on failure

- At most **200 deals** per move. Asking for more is refused outright and nothing is written.
- A deal already in the target stage is skipped silently: no write, and its last-updated
  timestamp does not move, so a no-op never resets a staleness clock.
- Everything happens in one database transaction, so "the deals moved but the history did
  not" cannot happen.
- Per-deal problems — a deal that has gone, or one that is archived — are reported
  individually while the rest of the batch still commits. One bad deal does not sink a
  selection of fifty.
- A whole-request refusal writes nothing at all, and the board reverts to what the server
  holds.

## Baker's version

`crm_bulk_move_deals` takes a list of deal identifiers and a stage. Same 200 cap, same
per-deal error reporting. It accepts **open stages only** — winning or losing in bulk would
skip the reason capture, so it refuses and points at `crm_mark_deal_won` and
`crm_mark_deal_lost`.

Note the difference from the interface: the Move to… menu in the app does list Won and Lost,
and moving there in bulk closes those deals with no reason recorded. Deals moved into Won in
bulk are dated today; correct a date afterwards on the deal itself. If the reason matters,
close them one at a time.
