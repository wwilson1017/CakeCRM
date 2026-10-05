---
title: The weekly review, step by step
description: The nine-step GTD weekly review Baker walks you through, the data each step uses, and how to mark a review done.
aliases: weekly review, review, start weekly review, mind dump, brain dump, review due, stale, waiting follow up, projects without a next action
admin: false
---
## How a review runs

A weekly review gets the todo system back to trusted: everything captured, the inbox empty,
every project moving. Open the **Review** tab and ask Baker to start one (the drawer offers
"Start my weekly review" there), or just say "let's do my weekly review" anywhere.

Baker calls `todo_weekly_review` once for the data, then walks the steps below **one at a
time**, waiting for your answer before moving on and making the changes you agree to as it
goes (`todo_create`, `todo_update`, `todo_bulk_update`, `todo_create_project`,
`todo_update_project`). Every section of the data carries its full count and up to 25 items
by id and title. When a section holds more, Baker says how many there are, works through the
ones it has, and calls `todo_weekly_review` again: an item that has been filed, done,
rewritten or given a next action leaves its section, so the next call shows the ones behind
it.

## The nine steps

1. **Mind dump.** Get everything out of your head before looking at the lists. Baker asks,
   one at a time: What have you promised someone — at work, at home, or to yourself — that
   is not written down? Who have you talked to this week, and does anything follow from it?
   What is coming up in the next few weeks that needs preparing? What is nagging at you —
   something broken, overdue, or undecided? Any idea you want to keep? Each answer is
   captured to the inbox with `todo_create`.
2. **Process the inbox to zero.** For each inbox item: is it actionable? Under two minutes,
   do it now. Not a next action, file it as waiting_for, delegated or someday_maybe.
   Otherwise agree its context, which files it as a next_action. Filing several the same way
   is one `todo_bulk_update`.
3. **Look back at what got done.** The todos completed in the last seven days. Celebrate
   them, and ask whether any of them leads to a next step.
4. **Due today or overdue.** For each dated todo that is due: do it, move the date, or drop
   it. A due date is for a real deadline only.
5. **Projects.** Is any project finished? Is there a new one? Every active project needs at
   least one next action; the data lists the active projects that have none, and each one
   gets a next action, a pause (someday) or completion.
6. **Waiting-for and delegated.** The ones nothing has touched in a week. Has it arrived?
   Is it time to follow up — and with whom?
7. **Stale next actions.** Next actions untouched for two weeks. Is each still the real next
   step? Rewrite it as a physical, visible verb, move it to someday, or drop it.
8. **Someday / maybe.** Items parked for over a month. Promote what is ready to a next
   action, and drop what no longer matters.
9. **Close the review.** A last look at the whole next-action list (`todo_list` with status
   next_action): keep a handful starred for the coming days. Then press **Mark review done**
   on the Review tab.

## Review due

The Review tab shows **Review due** when no review has been marked done in the last seven
days (or ever). Pressing **Mark review done** records today and clears the hint for a week.
Baker can see whether a review is due, but cannot mark one done — that button is yours, so
a review only counts once you have actually finished it.
