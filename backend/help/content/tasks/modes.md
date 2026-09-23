---
title: Task modes
description: Normal is a simple task list; GTD is the full workflow. Same records, switch freely.
aliases: task mode, normal mode, gtd mode, todos, tasks, switch, follow ups
admin: false
---
## Todos are the one follow-up primitive

Everything you want to come back to lives in one place: the todo store. There is no second,
parallel follow-up mechanism to keep in step with it.

## The two modes

**Normal mode** is a plain task list. The Todos page shows a table: done, the task and what it
is linked to, priority, and the due date, with an Open / Done / All filter that starts on
Open.

**GTD mode** is the full Getting Things Done workflow: an inbox to triage, next actions,
waiting-for and delegated items, someday-maybe, projects, contexts, tags, stars, repeats and
a weekly review. The Todos page becomes nine tabs. See `tasks/gtd`.

**GTD is the default** on a new install.

## Switching changes nothing about your data

Both modes are views over the **same** rows. Switching:

- migrates nothing,
- loses nothing,
- is instant,
- and is reversible either way.

A todo you created in one mode is the same record in the other. GTD simply uses fields that
normal mode leaves at their defaults.

## What else the switch changes

The tools Baker is offered swap with the mode. In normal mode it gets the simple task tools
— `crm_create_task`, `crm_list_tasks`, `crm_complete_task`, `crm_update_task` and
`crm_delete_task`. In GTD mode those are withdrawn and the `todo_` tools take their place. It
is never handed both vocabularies for one store, and its working instructions change with the
mode so it never coaches you toward a tool it cannot see.

## Who can switch

**Admin only**, in Settings, Assistant, Task mode. The mode is one install-wide setting, so
one person flipping it changes the task surface for everybody.

## Completing a repeating todo

Whichever mode you are in, completing a repeating todo spawns its next occurrence. That
happens on the server, so the plain checkbox in normal mode does it just as the GTD interface
does.
