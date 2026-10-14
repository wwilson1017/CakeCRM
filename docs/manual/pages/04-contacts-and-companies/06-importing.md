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
