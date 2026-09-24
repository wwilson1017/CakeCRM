---
title: Team and accounts
description: Add people, set admin or member, reset a password. Accounts are deactivated, never deleted.
aliases: team, users, accounts, seats, roles, admin, member, invite, deactivate, permissions
admin: true
---
## Roles

Exactly two: **admin** and **member**.

The split governs *install configuration*, not records. An admin can change how the install
is set up — branding, team, custom fields, integrations, todo mode, the assistant's
personality. A member can do everything else, including every CRM record.

## What an admin can do

In Settings, Workspace, Team:

- **Create** a person: email, name, password, role.
- **Edit** a person: rename, change role, deactivate or reactivate.
- **Reset** someone else's password, with an optional checkbox to also clear their
  two-factor setup and revoke their trusted devices. That checkbox is opt-in, never
  automatic, because it genuinely weakens the account — but it is the only recovery for
  someone who lost both their authenticator and their backup codes.

Passwords must be at least 8 characters.

## What nobody can do

- **Delete an account.** There is no delete. Accounts are deactivated instead, so a departed
  colleague's name still renders on the records they owned and no reference dangles.
- **Deactivate yourself**, or **remove your own admin role**. Both are refused.
- **Demote or deactivate the last active admin.** Promote someone else first.
- **Reset your own password here.** Use Settings, Personal, Change password, which checks
  your current password and your second factor. The admin reset path deliberately skips both
  of those, which is right for helping a colleague and wrong as a weaker self-service route.

A deactivation, a demotion or a password change bites on that person's very next request,
not whenever their session would have expired.

## Seats

There is no seat limit and no licence check. Add as many people as you want.

## Listing people

Every signed-in user can list users. That is deliberate: owner dropdowns and owner filters
have to turn an owner into a name.

## Ownership is not permission

Assigning a record to someone does not restrict anyone else. See
`contacts-and-companies/ownership`.

## What is per-seat, and what the team shares

Each person's conversation history with Baker is their own — owner-only, with no admin
override. So are their notifications and their Telegram link.

Baker's **memory** is shared: the facts it records and the context files it keeps are one
brain for the whole team, deliberately, because per-person memory would make Baker amnesiac
for every new seat. The honest cost is that something said in one person's private
conversation can become a fact every seat can see. System alerts and the AI provider keys are
install-wide too.

There is one **Gmail** connection per install, but using it is admin-only by default: a
member's Baker is not given the mail tools at all. An admin can share the mailbox with every
seat from Settings, Integrations, Gmail — see `settings/gmail`.

Changing Baker's two protected files, soul.md and MEMORY.md, is admin-only — on the Memory
page and through Baker, which refuses the rewrite on a member's seat. Everyone can still
read them, and topic files and daily notes are open to every seat either way. See
`assistant/context-files`.
