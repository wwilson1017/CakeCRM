---
title: First admin and passwords
summary: How the first admin account is created, how passwords change, and how to get back in when one is lost.
shots: [login]
---

You sign in with an **email and a password**.

{{shot:login}}

## The first admin

On its very first start, CakeCRM creates one admin account. Its email is `ADMIN_EMAIL` (default `admin@cakecrm.local`) and its password is `AUTH_PASSWORD`. Both are printed to the server logs on that first start, so check there if you are not sure what was used.

`AUTH_PASSWORD` is only the **starting** password. Once you change your password in the app, the new one is what counts, and editing `AUTH_PASSWORD` later has no effect.

## Adding your team

Add people at **Settings**, **Workspace**, **Team**. There are two roles:

- **Member** can use the whole CRM: records, pipeline, todos, notes, import, and Baker.
- **Admin** can do all of that, plus manage users, AI keys, branding, integrations and the destructive operations.

There is no seat limit. Accounts are never deleted, only deactivated, so a former colleague's name still shows on their records. See [Workspace settings](../08-settings/03-workspace.md).

## Changing your password

Go to **Settings**, **Personal**, **Change password**, and give your current password and the new one. The new password must be at least 8 characters. If two-factor is on for your account, the form also asks for a code.

Changing your password **signs out every other device immediately**; the one you are using stays signed in.

## When someone is locked out

- **A member** who forgot their password: an admin resets it at **Settings**, **Workspace**, **Team**, then tells them the new one. There is no emailed reset link, because a self-hosted install has no mail server to send one.
- **The admin**, or everyone: the person running the server rescues it from the environment.

To rescue the admin account:

1. Set `AUTH_PASSWORD_RESET` to a new password and restart. On Railway, adding the variable redeploys, which is the restart.
2. Sign in as the admin with that password. The logs carry a warning that the reset ran.
3. **Remove `AUTH_PASSWORD_RESET` and restart again.** While it is set, every restart applies it again, so a password you change in the app would not survive.

The reset reactivates the admin account if it was deactivated, turns off its two-factor sign-in, and ends its sessions.

## Two-factor sign-in

When two-factor is on for an account, the sign-in screen asks for the 6-digit code from an authenticator app, or a backup code. There is currently no screen in the app for turning two-factor on.
