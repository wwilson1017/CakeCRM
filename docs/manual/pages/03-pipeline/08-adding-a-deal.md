---
title: Adding a deal
summary: Create a deal from the Pipeline with the New Deal form, linking it to a contact and a company as you go.
shots: [add-deal]
---

On the Pipeline, press **Add Deal** at the top right (**Add** on a phone). The **New Deal** form opens.

{{shot:add-deal}}

## The fields

| Field | What to enter |
|---|---|
| Title * | A short name for the deal, for example "TechStart monthly catering". The only required field |
| Contact | The person you are dealing with |
| Company | The organisation the deal is with |
| Owner | Who is responsible for it. Starts as you; pick another person or Unassigned |
| Stage | Where the deal starts. Defaults to lead |
| Value ($) | What the deal is worth |
| Probability (%) | Your estimate of the chance it closes, 0 to 100. Leave it blank for 0% |
| Forecasted close date | When you expect it to close. A guess, kept separate from the real Closed on date |
| Notes | Anything worth remembering about the deal |

If an admin has set up custom fields for deals, they appear under **Custom Fields** at the bottom. See [Custom fields](../04-contacts-and-companies/10-custom-fields.md).

Press **Create** to add the deal, or **Cancel** to leave without saving.

## Linking a contact and a company

The Contact and Company boxes search as you type. Pick a match to link it.

If the person or company is not in the CRM yet, type the name and choose **Create "name"…** at the bottom of the list. CakeCRM creates the record and links it in one step, so you do not have to leave the form. Searching first is worth it: it is how you avoid making a duplicate. See [Duplicates](../04-contacts-and-companies/07-duplicates.md).

A deal linked to both a contact and a company scores higher than one linked to neither, and Reports can only show it under a company it is linked to.

## After you create it

The deal appears on the board in the stage you picked. From there:

- Open it to [log an activity](03-deal-page.md) such as a call or a meeting.
- Add a follow-up todo so it has a next step. A deal with no open todo is flagged "no next step"; see [Deal health and scores](07-deal-health-and-scores.md).
- Move it through the stages as it progresses; see [Stages and closing a deal](04-stages-and-closing.md).

With an AI provider connected you can also ask Baker to create a deal for you. See [What Baker can do](../07-baker/02-what-baker-can-do.md).
