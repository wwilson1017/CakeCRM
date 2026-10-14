---
title: Custom fields
summary: Extra fields of your own on contacts, companies and deals.
shots: []
---

Custom fields let you record things the built-in fields do not cover. They can be added to **contacts**, **companies** and **deals**.

## Defining fields (admins)

An admin sets them up in **Settings → Workspace → Custom fields**. Pick **Contacts**, **Companies** or **Deals**, then **+ Add custom field**. Give the field a name and a type:

| Type | What it holds |
|---|---|
| text | Any text |
| number | A number |
| boolean | A checkbox |
| date | A calendar date |
| select | One choice from a list you supply |

See [Workspace settings](../08-settings/03-workspace.md).

## Filling them in (everyone)

Anyone signed in can fill in the values. Custom fields appear on the record's create and edit forms, and in a Custom fields section on its page. Only defining fields is limited to admins.

You can always empty a field, even a number or date field.

## "Required" is advisory

A field marked required shows as required on the form, but the app still saves a record without it. Treat it as a prompt, not a rule.

## Baker and custom fields

With an AI provider connected, Baker can read and fill in custom fields on contacts, companies and deals, like any other field.

## Deleting a field

Deleting a field definition deletes every value stored under it, on every record. There is no undo.

Field definitions stay when you clear the example data. They are removed only by a full reset of the CRM.
