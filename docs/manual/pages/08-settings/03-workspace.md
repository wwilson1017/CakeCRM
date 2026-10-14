---
title: Workspace settings
summary: Admin-only settings for your company name and logo, your team, custom fields, and exporting your data.
shots: [settings-workspace]
---

The **Workspace** tab is for admins only. Members do not see it.

{{shot:settings-workspace}}

## Branding

- **Company name** — shown throughout the app.
- **Logo** — press **Upload logo**. PNG, JPEG, GIF or WebP, up to 2 MB. SVG is not accepted. You can remove the logo from the same card.

Press **Save branding** to keep your changes.

There is no colour picker. CakeCRM has one fixed look, in light and dark. Light or dark is each person's own choice.

## Team

Everyone shares one CRM. There are two roles:

- **Admin** — can change how the install is set up: branding, team, custom fields, integrations, todo mode and Baker's protected memory files.
- **Member** — can do everything else, including working with every contact, company, deal and todo.

Records are the same for both. Owning a record is not a permission. See [Ownership](../04-contacts-and-companies/09-ownership.md).

What you can do here:

- **Add user** — email, name, password (at least 8 characters) and role.
- Change a person's role from the list beside their name.
- **Reset password** — set a new password for someone else. An optional box also clears their two-step sign-in, for someone who lost their authenticator. Use it only when needed.
- **Deactivate** — stop someone signing in. You can reactivate them later.

Accounts are never deleted, so a past colleague's name still shows on the records they owned. You cannot deactivate yourself, remove your own admin role, or demote or deactivate the last active admin. To change your own password, use [Personal settings](01-personal.md).

There is no seat limit.

## Custom fields

Pick **Contacts**, **Companies** or **Deals**, then **+ Add custom field**. Give it a name and a type: text, number, boolean, date or select. Deleting a field deletes its values everywhere, with no undo. See [Custom fields](../04-contacts-and-companies/10-custom-fields.md).

## Export data

**Download CSV export** on the Export data card downloads everything in the CRM as one ZIP, with a CSV file per table: companies, contacts, deals and their stage history, activity, todos and projects, notes, custom fields, saved views, Baker's memory, and a users file for looking up owner names. Archived deals are included. Passwords, two-factor secrets, API keys, connection tokens, note attachments and Baker conversations are never exported.

It is a snapshot, not a sync: download again for fresh numbers. Take one before a big change, such as clearing data or a large import.
