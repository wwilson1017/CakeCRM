---
title: Reminders
description: A fixed note delivered to chosen teammates at a date and optional time, once or on repeat. Never by email.
aliases: reminder, remind me, remind, nudge, alarm, repeating reminder, remind again, void reminder
admin: false
---
## What a reminder is

A reminder is a note you write once that is delivered, word for word, to the people you
choose at a date and optional time. Nothing interprets it when it goes out: no AI runs, and
it works the same with no AI key configured.

A reminder is not a todo. It never appears in todo lists, and finishing it is not a thing:
it is sent, and that is all. Use a todo for work you need to do; use a reminder for "tell
these people this, then".

## When it is sent

- **Date and time** — sent at that time, in the install's timezone, within a minute.
- **Date only** — sent at 9:00 in the morning. A date-only reminder for today is accepted
  at any hour and is sent within a minute.
- A date before today, or a time today that has already passed, is refused.

## Who gets it, and how

People are teammates with a sign-in. When you pick nobody, it goes to you. Each person gets
an in-app notification (the bell), plus a browser push or a Telegram message if they turned
those on in Settings. **A reminder is never sent by email.** Someone whose sign-in has been
deactivated is skipped.

A teammate who receives your reminder sees who set it.

## Repeating

A reminder can repeat daily, weekly, monthly or yearly, counted from its first date. A
monthly reminder on the 31st goes out on the last day of shorter months and comes back to
the 31st. The next occurrence is created when the current one is sent, so there is always
exactly one upcoming reminder for a repeating series. If the server was down when one was
due, it is sent once when it comes back, and the series carries on from the next future
date rather than catching up on every missed day.

## Status: upcoming, fired, voided

- **Upcoming** — waiting to be sent. Only an upcoming reminder can be edited (its note,
  date, time, people or repeat) or voided.
- **Fired** — sent. It stays as a record.
- **Voided** — cancelled with a reason before it was sent. Voiding a repeating reminder
  ends the series. Nothing is ever deleted.

**Remind again** copies any reminder's note and people to a new date as a one-time reminder.

## Where to find them

The **Reminders** page lists every reminder with Mine / All, status and date filters.
Mine means reminders you set or receive. Reminders due today also appear on the
Dashboard's Today panel, under "+N more today".

Any teammate can edit or void any reminder; who set one is not a lock.

## Asking Baker

Baker can set, list, change and void reminders with `reminder_create`, `reminder_list`,
`reminder_update` and `reminder_void`. Setting, changing or voiding one always shows an
Approve card first, because someone will be notified. Name people by their email address,
their exact name, or "me".
