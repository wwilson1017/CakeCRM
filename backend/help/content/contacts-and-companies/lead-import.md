---
title: Importing a lead list with Baker
description: Hand Baker a CSV or XLSX lead list and get companies, contacts and one deal per row behind a single approval, with an optional note on every deal.
aliases: lead import, import leads, lead list, trade show list, attendee list, import deals, bulk deals, badge scans
admin: false
---
## Bring a lead list into the pipeline

Use this for a trade-show list, webinar attendees or another list of new leads. The Contacts
page import makes contacts only, with linked companies. This path makes companies, contacts
and deals together.

## Preview, then approve

Use **Attach a file** in Baker to attach a CSV or XLSX, or paste CSV or tab-separated rows
with a header line. Ask Baker to import the leads. Baker calls `crm_preview_lead_import`,
reads the whole file on the server and shows counts and a sample. Nothing is created in the
CRM yet. After you agree, `crm_execute_lead_import` raises one **Approve** card with the
count and sample read from the server's saved preview. The card keeps them after a reload.

Baker always asks for your approval before a lead import writes anything, Auto mode included.
The preview contains untrusted list content, so later writes in that conversation also need
approval. CSV and XLSX files stay with your conversation; deleting it deletes the files.

## What each row becomes

- A company name is matched without regard to case, after trimming, or a company is created.
  New companies are left unassigned. A merged company spelling follows its survivor.
- A person is matched by email to a contact that is not archived, or a new contact is created
  and owned by you. Existing contacts are linked, never changed. Rows sharing an email share
  one contact. A company-only row makes no contact. A missing name falls back to email or phone.
- Every usable row makes one new deal in **Lead**, owned by you. Its value is 0 USD unless a
  deal-value column is mapped. Its title is “Company - New Lead” (or the person's name if
  there is no company) unless a deal-title column is mapped.
- Columns mapped to notes are kept on the deal as “Header: value”. A value that cannot be
  read as a nonnegative number is kept there too. Source, tags and job title are saved on
  new contacts; existing contacts keep their details.

## Columns

Common headers match the same spellings as `contacts-and-companies/import`: name, email,
phone, company, title, source, tags and notes. Baker can supply a mapping for first name,
last name, deal title, deal value and additional notes columns. It checks the unmapped
columns and previews again if something useful was missed. Explicitly skipped columns are
reported separately. Duplicate column names are refused; two columns cannot map to the
same field unless that field is notes. Rename one or ask Baker to skip one and preview again.
Custom-field columns are not supported.

## An optional note on every deal

Tell Baker the note you want, for example where and when you met these leads. It posts one
chatter note per new deal, written as you, up to 2000 characters. This counts as real contact
and sets the deal's last-contact date. Row notes in the deal's notes field are separate from
this chatter note.

## Limits and duplicates

One import takes up to **500 rows**. XLSX reads the first sheet only and warns about other
sheets. Uploads use Baker's existing limit of 5 files per message, 10 MB per file. A preview
lasts **60 minutes** and imports once. Companies, contacts, deals and chatter notes commit
together, all or nothing. An import failure leaves the preview available to retry while valid.

Deals are never checked for duplicates: importing the same list twice doubles the deals.
A person row with no email always makes a new contact. There is no import undo tool; imported
deals can be archived like any other deal.

An expired or already-imported preview is refused with a message and creates nothing.
Ask Baker to preview the list again before approving a new import.
