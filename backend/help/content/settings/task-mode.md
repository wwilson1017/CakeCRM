---
title: Task mode
description: Switch between the simple task list and the full GTD workflow. One store, no migration, admin only.
aliases: task mode, gtd, todo mode, normal mode, switch modes, todos
admin: true
---
## The two modes

- **Normal** — a plain task list: title, due date, priority, done.
- **GTD** — the full Getting Things Done workflow: an inbox, next actions, waiting-for,
  delegated, someday, projects, contexts, tags, stars and a weekly review.

**GTD is the default** on a new install.

## Switching is safe

Both modes are views over the **same** underlying rows. Switching migrates nothing, loses
nothing and is instantly reversible. A todo created in one mode is the same record in the
other; GTD simply uses fields that normal mode leaves alone.

The switch also changes what Baker is offered: in normal mode it gets the simple task tools,
in GTD mode it gets the todo tools. It is never given both vocabularies for one store.

## Who can do this

**Admin only.** The mode is a single install-wide setting, so one person flipping it changes
the task surface for every seat. It lives in Settings, Assistant, Task mode.

## No-login links live on the same card

The same card configures the two surfaces that work without signing in, and **they are not
equally open**:

- The **capture page** is write-only and is reachable with **no token at all** until you set
  one. It is public by default. Setting a capture token switches it to a secret address and
  the bare one stops existing.
- The **phone-sized todo app** reads and writes every todo, is off until you switch it on,
  and mints its token in the same action, so it is never published at a guessable address.

Any token that exists is shown on this card and nowhere else. See
`tasks/no-login-surfaces`. Neither surface cares which task mode is active.
