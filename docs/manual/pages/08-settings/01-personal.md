---
title: Personal settings
summary: Notifications, your password, how the pipeline board opens, and linking your own Telegram.
shots: [settings-personal]
---

Open **Settings** at the top right. Settings has four tabs: **Personal**, **Assistant**, **Workspace** and **Integrations**. Everyone sees Personal and Assistant. Workspace and Integrations are for admins only, and members do not see those tabs at all.

{{shot:settings-personal}}

## Notifications

- **Enable Web Push** turns on browser notifications for this browser on this device. Your browser asks for permission first. Turn it on separately on each device you use.
- **Send test notification** sends a real notification and tells you whether it reached a device.

Push works with no AI keys. See [Notifications](../01-getting-started/06-notifications.md) for what raises one.

**Daily digest and nudges** is shown to admins only, because it is one setting for the whole team. It sends a pipeline summary each morning, plus a nudge when a deal goes cold or a contact with open business has not been spoken to. It also works with no AI keys.

## Change password

Enter your **Current password**, a **New password** of at least 8 characters, and confirm it. If two-step sign-in is on for your account, the form also asks for a code.

When the change succeeds, every other session you have ends straight away. You stay signed in where you are. A wrong current password is refused and does not sign you out.

Forgotten your password? Ask an admin to reset it. See [Workspace settings](03-workspace.md).

## Pipeline board

The pipeline hides the Won and Lost columns by default, so the board shows live work. Tick **Show Won and Lost stages** to bring them back on the board and in the list view. This is saved in this browser only. See [The pipeline board](../03-pipeline/01-board.md).

## Link my Telegram

Chat with Baker from your own phone. Once an admin has connected a Telegram bot:

1. Press **Get my link code**.
2. Press **Open Telegram to link**. If you are already in the chat with the bot, send `/link` and your code instead.
3. **Regenerate code** issues a fresh code and releases the device linked now. Use it to move to a new phone.
4. **Unlink my Telegram** ends your link. It affects nobody else.

If no bot is connected yet, the card says so. See [Telegram](../09-integrations/02-telegram.md).

## Light and dark

The light and dark switch is in the top bar, not in Settings. See [Light and dark mode](../01-getting-started/05-light-and-dark.md).
