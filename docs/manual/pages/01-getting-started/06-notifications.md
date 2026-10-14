---
title: Notifications
summary: The bell, browser push, Telegram and the daily digest, and what raises a notification in the first place.
shots: []
---

CakeCRM tells you when something needs you through the bell in the header, and optionally through browser push and Telegram. None of it needs an AI provider.

## The bell

The bell sits in the header on every page, on a phone too. Its badge counts your unread notifications. Open it to read them, dismiss one with its **✕**, or clear the lot with **Dismiss all**.

When the system itself has a problem to report, an alert appears at the top of the list with **Acknowledge** and **Resolve** buttons. Alerts are shared by the whole install.

## Who sees a notification

Every notification is one of two kinds.

- **Addressed to you.** Nobody else sees it, and nobody else can dismiss it. A mention, or a nudge about a deal or contact you own, is the usual case.
- **Sent to everyone.** The daily digest, anything Baker raises while working in the background, and a nudge about a record with no owner. Dismissing one of these dismisses it for the whole team.

## What raises a notification

- **Mentions.** A teammate types `@` in a note on a deal, contact or company and picks your name. The notification's title opens that record. See [Notes, mentions and attachments](../04-contacts-and-companies/05-notes-mentions-attachments.md).
- **The daily digest and nudges**, when an admin has turned them on (below).
- **Baker**, at most one notification per background run, once an AI provider is connected.

## Browser push

In Settings, Personal, the **Notifications** card has **Enable Web Push**. Pressing it asks your browser for permission and registers this browser. It works per browser and per device: turning it on at your desk does nothing for your phone. **Disable Web Push** turns it off again.

If your browser has blocked notifications for the site, the card says so; allow them in your browser's settings first.

**Send test notification** sends a real notification through the whole delivery path and tells you whether it reached a device or only landed in the bell.

## Telegram

If you link your Telegram account from Settings, Personal, **Link my Telegram**, your notifications also go to that chat. There is no separate on/off switch for it. See [Telegram](../09-integrations/02-telegram.md).

## The daily digest and nudges (admins)

The same card has a **Daily digest and nudges** section with **Turn on** and **Turn off**. It sends a pipeline summary each morning, plus a nudge when a deal goes cold or a contact with open business has not been spoken to. It applies to the whole install, so only admins see the control.
