---
title: Notifications
description: Browser push, the bell, and Telegram delivery. Works with no AI keys.
aliases: notifications, push, web push, alerts, bell, digest, nudges
admin: false
---
## Who can do this

The Notifications card is in Settings, **Personal**, and every signed-in user sees it. One
control inside it is admin-only — see the digest section below.

## Browser push

The toggle *is* the subscription: turning it on asks your browser for permission and
registers this browser to receive push. Turning it off unregisters it. It is per browser and
per device, so turning it on at your desk does nothing for your phone.

Push works with **no AI provider configured**. Nothing in notifications needs a key.

## The bell

The bell in the app chrome lists notifications and carries a count of the unread ones.
Dismiss them one at a time or all at once. The count is asked for separately from the list,
so a long backlog still shows a true number rather than the size of one page.

## Who sees a notification

Every notification is one of two things, and the bell shows you both.

- **Addressed to you.** Nobody else sees it, and nobody else can dismiss it. A nudge about a
  deal or contact you own is the usual case.
- **Sent to everyone.** The daily digest, anything Baker raises from a background turn, and a
  nudge about a record with no owner — an unassigned deal going cold is everybody's problem.
  A record owned by a deactivated account counts as unowned here, so its nudges reach the
  people who can still act on it.

One thing to know about the shared kind: dismissing it dismisses it for the whole team, the
way every notification behaved before recipients existed. Your own notifications are yours to
dismiss alone.

Browser push follows the same rule. A notification addressed to you is pushed only to the
browsers you have enabled push on; a shared one is pushed to everybody's. A browser binds
itself to whoever is signed in on it the next time the app loads, so handing a laptop to a
colleague does not leave your notifications arriving on it.

## Telegram

If Telegram is linked, notifications also go to the linked chat. There is no per-channel
switch — when Telegram is connected it is used.

There is one install-wide Telegram link, not one per person, so the linked chat receives
notifications whoever they were addressed to. Per-person links are a later piece of work.

## Testing it

There is a test button. It sends a real notification through the whole delivery path with no
AI involved, and tells you whether push actually reached a device or whether it only landed
in the app.

## The daily digest

The same card has a second toggle for a daily digest and nudges. That one is **install-wide
state**, not a browser setting, so it is **admin only** and is hidden from members even
though the card around it is not. The digest itself is plain database work: a pipeline
summary, stale deals, contacts nobody has touched. With an AI provider configured it may add
at most one extra notification; with none, it still runs.

## What raises a notification

Baker can raise one from a background turn, and it is limited to a single notification per
run. The digest and nudges raise their own, each claimed before it is sent so two ticks can
never send the same one twice.
