---
title: Changing many records in one approval
description: A change list puts one edit across many deals or todos on a single card, old value to new, with Undo for the whole batch.
aliases: change list, bulk edit, bulk update, mass update, undo, batch undo, many deals, old new
admin: false
---
## What it is

When you ask Baker for one edit across several records ("move these 40 deals to Proposal and
set their close date to the 30th"), it sends one change list instead of one change per
record. You get **one card**:

- the number of changes, and each one as *record · field: old value → new value*. The old
  values and the record names are read by the server, not written by Baker. A long list shows
  its first 20 rows with **Show all**;
- a **Will not be changed** box listing every row the server refused, with the reason.
  Refused rows are never written;
- **Approve** and **Decline**. Approve applies every listed change in one database
  transaction. Approve stays disabled until the full list loads; if loading fails, press
  **Retry**. Decline remains available.

The card appears in every mode, Auto included. In Telegram the prompt shows the count, up to five sample changes, and the
refused rows; the full list and Undo are in the app.

## What a change list can set

- **Deals:** title, stage (open stages only), value, expected close date, probability,
  currency, temperature, notes and owner.
- **Todos:** title, status, due date, bring-back date, star, context, priority, description, tags and owner.

Up to 200 rows on one card. Tags replace the whole list. An owner is an existing user id,
or empty to unassign. Long values show their first 120 characters; hover to read the full
value. Titles, notes and descriptions are omitted from the Telegram sample.

Links to contacts, companies or deals stay outside change lists because re-linking follows
company merges before locking records. Use status rather than a separate completed flag.
Repeat and auto-star settings stay outside because completing can create a next occurrence
that Undo cannot remove. Projects stay outside because assigning by name can create a project.
Contacts, companies and custom fields are not available here either.

## Rows that are refused

- a deal Baker has not looked up earlier in this conversation (`assistant/confirmations`);
- an archived deal: restore it first;
- moving a deal into Won or Lost, or moving a won or lost deal: deals are closed and reopened
  one at a time, so the date and the reason are captured;
- completing or reopening a repeating todo, because that creates its next occurrence;
- a field outside the lists above, a value the field does not accept, the same record and
  field listed twice, a value the record already has, or an owner who does not exist;
- a malformed row, listed as **row N** with its reason. Other valid rows can still be approved.

If every row is refused there is no card, and Baker is told why.

## Records edited while the card waits

Approve checks each record again. If any proposed field no longer holds the old value the
card showed, all proposed changes for that record are skipped and reported as "changed since
the card was shown". Other records still apply. A value change also requires the currency
to match what the card showed.

If the records are busy and the transaction fails, nothing changes. Ask Baker to send the
change list again.

## Undo

After Approve the card shows how many changes were applied and an **Undo** button. One click
puts every applied row back to its old value, in one transaction. If any applied field
or a value row’s currency changed since, all changes for that record are left alone and listed. A batch can be undone once; after that the card reads
Undone, also when the conversation is reopened. Baker cannot undo a batch itself; ask it and
it will point you to the button. Only the person whose conversation it is can undo it.

Undoing a stage change adds a second entry to the deal's stage history; it does not erase
the first. Undoing a completed todo reopens it.

## Money

Each value row shows its deal's own currency. Values in different currencies are never added
together, and the card shows no total.

## The tool

`crm_apply_change_list`. The single-record tools and `crm_bulk_move_deals`
(`pipeline/bulk-moves`) work as before.
