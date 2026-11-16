---
title: Notifications
description: Browser push, the bell, and Telegram delivery. Works with no AI keys.
aliases: notifications, push, web push, alerts, bell, digest, nudges, mentions, @-mention, heartbeat, check-in, quiet hours
admin: false
---
## Who can do this

The Notifications card is in Settings, **Personal**, and every signed-in user sees it. The digest and
background check-in controls inside it are admin-only — see their sections below.

## Browser push

The toggle *is* the subscription: turning it on asks your browser for permission and
registers this browser to receive push. Turning it off unregisters it. It is per browser and
per device, so turning it on at your desk does nothing for your phone.

Push works with **no AI provider configured**. Delivery itself never needs a key.

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

Each person links their own Telegram from Settings, Personal, Link my Telegram, and a
notification addressed to one person reaches only that person's chat. A notification sent to
everyone reaches every linked chat. A seat that has not linked simply receives nothing there.

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

## Baker's background check-in

Separate from the daily digest, Baker checks for newly overdue todos, deals going cold and
contacts nobody has followed up with. It only speaks when something is new. A record stays
quiet until it leaves that watched set and later comes back, for example after a deal is
touched and then goes cold again. Closing one overdue todo does not repeat the others.

This is **install-wide and admin-only**, and needs an AI provider. In Settings, Personal,
Notifications, admins with a provider see **Baker's background check-in**:

- **Turn on / Turn off** controls these check-ins separately from the daily digest.
- **Check every** ranges from 30 minutes to once a day.
- **Quiet hours** use the install's local timezone, shown beside the controls. A window such
  as 22:00–07:00 can cross midnight. Clear both times to remove it.
- **At most one notification every** sets the minimum gap: four hours by default, from zero
  (no minimum) to 24 hours. New records wait through the gap and quiet hours for a later check.

Press **Save** to apply the interval, quiet hours and gap. An admin can also ask Baker
"only check once a day", "stop at night" or "pause for a week". A pause resumes by itself
when the requested number of days ends; **Resume** ends it early. Switching on does not
end a pause. Baker reads the values with `heartbeat_get_settings` and changes them with
`heartbeat_update_settings`. In Ask mode it shows an **Approve** card first; on Telegram
it asks for typed approval. In Auto it applies the change, like any other non-routine write.
No key or secret is involved.

The server operator's environment switch overrides these preferences. The admin run-now
check is for testing: it bypasses the schedule, pause, quiet hours, gap and repeat guard.

## What raises a notification

A teammate can mention you in a note on a deal, contact or company by typing `@` and picking
your name. You get a notification addressed to you, and its title opens that record. You are
never notified about mentioning yourself, and editing a note only notifies people who were
newly added to it.

Baker can raise one from a background turn, and it is limited to a single notification per
run. A background check-in notification is not repeated for an unchanged record. When its text names a deal Baker looked at during that run, the deal number is replaced
with the deal's title and a link that opens it, since a push or a Telegram message cannot
open a deal from its number. A number Baker did not look at is left as written. Baker cannot
mention anyone. The digest and nudges raise their own, each claimed before it is sent so two ticks can
never send the same one twice.
