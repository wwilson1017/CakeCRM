---
title: Archived deals
description: Archiving hides a junk deal without deleting it, and Restore brings it back.
aliases: archive, archived, restore, unarchive, hide a deal, deleted deal, junk, missing deal
admin: false
---
## What archiving is

A soft hide. The deal keeps every note, activity and todo; it simply stops appearing. Nothing
is deleted, ever.

## Archive is not the same as Lost

- **Lost** is a real outcome: the deal happened and you did not win it. It belongs in your
  win rate and your loss reasons.
- **Archived** is "stop showing me this": a duplicate, a test record, something created by
  mistake.

Never archive a deal that genuinely closed. Winning or losing is `crm_mark_deal_won` or
`crm_mark_deal_lost`.

## How to archive

There is **no archive button in the interface**. Ask Baker, which uses `crm_archive_deal`.
That is deliberate — archiving is rare and destructive-looking, and the recovery path below
matters more than the shortcut.

Archiving always needs a **reason**. If you do not give one, Baker asks. The reason and your
name are saved on the deal and written into its notes as "Archived — <reason>", so whoever
finds it later knows who put it away and why. Merging two deals archives the source with the
reason "Merged into deal #N".

## Where an archived deal goes

Out of sight nearly everywhere: the board, the dashboard, analytics, the deal lists, and the
contact and company rollups all skip archived deals. Its open todos drop out of todo lists
too, because archiving is the "stop nagging me" gesture.

Its **activity history is never hidden**. That record is what you read to decide whether to
bring it back.

## How to bring one back

1. On the Pipeline, set the **Archived** facet to *Include archived* or *Archived only*.
   There is also a link in the page header that flips between showing live and archived
   deals, which still works when the board is empty.
2. Archived cards render dimmed and inert — no checkbox, no dragging — but they still open.
3. Open the deal. A banner across the top says when it was archived, by whom and why, and
   offers **Restore**. Deals archived before this was recorded show the date only.

Restoring puts it straight back on the board and leaves a "Restored from archive." note with
your name. Neither note counts as contact with the customer, so archiving and restoring a
deal does not reset how long it has gone untouched.

## Rules while a deal is archived

- Its stage cannot be changed. A deal that was both won and archived would book revenue no
  report could see.
- It cannot be selected for a bulk move.
- Archiving twice keeps the original date, person and reason.

## Restoring a merged deal is not an undo

When two deals are merged the source is archived. Restoring it makes it visible again, but
the merge already moved its activity and todos and copied its notes onto the survivor. Those
do not come back.
