---
title: No-login links
summary: Add todos, or work your whole list, from a phone without signing in.
shots: []
---

Two links let you reach your todos with no sign-in. They are **not equally open**, so read this before you turn them on. An admin sets both up in **Settings → Assistant → Todo mode**, under **No-login links**. See [Assistant settings](../08-settings/02-assistant.md).

## Quick capture (write-only)

A phone bookmark that drops text straight into your inbox. It cannot read anything back.

- It is **on by default with a public address**. Anyone who knows that address can add to your inbox. The card warns you about this.
- Press **New secret link** to give it a secret address. The plain address then stops working.
- **Make public** removes the secret again.

Items that arrive this way carry a **Capture link** label. Read them before acting on them.

## Full todo app (read and write)

The whole todo app, sized for a phone, with no login. It is **off by default**.

- Tick **Enable the no-login todo app**. A secret link is made in the same step, so it is never published at a guessable address.
- On a phone, the lists sit in a bar along the bottom: **Inbox**, **Today**, **To Do**, **Projects** and **More** (Contexts, Waiting, Someday, Review and Done).
- Renaming contexts and saved Views are not available here.

## Treat these links like passwords

Anyone holding the todo app link can read and change every todo. The link is not tied to any account, so deactivating the person who shared it does not stop it.

- **Copy** puts a link on your clipboard. Open it on the device that needs it, rather than pasting it into a chat or ticket.
- **New secret link** replaces the link at once. The old one stops working, including any home-screen app installed from it. This is how you revoke access.

Neither link reaches anything else in the CRM: no contacts, deals, settings or assistant. Both work in either todo mode.

## Capture from Telegram

If your phone is linked to the team's Telegram bot, in GTD mode a message starting with `capture ` adds an inbox todo instantly, with no AI involved. See [Telegram](../09-integrations/02-telegram.md).
