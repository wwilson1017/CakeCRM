---
title: Ownership
summary: What a record's owner means, and what it does not.
shots: []
---

Contacts, companies, deals and todos each have an **Owner**.

## Owner is not a permission

**Owner grants and withholds nothing.** Anyone signed in can read, edit, delete and reassign any record, whoever owns it. There are no per-record permissions in CakeCRM.

What owner is for:

- knowing whose desk a record sits on,
- filtering a list to one person's work,
- splitting reports and counts by person.

## Unassigned is normal

A record can have no owner, and that is fine. Some records start out unassigned on purpose, such as a company created as a side effect of importing contacts or linking one on a form. Pages show **Owner** *Unassigned* rather than hiding the row.

## Filtering by owner

The Pipeline has an **Owner** filter. When your team has more than one person, the Contacts, Companies and simple Todos lists have one too. Each offers an **Unassigned** choice beside the names.

## Changing the owner

Edit the record and pick from the **Owner** list. It offers Unassigned and every active person. If the current owner has been deactivated, they still show, labelled as such, so opening a form never quietly reassigns the record.

A new contact or company you add starts owned by you. Contacts you import are owned by you.

## Owner versus author

The **owner** is who a record is assigned to. The **author** is who did a piece of work, such as who logged a call or wrote a note. A colleague's call on your contact is credited to them.

## Admins and members

An admin sees exactly the same records as a member. The difference between the two roles is about install settings, not data. See [Workspace settings](../08-settings/03-workspace.md).
