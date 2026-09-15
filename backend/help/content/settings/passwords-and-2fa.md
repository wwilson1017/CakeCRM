---
title: Passwords and two-factor
description: Change your password, what two-factor does at login, and the honest state of turning it on.
aliases: password, change password, 2fa, two factor, totp, authenticator, backup codes, security, login, locked out
admin: false
---
## Changing your own password

Settings, Personal, Change password. Give your current password and the new one. If
two-factor is switched on for your account the form also asks for a code.

- Minimum 8 characters.
- A wrong current password is refused with a plain error and does **not** sign you out.
- Your current password is checked *before* the two-factor code, so a typo in the password
  field never burns a code.
- On success **every other session ends immediately** and trusted devices are revoked. You
  stay signed in where you are.

## Two-factor: read this before advising anyone

The server supports time-based one-time codes, ten single-use backup codes, and remembering
a trusted device for 30 days. The login screen asks for a code when an account has it
switched on.

**There is no screen anywhere in this product to turn it on.** Nothing shows a QR code,
nothing lists backup codes, nothing disables it, nothing regenerates codes. The only way to
enable it is to call the API directly. So the accurate answer to "how do I turn on
two-factor?" is: you cannot, from the interface — the capability exists on the server and
its setup screen has not been built.

What you *can* see is whether it is currently on for you: the change-password card asks for
a code only when it is.

## If someone is locked out

- **A member** who forgot their password: an admin resets it from Settings, Workspace, Team.
  If they also lost their authenticator, the admin ticks the box that clears two-factor at
  the same time.
- **The only admin**, or everyone: the operator sets a password-reset value in the server
  environment. On the next boot it resets the lowest-numbered admin's password, reactivates
  that account, clears its two-factor and trusted devices, and ends its sessions. It is
  re-applied on **every** boot while it is set, and the server logs a loud warning telling
  the operator to remove it afterwards.

There is no emailed reset link. A self-hosted install has no mail infrastructure to send one.
