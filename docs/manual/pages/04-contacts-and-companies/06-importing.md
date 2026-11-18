---
title: Importing contacts
summary: Bring in contacts from a CSV or vCard file, with no AI needed, or from other formats once AI is set up.
shots: [import-modal]
---

On the Contacts page, press **Import Contacts**.

{{shot:import-modal}}

## Steps

1. Click **Click to select a file** and choose your file.
2. Press **Parse Contacts**. The app reads the file and shows the contacts it found, with name, email, phone and company.
3. Untick any you do not want.
4. Press **Import Selected**.

## Which files work

- **CSV** (`.csv`) and **vCard** (`.vcf`) always work. No AI is involved and the result is the same every time.
- **Other formats** (JSON, plain text, tab-separated, or a CSV too messy to read) need an AI provider. Until an admin connects one in **AI Setup**, the file picker only offers CSV and vCard. See [AI setup](../07-baker/06-ai-setup.md).

## Limits

- 1 MB per file.
- 5,000 contacts per import.
- For other formats, only the first 200 KB of the file is read. A longer file comes with a warning that some contacts may be missing.
- A file whose contents are not text is refused with a message.

## Columns in a CSV

Column headings are matched loosely and without regard to capitals. "Full Name", "Contact Name" and "Name" all count as the name. The same goes for email, phone, company, title, source, tags and notes.

A CSV must have a name column. A row with a blank name is skipped and counted.

## What happens to each contact

- **You own every contact you import.**
- A company name is matched to an existing company, or a new company is created. **New companies made this way are left unassigned**, for someone to pick up.
- A row that fails is listed as an error, and the rest still import.

## Duplicates are not checked

Import does not look for people already in the CRM. Every row becomes a new contact, even if someone with that email exists. Tidy your file first. See [Duplicates](07-duplicates.md).


## Importing leads with Baker

For a lead list that should become companies, contacts **and deals**, use Baker instead of
the Contacts page import. An AI provider must be set up for Baker.

1. Use **Attach a file** in Baker to attach a CSV or XLSX, and ask to import the leads. You can also paste CSV or tab-separated rows with a header line.
2. Read the preview: how many deals, new and existing contacts and companies, plus a sample. Ask Baker to map any useful columns it missed and preview again.
3. Press **Approve** on the one card. Its count and sample come from the saved preview and survive a page reload. Baker always asks for your approval before a lead import writes anything, Auto mode included.

You get:

- Companies matched by name or created, with new companies left unassigned.
- Contacts matched by email to a contact that is not archived, or created and owned by you. Existing contacts are not changed. A company-only row makes no contact.
- One new **Lead** deal per usable row, owned by you, titled “Company - New Lead” (or the person's name) unless a title column is mapped. Value starts at 0 USD unless a value column is mapped. Columns mapped to notes go in the deal's notes field.
- If you supply a note, one chatter note on every new deal, written as you. It can be up to 2000 characters and counts as contact, giving the deal a last-contact date.

The limit is **500 rows per import**; an XLSX uses its first sheet. A preview lasts **60 minutes**
and imports once. Companies, contacts, deals and notes are saved together, all or nothing.
An expired or already-imported preview says so; ask Baker for a new preview.

**Deals are never checked for duplicates.** Importing the same list twice doubles the deals.
A person row without an email always creates a new contact. Custom-field columns and import
undo are not supported; you can archive imported deals normally.

CSV and XLSX bytes stay with the conversation and are deleted with it. See
[What Baker can do](../07-baker/02-what-baker-can-do.md).
