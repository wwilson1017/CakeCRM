---
title: No-login links
summary: Capture todos or sign a phone in once to work your whole list without logging in to CakeCRM.
shots: []
---

Two surfaces let you reach your todos without logging in to CakeCRM. They are **not equally open**: capture uses a public or secret address; the full todo app uses a separate device sign-in. An admin configures them in **Settings → Assistant → Todo mode**, under **No-login links**. See [Assistant settings](../08-settings/02-assistant.md).

## Quick capture (write-only)

A phone bookmark that drops text straight into your inbox. It cannot read anything back.

- It is **on by default with a public address**. Anyone who knows that address can add to your inbox. The card warns you about this.
- Press **New secret link** to give it a secret address. The plain address then stops working.
- **Make public** removes the secret again.

Items that arrive this way carry a **Capture link** label. Read them before acting on them.

## Full todo app (read and write)

The whole todo app, sized for a phone, at `/todo`. It is **off by default**. An admin turns on **Enable the no-login todo app** in Todo mode. Then each person signs in their own devices:

1. Open **Settings → Personal → Todo app devices** and press **Show a sign-in code**.
2. Scan the QR code with your phone or open the link there. The code is **single-use and expires after one hour**.
3. Press **Open my list on this device**. Opening the link alone does not sign you in.
4. Once signed in, add the todo page to your home screen:

- **iPhone or iPad (Safari):** tap Share (the square with an upward arrow; tap **•••** first if hidden), then **Add to Home Screen**. Leave **Open as Web App** on if shown, then tap **Add**.
- **Android (Chrome):** tap **⋮** beside the address bar → **Install and create shortcut** → **Install**. Older Chrome calls the menu item **Add to Home screen** or **Install app**.

On a phone, the lists sit in a bar along the bottom: **Inbox**, **Today**, **To Do**, **Projects** and **More** (Contexts, Waiting, Someday, Review and Done). Renaming contexts and saved Views are not available here.

## Protect your codes and capture link

Anyone who uses a todo sign-in code can read and change the whole todo list. Show it only to your own devices; do not paste it into a chat or ticket. Each phone stays signed in after using its code. **Sign out** in the todo app ends that device's session. **Sign out everywhere** in Todo app devices ends all devices you signed in and cancels your unused codes. Deactivating your account also signs your devices out.

Treat the **capture link** like a password too: anyone holding it can add to the inbox. **New secret link** replaces it and stops the old one working. This capture control does not revoke todo app sessions.

Old secret links to the full todo app are no longer used. Sign the phone in with a fresh code and replace any old home-screen icon with the signed-in `/todo` page.

Neither surface reaches anything else in the CRM: no contacts, deals, settings or assistant. Both work in either todo mode.

## Capture from Telegram

If your phone is linked to the team's Telegram bot, in GTD mode a message starting with `capture ` adds an inbox todo instantly, with no AI involved. See [Telegram](../09-integrations/02-telegram.md).
