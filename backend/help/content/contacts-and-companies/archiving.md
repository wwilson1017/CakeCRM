---
title: Archiving contacts and companies
description: Archiving a contact or company needs a reason, and the reason and your name are saved in its notes.
aliases: archive contact, archive company, archived, restore, unarchive, hide a contact, hide a company, inactive
admin: false
---
## What archiving is

For a contact or a company, archived is a **status**, like active or inactive. The record
keeps every note, activity and deal. It still appears under the Archived status filter and,
marked as archived, on reports. Deals work differently; see `pipeline/archived-deals`.

## How to archive

- **Yourself:** open the record, choose **Edit**, set **Status** to *Archived*, and fill in
  **Why archive?**. Save is refused until there is a reason.
- **With Baker:** ask it to archive the record. It uses `crm_update_contact` or
  `crm_update_company` and asks you why if you have not said.

A record cannot be created already archived. Create it, then archive it with a reason.

## What gets recorded

Archiving writes an "Archived — <reason>" note in the record's notes, credited to you.
Setting the status back to active or inactive writes a "Restored from archive." note.
Neither note counts as talking to the person, so a contact's last-contact date and its
staleness do not move.
