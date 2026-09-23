---
title: How to Win Friends and Influence People
description: Carnegie's fundamentals for dealing with people — genuine interest, names, listening, seeing their side, admitting fault fast — for first meetings and relationship repair.
aliases: Carnegie, rapport, relationship repair, first meeting, genuine interest, remember names, listen, let them talk, admit fault, avoid arguments, appreciation, win friends
admin: false
author: Dale Carnegie
---
## Core ideas

- **Criticism almost never produces change.** People defend themselves rather than
  reconsider. Carnegie's opening argument, drawn from a lifetime of teaching public
  speaking to working adults, is that condemnation is the least efficient tool available
  for changing anyone's behaviour.
- **Appreciation has to be honest to work.** He draws a hard line between sincere
  appreciation and flattery, and argues that people can tell the difference far more
  reliably than the flatterer believes.
- **Talk about what the other person wants.** The only durable way to influence someone is
  to show how the thing you want serves something they already care about. Most persuasion
  fails because it explains why the speaker wants it.
- **Become genuinely interested in other people.** Not a technique for appearing
  interested — the actual thing. It is also the cheapest possible preparation: ten minutes
  learning what somebody's job is really like changes every conversation that follows.
- **A person's name is worth remembering and using.** Small, slightly embarrassing to state
  plainly, and consistently effective.
- **Be a good listener and encourage them to talk about themselves.** The best-regarded
  conversationalists mostly ask and listen. In a first meeting, the ratio of their talking
  to yours is a better predictor of how it went than anything you said.
- **You cannot win an argument.** Win it on the facts and you lose the person's goodwill,
  which was the thing you actually needed. Look for the part of their view that is right
  and start there.
- **Never say "you're wrong".** Show respect for their opinion, and let them change their
  mind without an audience to the change.
- **If you are wrong, admit it quickly and emphatically.** Get ahead of the criticism by
  making it yourself. It disarms almost everyone, and a self-administered admission costs
  far less than a defended one.
- **Let the other person feel the idea is theirs.** Ideas people arrive at themselves get
  implemented; ideas installed by someone else get resisted, regardless of quality.
- **Try honestly to see things from their point of view.** Carnegie's closing move on
  nearly every chapter, and the one everything else rests on.

## When Baker reaches for it

- **A relationship needs repair** — something went wrong, a delivery slipped, a contact has
  cooled, and the next conversation has to clear the air before it can do anything else.
- **A first meeting is being prepared** — a new contact with an empty activity history.
- **The user wants to win an argument with a buyer** — about a comparison, a benchmark, a
  technical point. Usually worth not winning.
- **The user is at fault** — a mistake has been made and the instinct is to explain it.
- **A long-standing contact has gone quiet** — contact staleness on somebody who used to
  reply quickly.
- **The tone of the notes has turned adversarial** — the record reads as a dispute rather
  than a relationship.

## Applied to the CRM

- **Prepare from the record, not from memory** — `crm_get_contact` and
  `crm_get_activity_log` give you what this person has actually said and done before you
  walk in. That is what "genuinely interested" looks like in practice.
- **Write down the personal details that matter** — the project they are proud of, the
  constraint they are stuck with, how they prefer to be reached. `crm_add_note` on the
  contact keeps it with the relationship; `memory_add_fact` keeps a durable preference
  where Baker will find it in a later conversation.
- **Log the first meeting properly** — `crm_log_activity` with what they said, not what you
  presented. A first meeting recorded as "intro call, went well" is a meeting nobody can
  build on.
- **Find the quiet relationships before they cool further** — `crm_get_contact_staleness`
  names the people who used to answer and no longer do.
- **Make the repair a real next step** — an apology call or a written acknowledgement as a
  task (`crm_create_task`; `todo_create` in GTD task mode), with what you are admitting to
  in the description so it does not soften on the way to the call.

## Go read it

Dale Carnegie, *How to Win Friends and Influence People* (Simon & Schuster, 1936). The
examples are ninety years old and the advice has not aged at all, which is the interesting
part — it is a book about attention and humility that somehow acquired a reputation for
being about manipulation.
