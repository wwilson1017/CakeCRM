---
title: Custom fields
description: Define your own fields on contacts, companies and deals, then fill them in on the record.
aliases: custom fields, extra fields, field definitions, dropdown, select field, schema
admin: true
---
## What they are

Fields you define yourself, on top of the built-in ones. They attach to three kinds of
record: **contacts**, **companies** and **deals**.

## Defining them

**Admin only**, in Settings, Workspace, Custom fields. Pick the record type, give the field a
name and a type:

- **text** — free text.
- **number** — must parse as a real number.
- **boolean** — a checkbox.
- **date** — must be a real calendar date written as year-month-day.
- **select** — one of a list of options you supply.

## Filling them in

Anyone signed in can set values. They appear on the record's create and edit forms and in a
Custom fields section on its detail page. Only the *definitions* are admin-gated; the
*values* are ordinary record data.

Clearing a value to empty always works, even on a number or date field — emptying a field is
not the same as typing something invalid into it.

## Required is advisory

Marking a field required shows it as required in the interface, but nothing on the server
enforces it. A record can always be saved without it. Treat it as a prompt to the person
filling the form, not a constraint.

## What Baker can do

Six tools: `crm_get_contact_fields`, `crm_set_contact_fields`, `crm_get_company_fields`,
`crm_set_company_fields`, `crm_get_deal_fields` and `crm_set_deal_fields`. The read tools
return every defined field, including ones with no value yet — so Baker can tell the
difference between "not recorded" and "no such field", and should check the definitions
before deciding something cannot be recorded.

## Deleting a field

Deleting a definition deletes the values stored under it, everywhere. There is no undo.

Definitions survive clearing the sample data. They are removed only by a full reset of the
CRM.
