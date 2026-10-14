---
title: Stages and closing a deal
summary: The six stages, the checklist behind each one, and how to close a deal so the date and reason are kept.
shots: []
---

## The six stages

In order: **lead, qualified, proposal, negotiation, won, lost**. Lead through negotiation are the open stages; won and lost are the closed ones.

There is no preset probability per stage. A new deal starts at 0% unless you set one. Winning sets it to 100% and losing sets it to 0%.

## Stage checklists

Every stage has a short summary and a checklist of what it takes for a deal to belong there.

- **Hover** over a stage name on the board for a quick look.
- **Click** it to pin the checklist in a small panel at the bottom-right of the screen (a sheet along the bottom on a phone). The board keeps working while it is open. Click another stage name to switch, or the same one, the **×**, the panel itself or Escape to close it.

CakeCRM ships a **Standard** checklist for every stage. An admin can write their own: pin the stage, press **Edit**, change the summary, add, remove or reorder lines with **Add item**, then **Save**. The panel then shows **Custom**, and **Reset to standard** puts it back. Members see the checklists but not the Edit button. Custom checklists go back to standard when the CRM is reset or its sample data is cleared.

## Won and Lost on the board

The board hides Won and Lost by default. Two separate controls decide this:

- **The stage checkboxes on the board** hide or show a column for this browser tab only.
- **Settings, Personal, Pipeline board** has a **Show Won and Lost stages** checkbox. That is the default every new tab starts from, saved in this browser.

Moving a deal into a hidden column shows that column, so a closed deal never vanishes without explanation.

## Closing a deal properly

Open the deal and use **Mark Won** or **Mark Lost**. These are the only ways that keep the full record.

**Mark Won** asks **When did this close?** The Closed on day defaults to today. An earlier day is fine; a future day is refused. Press **Confirm**.

**Mark Lost** opens **Mark as Lost** with a **Reason (optional)** box, for example "Budget constraints, chose a competitor". Press **Mark Lost**. The reason is saved on the deal and written into its notes thread.

Every other way into Won or Lost (dragging a card, a [bulk move](05-bulk-moves.md), or changing the stage in the edit form) records today as the Closed on date and records no lost reason. A lost reason cannot be added later, and it is the most useful thing to have when you look back over a quarter, so it is worth the extra click.

## The Closed on date

When a deal is won, the day it really closed is kept as its **Closed on** date, separate from the forecasted close date, which was only a guess. It shows on the deal, in the list view's Closed on column and in the Closed on filter.

- While a deal is Won you can correct the date in the edit form, but you cannot blank it.
- Every change leaves a "Closed on: old → new" note in the deal's notes thread.
- Moving the deal out of Won clears the date; winning it again asks again.
- Moving a deal out of Lost clears its lost reason.
