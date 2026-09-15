---
title: Pipeline stages
description: The six deal stages, how a deal moves between them, and why Won and Lost are hidden by default.
aliases: pipeline, stages, kanban, board, deals, won, lost, drag, close a deal, filters, hidden columns
admin: false
---
## The six stages

In order: **lead, qualified, proposal, negotiation, won, lost**. Lead through negotiation are
the open stages; won and lost are the closed ones.

There is no preset probability per stage. A new deal starts at 0% unless you set one. Closing
a deal is the exception: winning sets it to 100, losing sets it to 0.

## Won and Lost are hidden by default

The board opens showing the four open stages. A header control always reads how many stages
are hidden with a **Show all** beside it, so nothing is ever secretly missing.

Two separate things control this, and they answer different questions:

- **On the board**, clicking a stage chip hides or shows that column. That choice is
  per browser tab and temporary.
- **In Settings, Personal, Pipeline board**, a checkbox says whether a *fresh* board should
  start with Won and Lost showing. That is the standing default, saved in this browser.

The tab's own choice wins while it exists, so revealing a column for a minute does not
rewrite your default. Moving a deal into a hidden column automatically reveals that column,
so a closed deal never vanishes without explanation.

## Moving a deal

- **Drag** a card between columns on the board.
- **Edit the stage** in the deal's detail panel.
- **Select several cards and move them together.** See `pipeline/bulk-moves`.

## Closing a deal properly

Use the explicit close actions rather than dragging into Won or Lost:

- `crm_mark_deal_won` sets the stage to won and the probability to 100.
- `crm_mark_deal_lost` sets the stage to lost and the probability to 0, records the reason,
  and writes the reason into the deal's notes thread.

In the interface, the Mark Lost action opens a dialog for the reason. **Only that action
captures a reason.** Dragging a card into Lost, moving it in bulk, or changing the stage
field on the form all close the deal with no reason and no note. The lost reason is the most
useful field in the pipeline when you review a quarter, so it is worth the extra click. A
reason cannot be added after the fact.

Baker's plain stage tool, `crm_update_deal_stage`, refuses to move a deal into won or lost
for exactly this reason and points at the two close tools instead. Leaving the lost stage
clears the reason, so a reopened deal does not carry a stale one.

## What a card shows

Title, value, the linked contact, probability if it is set, and the expected close date if
there is one — plus a lead score, an estimated touch count, and a temperature icon you can
click through. On a **Won** card those three are replaced by when that account was last
contacted, because after the sale the question changes from "is this closing" to "has this
gone quiet".

## Deal fields

Value and a currency code, an expected close date, a probability, notes, and links to a
contact and a company. Currency is free text and everything else in the app sums as though
one currency is in use — the company report is the one place that declines to add different
currencies together.

## Filtering the board

Six facets: **stage**, **owner** (including an Unassigned bucket), **value** range, **close
date** (overdue, next 7 days, this month, no date), **deal activity** (active within 7 or 30
days, silent 30+ days, nothing logged) and **archived**. Filters are remembered per browser
tab. Dragging still works while a filter is on, because a drop only assigns a stage and never
a position.
