---
title: Capturing without signing in
description: Two token-protected surfaces let you add or work todos from a phone with no login.
aliases: capture, no login, public, phone, quick capture, todo app, link, token, share
admin: false
---
## Two surfaces, and they are not equally open

- **Capture** is **write only**. It posts one line into your todo inbox and can read nothing
  back — there is no read endpoint on it at all. It is reachable without a token unless you
  set one, at which point the bare address stops existing.
- **The todo app** is the whole todo interface, read and write, sized for a phone. It is
  **off** until you switch it on, and switching it on mints its token in the same action, so
  it is never published at a guessable address.

Neither one reaches anything else in the CRM. A token gets you todos and projects and
nothing more: no contacts, no deals, no Settings, no assistant.

Neither one cares which todo mode is active.

## Getting around the todo app on a phone

On a phone-width screen the todo app puts its lists in a bar along the bottom: **Inbox**,
**Today**, **To Do**, **Projects** and **More**. More opens the rest — Contexts, Waiting,
Someday, Review and Done. On a wider screen the same lists sit in a row of tabs at the top
instead. Inside the CRM the todo pages always use the top tabs, since the bottom of the
screen there belongs to the CRM's own navigation and the assistant button.

## Turning them on

**Admin only**, in Settings, Assistant, Todo mode. That card is the only place the tokens are
ever shown, because the token *is* the credential.

You do not choose the token. Regenerating asks the server to mint a fresh random one, so a
secret is never typed or transmitted by a browser before it exists.

## The shape of the links

Each address is the surface's own path with the secret appended as an extra path segment —
no query string, no header, nothing to configure on the device. The token is long and random.

**Treat those links like passwords.** Anyone holding the todo app link can read and change
every todo, and that access is not tied to any account: deactivating the person who created
it does not revoke the link. Regenerate the token to revoke it. Never paste a live link into
a chat, a ticket or a transcript — read it off the Settings card on the device that needs it.

## How they behave when the token is wrong

- A wrong or missing token answers **not found**, never "unauthorized". Someone probing
  learns nothing about whether a surface exists.
- Wrong guesses burn a strict per-address budget; once it is spent, guesses answer "too many
  requests" instead.
- Ordinary use has a much larger budget, so your own phone is never rate-limited in practice.
- Captured text is capped, and an oversized request is refused before anything parses it.
- Every response asks browsers and crawlers not to cache or index it.
