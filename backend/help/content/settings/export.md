---
title: Export your data
description: Download everything in the CRM as CSV files in one ZIP, from Settings. Admin only.
aliases: export, csv, download my data, backup, spreadsheet, excel, get my data out, zip
admin: true
---
## What it is

Settings → Workspace → **Export data** downloads one ZIP containing a CSV file per table:
companies, contacts, deals and their stage history, activity, todos and projects, notes and
mentions, custom fields, stage checklists, saved views, the assistant's memory and context
files, plus a users file so the owner and author columns can be looked up by name. Every
file has a header row and is sorted by id. Nothing is filtered: archived deals are included.

Passwords, two-factor secrets, API keys and connection tokens are never exported, nor are
note attachments (files, not rows) or assistant conversations (each person's own). A cell
that starts with =, +, - or @ is written with an apostrophe in front, so a spreadsheet shows
it as text instead of running it as a formula; phone numbers written with a leading + pick
up that mark too.

Every file is read from one snapshot, so the files agree with each other even if someone is
working in the CRM while the export runs.

## When to use it

- A copy of everything before a big change, such as clearing data or a large import.
- Working the numbers in a spreadsheet: open the deals file directly.
- Moving to another system. The id columns are the links between files, so the contact id
  column in the deals file points at the id column in the contacts file, and so on.

It is a snapshot, not a sync. Download again for fresh numbers.

## Admin only

The export is the whole database in one file, so only an admin sees the card. Members
cannot request it.
