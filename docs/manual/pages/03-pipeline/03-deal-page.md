---
title: The deal page
summary: Everything about one deal in a single panel: its details, how to edit or close it, the activity log and the notes thread.
shots: [deal-detail]
---

Click a deal anywhere in CakeCRM (a board card, a list row, a Top deals row on the dashboard) and it opens in a panel over the page.

{{shot:deal-detail}}

## Along the top

| Button | What it does |
|---|---|
| Copy link | Copies a link that opens this deal on the Pipeline, to paste into a message or a note |
| Edit | Turns the panel into a form so you can change the deal's fields |
| Mark Won | Closes the deal as won, asking which day it closed |
| Mark Lost | Closes the deal as lost, asking for a reason |

**Mark Won** and **Mark Lost** only appear while the deal is in an open stage. See [Stages and closing a deal](04-stages-and-closing.md).

## The details

Under the title and value you see the stage, then the Deal ID, Contact, Company, Owner, Temperature, Probability, Lead Score, Forecasted close date and Closed on. A lost deal also shows its LOST REASON.

Click the temperature icon to cycle the deal through hot, warm, cold and not set. Once an AI provider is connected, an estimated touch count also appears here, and you can open the line-by-line reasons behind it. See [Deal health and scores](07-deal-health-and-scores.md).

## Editing

Press **Edit** to change the title, contact, company, owner, stage, value, probability, forecasted close date or notes. A Won deal also shows its Closed on date here. Press **Save** to keep your changes or **Cancel** to drop them.

If you try to close the panel with unsaved changes, CakeCRM asks before throwing them away.

## Log Activity

Pick **call**, **email**, **meeting** or **note**, type what happened in the box, and press **Log**. **Clear** puts the choice back if you picked one by mistake. An activity is a dated record of an interaction; it is history and is not edited afterwards.

## Custom fields

If an admin has set up custom fields for deals, they appear under the activity box. See [Custom fields](../04-contacts-and-companies/10-custom-fields.md).

## Activity History

Every call, email, meeting and note logged against the deal.

## Chatter

The notes thread at the bottom is for standing commentary about the deal. You can reply in threads, attach files and mention a teammate with `@`. See [Notes, mentions and attachments](../04-contacts-and-companies/05-notes-mentions-attachments.md).

## Archived deals

An archived deal opens with a banner saying when it was archived, by whom and why, and a **Restore** button. See [Archived deals](06-archived-deals.md).
