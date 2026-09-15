---
title: Approving Baker's changes
description: When Baker asks before writing, how to approve, and what quietly tightens the rules mid-conversation.
aliases: confirmation, approve, deny, permission, write, power mode, read only, pending, budget, safety
admin: false
---
## Three modes

- **Read-only** — Baker is not even offered the tools that change anything, and would be
  refused if it named one anyway.
- **Normal** — every change asks first. This is the sensible default.
- **Power** — changes run straight away, with the exceptions below.

Read tools never ask, in any mode.

## What asking looks like

Baker's message carries a card naming the tool and showing exactly what it is about to do,
with **Approve** and **Deny**. Until you answer, the result Baker sees says the action is
pending your approval — which means nothing has happened yet, not that anything failed.

In Telegram the same thing arrives as two inline buttons. Only the linked account can press
them, and Baker waits until every pending change in a batch is answered before carrying on.

Approval is decided on the server: it reloads the tool and its arguments from its own record
rather than trusting what comes back from the browser, marks the call as running before it
runs it so a crash cannot double-execute it, and answers a repeated approval with the result
it already produced.

## Two things that ask even in power mode

- **Writing or deleting one of Baker's two protected knowledge files.** See
  `assistant/context-files`.
- **A conversation that has read untrusted outside content.** Reading mail, or uploading a
  file, brings in text somebody else wrote. From that point the conversation drops back to
  asking before every change, for the rest of its life — even after the message that brought
  the content in has aged out of view. The taint is recorded against the conversation itself,
  so it cannot be shaken off by talking for long enough.

That is the defence against prompt injection: text arriving from outside is wrapped and
labelled as data, so it cannot pose as an instruction, and if it somehow steered Baker anyway
the change still has to get past you.

## The write budget

An interactive turn may make at most 20 changes. The first one over is refused while the turn
carries on; anything further ends the turn. An unattended background turn has a smaller
budget still.

## Deleting is real

Deleting a contact, a company, a deal or a todo is permanent — there is no recycle bin.
Archiving a deal is the recoverable option (`pipeline/archived-deals`), and a todo can be set
to dropped instead of deleted.
