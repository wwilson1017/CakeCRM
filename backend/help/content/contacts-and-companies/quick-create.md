---
title: Creating records quickly
description: What is actually required, and how to create a linked contact or company without leaving the form.
aliases: create, new contact, new company, new deal, quick add, required fields, link company
admin: false
---
## What is required

Very little:

- **Contact** — a name. Email, phone, title, source, tags, notes and the company link are all
  optional.
- **Company** — a name. Domain, industry, phone, address, notes and source are optional.
- **Deal** — a title. Stage defaults to lead, value to zero, probability to zero, currency to
  a default, and the contact and company links may both be empty.

## Creating a link without leaving the form

The Contact and Company fields on a deal form, and the Company field on a contact form, are
search-as-you-type pickers rather than dropdowns of the first few hundred records. Type a
name, and if nothing matches, the list offers a **Create "…"** row. Choosing it creates the
record straight away and links it.

Two consequences worth knowing:

- **Typing a name and pressing Save without choosing Create discards it.** The Create row is
  the commit. This catches people out, so say it plainly.
- Creating from a picker writes a real record immediately. Abandoning the form afterwards
  leaves that record behind — at most one, and typing the same name again reuses it rather
  than making a second.

## Typing a company that does not exist

It is created for you. Company names are resolved case-insensitively after trimming, through
one shared routine used by import, the pickers and Baker alike, so two spellings of a company
cannot become two companies.

The New Company form is different on purpose: it creates unconditionally and tells you
plainly if that name already exists, because there you meant to create a company rather than
to link one.

## Editing an existing contact's company

If a contact carries an old free-text company name that was never linked to a real company
record, the form shows that text with a "not linked" hint and a Remove action.

**Leaving the company field alone when you edit something else does not touch it.** That is
deliberate: it is the only thing standing between old unmatched text and someone erasing it
by opening the form to fix a phone number.
