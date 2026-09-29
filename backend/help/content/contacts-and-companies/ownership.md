---
title: Record ownership
description: Owner is an assignment and a filter, not a permission. Anyone can edit anything.
aliases: owner, ownership, assign, unassigned, my records, reassign, who owns, permissions
admin: false
---
## Owner is not access control

Contacts, companies, deals and todos each carry an owner. **It grants and withholds nothing.**
Any signed-in person can read, edit, delete and reassign any record regardless of who owns
it. There are no per-record permissions in this product.

Say this clearly when someone asks, because "owner" in other CRMs often means "only they can
touch it", and here it does not.

What owner is for: knowing whose desk a record sits on, filtering a list to one person's
work, and splitting analytics per person.

## Unassigned is a real state

An owner can be empty, and that is legitimate rather than a mistake. The mail touch scan and
the assistant both produce unassigned records. So does every path that auto-creates a
**company** as a side effect of linking a contact — a bulk import or a quick-create picker —
because a company nobody picked up is not one anybody owns. The contacts an import creates
*are* owned, by whoever ran it. Lists show an **Unassigned** bucket
beside the named people, and record pages show the owner row even when it is empty — hiding
it is what makes an empty owner unreadable.

## Owner and author are different things

- **Owner** — who the record is assigned to.
- **Author** — who actually did a particular piece of work: who logged an activity, who wrote
  a note.

So a colleague's call on your account credits them, not you. Work Baker does on your behalf
currently records no author and rolls up as unattributed, which undercounts it but never
attributes it to the wrong person.

## Filtering by owner

Every list page has an Owner facet, including the Unassigned bucket, and filtering happens
over the whole loaded set so the counts and the rows always agree. The dashboard's Today
panel and the weekly-touches views have their own Mine and Everyone toggles. "Mine"
deliberately includes unassigned work, because somebody has to pick it up.

## Reassigning

Change the owner on the record. The dropdown offers Unassigned plus every active person. If
the current owner has since been deactivated they still appear, labelled as such, so opening
a form does not silently reassign them.

Baker can set a company's owner too, when it creates or updates one: ask it to assign the
company to you, to a teammate by email, or to nobody. A company Baker creates for you is yours
unless you say otherwise. It cannot yet reassign a contact or a deal — do that on the record.

## Admin versus member

An admin sees no more *records* than a member. The role difference is about install settings,
not data. See `settings/team`.
