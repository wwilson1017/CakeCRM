---
title: Importing contacts
description: CSV and vCard import need no AI key. Other formats need a provider and degrade with a warning.
aliases: import, csv, vcard, vcf, upload contacts, bulk add, spreadsheet, migrate
admin: false
---
## Two paths

- **Deterministic** — a `.csv` file, or a vCard `.vcf` file. No AI, no key, same result every
  time.
- **Assisted** — anything else (JSON, plain text, tab-separated, or a CSV too malformed to
  parse). This one reads the file with an AI model.

The file picker only offers CSV and vCard until an AI provider is connected; once one is, it
widens to the other formats.

## Limits

- **1 MB** per file.
- **5000 contacts** per import.
- For the assisted path, the first 200 KB of the file is what the model sees. A longer file
  is truncated with a warning saying some contacts may be missing.

## Columns

Headers are matched case-insensitively against a list of common spellings, so a column
headed Full Name, Contact Name or just Name all resolve to the contact's name, and the same
applies to email, phone, company, title, source, tags and notes.

**A name column is required.** Without one the import is refused with a message. A row whose
name is blank is skipped and counted, not treated as an error.

## What happens to each row

- A contact is created, and **the person running the import owns it**.
- A company name is resolved to a real company record, creating one if it does not exist yet,
  matched case-insensitively after trimming. That resolution is batched, so importing
  thousands of rows does not mean thousands of extra queries.
- **A company created that way is left unassigned**, deliberately, unlike the contacts. A
  company that appeared as a side effect of linking a contact is not one anybody chose to
  take on, so a bulk import puts the contacts in your own list and leaves the companies for
  someone to claim. See `contacts-and-companies/ownership`.
- A row that fails is reported in an error list while the rest of the import continues.

You get back three numbers: imported, skipped, and the errors.

## Duplicates are not checked

Import does **no** duplicate detection. Every valid row becomes a new contact, even if
somebody with that exact email is already in the CRM. This is worth saying out loud, because
it is the opposite of what people expect.

The remedy is to run the duplicate scan afterwards — see `contacts-and-companies/dedupe` —
and bear in mind there is no contact merge, so cleaning up means deleting by hand. Importing
carefully costs less than fixing it later.

## When there is no AI provider

The assisted path does not fail; it returns a warning saying the format needs an AI provider
and pointing at Settings. CSV and vCard keep working exactly as before. If the model call
times out or errors, that also comes back as a warning rather than a crash.

## Binary files

A file whose contents are binary is refused up front with a message saying so, rather than
being fed to a parser.
