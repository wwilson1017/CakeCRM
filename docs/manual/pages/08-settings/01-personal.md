---
title: Personal settings
summary: Notifications, your password, how the pipeline board opens, linking your own Telegram, installing CakeCRM on your phone, and connecting your own AI tool.
shots: [settings-personal]
---

Open **Settings** at the top right. Settings has four tabs: **Personal**, **Assistant**, **Workspace** and **Integrations**. Everyone sees Personal and Assistant. Workspace and Integrations are for admins only, and members do not see those tabs at all.

{{shot:settings-personal}}

## Notifications

- **Enable Web Push** turns on browser notifications for this browser on this device. Your browser asks for permission first. Turn it on separately on each device you use.
- **Send test notification** sends a real notification and tells you whether it reached a device.

Push works with no AI keys. See [Notifications](../01-getting-started/06-notifications.md) for what raises one.

**Daily digest and nudges** is shown to admins only, because it is one setting for the whole team. It sends a pipeline summary each morning, plus a nudge when a deal goes cold or a contact with open business has not been spoken to. It also works with no AI keys.

**Baker's background check-in** is a separate admin-only block, shown when an AI provider is ready. It reports newly overdue todos, cold deals and untouched contacts without repeating unchanged records. Set on/off, **Check every** (30 minutes to once a day), **Quiet hours** in the displayed install timezone, and **At most one notification every** (four hours by default). Quiet hours can cross midnight. Save applies the schedule; **Resume** ends a pause. Admins can also ask Baker to change these settings or pause for a number of days.

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

## Install on your phone

Everyone sees this card under **Personal**, on a computer or a phone. On a computer it shows this site's address: open it on your phone and sign in first. Expand the steps for your device.

- **iPhone or iPad (Safari):** tap Share (the square with an upward arrow; tap **•••** first if hidden), then **Add to Home Screen**. Leave **Open as Web App** on if shown, then tap **Add**.
- **Android (Chrome):** tap **⋮** beside the address bar → **Install and create shortcut** → **Install**. Older Chrome calls the menu item **Add to Home screen** or **Install app**.

On **Chrome or Edge on a computer**, click the install icon at the right end of the address bar, then **Install**. If the browser offers a native prompt, the card also shows an **Install CakeCRM** button.

Open CakeCRM from its home-screen icon from then on. There is nothing to download from an app store; the card confirms when you are using the installed app. If installation is missing, use an **https://** address and, on iPhone, Safari rather than an in-app browser.

The [no-login todo app](../05-todos/11-no-login-links.md) can have its own home-screen icon: sign the phone in through **Todo app devices**, then follow the same steps on the todo page.

## Connect an AI tool

Connect an AI tool you already use, such as ChatGPT, Claude, Claude Code or Codex, so it can work in CakeCRM as you. It sees what you can see, and what it adds or changes is recorded as you, made through that tool. CakeCRM needs no AI keys for this. Members can connect once an admin has switched it on under [Workspace settings](03-workspace.md); admins always can.

- **ChatGPT or Claude**: copy the address on the card and add it as a connector in the app. A CakeCRM window opens. Sign in, check which app is asking, choose **Read and write** or **Read only**, and press **Allow**. This needs your CakeCRM to have a public https address; the card says so if it has none.
- **Coding tools**: create a personal token with a name, Read and write or Read only, and an expiry of 30 days, 90 days, a year or never. It is shown once, so copy it. Pick your tool on the card to get the exact command or settings block with the token filled in.

Tools that archive, merge, drop, change many records at once or set reminders ask you before they run. A connected tool never sees Gmail, never deletes a contact, and never changes Baker's notes or memory.

Each connection shows when it was connected and last used. **Calls** lists what it did over the last 90 days, without what it asked or got back. **Revoke** disconnects it at once and takes it off the list, log included, so check **Calls** first. A password change disconnects all of your tools.

## Light and dark

The light and dark switch is in the top bar, not in Settings. See [Light and dark mode](../01-getting-started/05-light-and-dark.md).
