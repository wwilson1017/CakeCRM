---
title: Never Split the Difference
description: Voss's negotiation method — tactical empathy, mirrors, labels and calibrated questions — for pushback, a price objection, or a counterpart who has gone quiet.
aliases: Voss, negotiation, tactical empathy, mirroring, labeling, calibrated questions, accusation audit, price objection, pushback, gone quiet, that's right, bargaining
admin: false
author: Chris Voss with Tahl Raz
---
## Core ideas

- **Negotiation is emotional before it is rational.** Voss's argument, drawn from hostage
  work rather than from business theory, is that the other side's decision is made by a
  person managing fear, status and the risk of looking foolish. Address that first and the
  numbers get easier; lead with the numbers and the person digs in.
- **Tactical empathy is naming the other side's position, not agreeing with it.** You
  demonstrate that you understand what they are up against. Understanding costs you
  nothing and is the fastest way to lower someone's guard.
- **Mirroring.** Repeat the last three or four words they said, as a question, then stop
  talking. It is almost invisible in conversation and it reliably makes people expand on
  what they just told you — usually with the part they had left out.
- **Labelling.** Put a name to the emotion you are hearing: "it sounds like the timing is
  the real problem here". A label that lands gets confirmed and defused. A label that
  misses gets corrected, which is just as useful.
- **The accusation audit.** Before a hard conversation, say out loud the worst things they
  could be thinking about you — you are expensive, you went quiet for a month, the last
  rollout was painful. Saying it first takes the weapon out of their hands and buys you
  credibility for everything that follows.
- **Calibrated questions start with "how" or "what", never "why".** "How am I supposed to
  do that?" hands them your problem to solve. "Why" reads as an accusation in almost every
  language.
- **"That's right" beats "you're right".** Voss's test for whether you have actually been
  understood: "you're right" is what people say to end a conversation, while "that's right"
  is what they say when you have summarized their own position back to them better than
  they did. Aim for the second and keep going until you get it.
- **"No" is the start of the conversation.** A quick yes is often a person getting rid of
  you. Questions people can safely answer "no" to ("is now a bad time?") get you a real
  answer and leave them feeling in control.
- **Never split the difference.** Meeting in the middle feels fair and usually produces an
  outcome neither side actually wanted. Hold the shape of the deal and trade on things
  that are not price.
- **Concede on a decreasing curve.** If you must move on price, make each concession
  smaller than the last and end on an odd, precise-looking number. A pattern of shrinking
  moves signals you are near your limit far better than saying you are near your limit.

## When Baker reaches for it

- **They pushed back on price** — the deal sits in *negotiation* and the value has moved
  down, or the user says the buyer called the quote high.
- **The counterpart has gone quiet** — the deal carries the *stale* flag, or the contact
  staleness read shows weeks of silence after a proposal went out.
- **The relationship has a grievance in it** — a late delivery, a support failure, a long
  gap in contact. That is what the accusation audit is for.
- **A deal is stuck in one stage** — the *stuck in stage* health flag on a late-stage deal
  usually means an unspoken objection nobody has named yet.
- **The user is about to concede to save the deal** — before they discount, there is
  almost always a trade available that costs less.
- **A discovery call produced agreement but no detail** — lots of "you're right", no
  substance. Mirror and label until you get a "that's right".

## Applied to the CRM

- **The accusation audit becomes the next step** — before the call, list what the buyer may
  be holding against you and write it on the deal with `crm_add_note`, then book the call
  as a task (`crm_create_task`; `todo_create` in GTD task mode) so the deal stops carrying
  *no next step*.
- **A label becomes a logged fact** — when a label lands and the buyer confirms it, that is
  the real objection. Record it with `crm_log_activity` on the deal so the next
  conversation starts from it rather than rediscovering it.
- **Before a price conversation, read the deal first** — `crm_get_deal_health` gives the
  stage, the value, how long it has sat and whether anything is outstanding. Walking into a
  concession without knowing how long the deal has been stuck is how the discount gets made
  twice.
- **Silence has a move, and it is a question, not a chase** — for a deal flagged *stale*,
  draft a short calibrated question rather than a follow-up (`gmail_create_draft`), and
  note what you asked with `crm_add_note`.
- **A real "that's right" is a stage decision** — when the buyer restates their own problem
  back to you, the deal has earned the next stage; move it with `crm_update_deal_stage`
  rather than letting it sit in *proposal* on optimism.

## Go read it

Chris Voss with Tahl Raz, *Never Split the Difference* (HarperBusiness, 2016). The book is
built on transcripts of real negotiations where the stakes were lives, and reading the
dialogue is what teaches the timing — a summary can give you the moves, but not the ear for
when to stay silent.
