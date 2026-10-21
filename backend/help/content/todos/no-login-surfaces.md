---
title: Capturing without signing in
description: Capture todos from a phone with a secret link, and open the whole todo app on a signed-in phone without logging in to the CRM.
aliases: capture, no login, phone, quick capture, todo app, link, token, sign in, sign-in code, QR, device, sign out
admin: false
---
## Two surfaces, and they are not equally open

- **Capture** is **write only**. It posts one line into your todo inbox and can read nothing
  back — there is no read endpoint on it at all. It is reachable without a token unless you
  set one, at which point the bare address stops existing.
- **The todo app** is the whole todo interface, read and write, sized for a phone, at one
  address for everyone: `/todo`. It is **off** until an admin switches it on, and then each
  device signs in once. There is no link to copy or keep.

Neither one reaches anything else in the CRM. They get you todos and projects and nothing
more: no contacts, no deals, no Settings, no assistant.

Neither one cares which todo mode is active.

## Signing a phone in to the todo app

1. On a computer where you are logged in, open **Settings, Personal, Todo app devices** and
   press **Show a sign-in code**. You get a QR code and a link.
2. Scan the code with the phone, or open the link there.
3. Press **Open my list on this device**. That browser is now signed in and stays signed in.
4. Add the page to the phone's home screen if you like.

The code works **once, for one hour**. Opening the link does nothing by itself; only the
button signs the device in. Anyone who uses the code gets your whole todo list, so show it
only to your own devices. Signing a phone in never signs another device out.

The card also shows how many of your devices are signed in.

## Signing out

- **Sign out** in the todo app signs out that device only. Every other device keeps working.
- **Sign out everywhere**, on the Todo app devices card, signs out every device you signed in
  and cancels any codes you have not used yet.
- When an admin deactivates a person, that person's devices are signed out too.

A device that is signed out and opens `/todo` sees a short page with the steps to sign in. It
is never sent to the CRM login screen.

## After the upgrade from links

The todo app used to open from a secret link. That link is no longer used: an address saved
from before shows "This address is no longer used" with the same steps. The app stays switched
on, and each device signs in once. A home-screen icon added from the old link keeps opening the
old address, so remove it and add `/todo` again once the phone is signed in.

## Getting around the todo app on a phone

On a phone-width screen the todo app puts its lists in a bar along the bottom: **Inbox**,
**Today**, **To Do**, **Projects** and **More**. More opens the rest — Contexts, Waiting,
Someday, Review and Done. On a wider screen the same lists sit in a row of tabs at the top
instead. Inside the CRM the todo pages always use the top tabs, since the bottom of the
screen there belongs to the CRM's own navigation and the assistant button.

## Turning them on

**Admin only**, in Settings, Assistant, Todo mode: the switch for the todo app, and the
capture link. While the todo app is switched off, everyone's Todo app devices card says so and
offers nothing else.

You do not choose the capture token. Regenerating asks the server to mint a fresh random one,
so a secret is never typed or transmitted by a browser before it exists.

## The capture link

The address is `/capture` with the secret appended as an extra path segment — no query
string, no header, nothing to configure on the device. The token is long and random.

**Treat the capture link like a password.** Anyone holding it can add to your inbox, and
Baker reads what arrives through it as your own text — with no token configured it treats
each capture as a stranger's instead (see `assistant/confirmations`). Regenerate
the token to revoke it. Never paste a live link into a chat, a ticket or a transcript — read
it off the Settings card on the device that needs it.

## How they behave when something is wrong

- A wrong capture token, or a wrong, used or expired sign-in code, answers **not found**,
  never "unauthorized". Someone probing learns nothing about whether a surface exists.
- Wrong guesses burn a strict per-address budget; once it is spent, guesses answer "too many
  requests" instead.
- Ordinary use has a much larger budget, so your own phone is never rate-limited in practice.
- The todo app only accepts changes made from its own pages, so another website cannot make
  your signed-in phone change your todos.
- Captured text is capped, and an oversized request is refused before anything parses it.
- Every page asks browsers and crawlers not to cache or index it.
