---
title: Todo mode
description: Switch between the simple todo list and the full GTD workflow. One store, no migration, admin only.
aliases: todo mode, gtd, normal mode, switch modes, todos
admin: true
---
## The two modes

- **Normal** — a plain todo list: title, due date, priority, done.
- **GTD** — the full Getting Things Done workflow: an inbox, next actions, waiting-for,
  delegated, someday, projects, contexts, tags, stars and a weekly review.

**GTD is the default** on a new install.

## Switching is safe

Both modes are views over the **same** underlying rows. Switching migrates nothing, loses
nothing and is instantly reversible. A todo created in one mode is the same record in the
other; GTD simply uses fields that normal mode leaves alone.

The switch also changes what Baker is offered: in normal mode it gets the simple todo tools,
in GTD mode it gets the todo tools. It is never given both vocabularies for one store.

## Who can do this

**Admin only.** The mode is a single install-wide setting, so one person flipping it changes
the todo surface for every seat. It lives in Settings, Assistant, Todo mode.

## The no-login surfaces live on the same card

The same card controls the two surfaces that work without logging in to the CRM, and **they
are not equally open**:

- The **capture page** is write-only and is reachable with **no token at all** until you set
  one. It is public by default. Setting a capture token switches it to a secret address and
  the bare one stops existing. The token is shown on this card and nowhere else.
- The **phone-sized todo app** reads and writes every todo and is off until you switch it on
  here. It has no link: each person signs their own devices in under Settings, Personal, Todo
  app devices, with a one-time code.

See `todos/no-login-surfaces`. Neither surface cares which todo mode is active.
