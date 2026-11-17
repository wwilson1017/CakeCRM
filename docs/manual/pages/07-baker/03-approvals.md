---
title: Approvals and modes
summary: Choose how much Baker may change on its own, and what still asks for your approval in every mode.
shots: []
---

## Three modes

At the bottom of the Baker drawer are three buttons: **Read**, **Ask** and **Auto**. Hover one to see its description. **Ask** is the default.

| Mode | What it means |
|---|---|
| Read | Read-only. Baker can look things up but not change data. It is not even offered the tools that change things, and would be refused if it tried one. |
| Ask | Everyday CRM edits run right away. Deletes, archives, merges, bulk changes, email drafts, notifications and memory always ask. |
| Auto | Changes run without asking, apart from the exceptions below. |

Looking things up never asks, in any mode.
Baker runs the lookups of one step side by side. If a lookup has not answered within two minutes, Baker stops waiting, is told it timed out, and carries on.

## What Ask runs straight away

In Ask mode, a change runs without a card only when it stays inside a CakeCRM record, notifies nobody, removes nothing from view, is not a bulk change, and sends nothing outside your install. In practice that means:

- creating and updating contacts, companies and deals;
- moving a deal's stage, and marking a deal won or lost;
- logging an activity;
- creating, updating and completing todos, and creating or updating projects;
- setting custom-field values on a contact, company or deal.

A few of those calls still ask, because of what they would do: archiving a contact or company, dropping a todo or project, or setting a bring-back date that hides a todo. Marking a todo done, completing a project, or filing either to someday runs without asking.

## What always asks in Ask mode

Deleting, archiving, merging, bulk moves, lead imports, change lists and bulk todo updates, recomputing lead scores, creating a Gmail draft, sending a notification, recording a memory fact, and any change to Baker's protected files. Anything new that has not been classified asks too.

## What asks even in Auto

- **Writing Baker's protected files**, soul.md and MEMORY.md. These always ask, in every mode, and only an admin can change them. See [Memory](04-memory.md).
- **Importing a lead list.** A lead import always shows one Approve card with the count and a sample, in every mode and from any conversation. See [Importing](../04-contacts-and-companies/06-importing.md).
- **A change list.** One edit across many deals or todos always shows one card listing every change, in every mode. See "Changing many records at once" below.
- **A conversation that has read outside content.** Once Baker has read an email, an uploaded file, a photo, or a todo that came in through the public quick-capture link while no token was set, every change it proposes asks for approval for the rest of that conversation, even after that message has scrolled out of view. Text someone else wrote can never quietly steer Baker into changing your data. The Telegram chat is one long conversation, so once it has read such outside content it asks before every change from then on. Within one step, Baker finishes its lookups before running changes, so a change proposed alongside an email read asks for approval too.

## Approving a change

When Baker wants to make a change that needs approval, its message carries a card naming the action and showing exactly what it will do, with **Approve** and **Deny**. Until you answer, nothing has happened.

In Telegram the same thing arrives as two buttons, Approve and Deny, and only the linked account can press them. A button answers that one action; replying **yes** approves every pending change. Any other message cancels the pending changes. Telegram chats always run in Ask mode.

## Changing many records at once

Ask Baker for one edit across several deals or todos ("move these 40 deals to Proposal and set their close date to the 30th") and you get **one card** instead of one per record.

- The card lists every change as *record · field: old value → new value*. CakeCRM reads the old values and the record names itself. A long list shows its first 20 rows; press **Show all** for the rest.
- A **Will not be changed** box lists every row CakeCRM refused, with the reason. Those rows are never written.
- **Approve** becomes available once the full list loads; a failed load offers **Retry**, and **Decline** stays available. Approve applies all the listed changes together. If any proposed field changed while the card waited, every proposed change for that record is skipped and listed as "changed since the card was shown".
- After Approve, the card shows how many changes were applied and an **Undo** button. One click puts every applied row back. If any applied field has changed since, every change for that record is left alone and listed. A batch can be undone once, and the card still shows its state when you reopen the conversation.

A change list can set, on deals: title, stage, value, expected close date, probability, currency, temperature and notes. On todos: title, status, due date, bring-back date, star, context, priority, description and tags. Tags replace the whole list. Owners are not set by a change list; change a deal's or todo's owner on the record itself. Up to 200 rows fit on one card.

Long values show their first 120 characters; hover for the full value. Links, repeat settings and projects remain outside change lists. Use status rather than a separate completed flag.

Some rows are always refused: an archived deal, a deal Baker has not looked up in this conversation, moving a deal into or out of Won or Lost (close and reopen deals one at a time, so the date and reason are captured), and completing or reopening a repeating todo. A malformed row is listed as **row N**; other valid rows can still be approved. If all rows are refused, there is nothing to approve and no card.

Each value row shows its own currency. If that currency changes before Approve or Undo, all changes for that deal are skipped or left alone. Amounts in different currencies are never added up, so the card shows no total.

If the records are busy and the transaction fails, nothing changes. Ask Baker to send the change list again.

In Telegram the approval message shows the count, up to five sample changes (excluding titles, notes and descriptions), and the refused rows. The full list and Undo are in the app.

## Baker looks a deal up before changing it

Baker must find the deal in this conversation and check its title before changing it.
The lookup must finish in an earlier step, so a guessed id cannot be sent alongside a
search and used by luck. This check also runs when you approve a change. After a CRM
reset, Baker must look the deal up again.

Changes to lost or archived deals can still go through. Baker receives a warning to
tell you which deal it changed and check that it is the one you meant.
