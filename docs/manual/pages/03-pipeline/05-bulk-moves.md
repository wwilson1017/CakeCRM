---
title: Moving deals in bulk
summary: Select several deals and move them to another stage in one go.
shots: []
---

You can move many deals to a new stage at once. It is the only bulk action on the pipeline: there is no bulk archive, delete, close-with-reason, reassign or merge.

## Doing it

1. Tick the checkbox on each deal card, or on each row in the list view. In the list view, the box in the header selects every visible row.
2. A bar appears reading how many deals are selected.
3. Pick a stage from **Move to…** and press **Apply**.

There is no confirmation dialog. If you move deals to the wrong place, move them back. **Clear** drops the selection.

The selection only ever covers deals on screen, so what the bar counts and what Apply moves are always the same set. Checkboxes are not shown on a phone.

## Limits

- At most **200 deals** per move. Selecting more is refused and nothing moves.
- A deal already in the target stage is skipped and left untouched.
- Archived deals have no checkbox and cannot be moved.
- If a deal in the selection has gone or been archived in the meantime, CakeCRM tells you which one and still moves the rest.

## Moving into Won or Lost

**Move to…** does list Won and Lost, but a bulk move closes the deals with today as their Closed on date and no lost reason. If the date or reason matters, close the deals one at a time with **Mark Won** or **Mark Lost**. See [Stages and closing a deal](04-stages-and-closing.md).

## Asking Baker

With an AI provider connected, you can ask Baker to move a set of deals. It has the same 200-deal limit, but it only moves deals into open stages. It refuses to win or lose deals in bulk, and closes them one at a time instead so each date and reason is captured. See [What Baker can do](../07-baker/02-what-baker-can-do.md).
