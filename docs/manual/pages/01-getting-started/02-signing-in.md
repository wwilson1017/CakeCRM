---
title: Signing in
summary: Sign in with your email and password, and what happens when your account has two-factor turned on.
shots: [login]
---

Open your install's address (for example `https://crm.example.com`) and you land on the sign-in screen.

{{shot:login}}

## Signing in

1. Enter your **Email** and **Password**.
2. Press **Sign in**.

You arrive on the Dashboard. Your account is created by an admin from Settings, Workspace, Team; there is no sign-up form. On a brand-new install, the first admin account is created by the server from its environment settings. See [The first admin and passwords](../10-self-hosting/02-first-admin-and-passwords.md).

## Two-factor codes

If two-factor authentication is switched on for your account, a second screen asks for a code after your password.

- Enter the 6-digit code from your authenticator app and press **Verify**.
- Lost your phone? Press **Use a backup code** and enter one of your backup codes instead. Each backup code works once.
- Tick **Trust this browser for 30 days** to skip the code on this browser for a month.
- **Back** returns you to the password screen.

There is no screen in CakeCRM for turning two-factor on, showing a QR code or listing backup codes. The server supports it, but the setup screen has not been built.

## Signing out

Press **Sign out** at the top right of any page. On a phone it is at the bottom of the menu; see [Using CakeCRM on a phone](04-mobile.md).

## Forgotten passwords

There is no emailed reset link: a self-hosted install has no mail server to send one.

- **A member** who has forgotten their password asks an admin, who resets it from Settings, Workspace, Team. If they have also lost their authenticator, the admin can tick the box that clears two-factor at the same time.
- **The only admin** needs whoever runs the server to set a password-reset value in its environment. See [The first admin and passwords](../10-self-hosting/02-first-admin-and-passwords.md).

To change a password you still know, use Settings, Personal, Change password. Changing it signs you out everywhere else and keeps you signed in where you are. See [Personal settings](../08-settings/01-personal.md).
