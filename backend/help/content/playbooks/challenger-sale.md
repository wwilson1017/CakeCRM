---
title: The Challenger Sale
description: Dixon and Adamson on why the best reps teach, tailor and take control — leading with commercial insight, for complex deals where the buyer likes you but nothing moves.
aliases: Dixon and Adamson, challenger, teach tailor take control, commercial insight, reframe, constructive tension, complex sale, relationship builder, nothing moves, consensus, mobilizer, teaching pitch
admin: false
author: Matthew Dixon and Brent Adamson
---
## Core ideas

- **Five profiles, and the relationship builder is not the winner.** The research sorted
  thousands of reps into five patterns. In complex sales the top performers were
  overwhelmingly challengers — people who teach the customer something, tailor the message
  to each stakeholder, and stay in control of the conversation. The warm relationship
  builder came last.
- **Teach the customer something about their own business.** Not about your product. The
  insight has to be something they did not know, that costs them money, and that leads
  naturally to a capability you happen to have. An insight that could lead anywhere is just
  interesting; an insight that only you can act on is a reason to buy.
- **Reframe first, then evidence.** The teaching pitch opens by challenging an assumption
  the buyer holds, supports the reframe with data, makes the cost emotionally real with a
  story about someone in their position, then shows the new way of working — and only at
  the end shows the product as how you get there.
- **Tailor to the person, not the logo.** The chief financial officer and the operations
  lead need different versions of the same insight. A message pitched at "the company"
  persuades nobody in particular.
- **Take control means talking about money and pushing back.** Challengers are comfortable
  discussing price, comfortable saying a request is unreasonable, and comfortable keeping
  the process on the path they think it should take. That is assertiveness, not aggression.
- **Constructive tension is the point.** A conversation with no tension in it is a pleasant
  meeting that changes nothing. Buyers say they value a rep who makes them think more than
  one who makes them comfortable.
- **Customer loyalty is won during the sale, not after it.** The single largest driver of
  loyalty in the study was the buying experience itself — what the buyer learned along the
  way — not brand, product or price.
- **Find the mobilizer, not the friend.** The most helpful contact is not the one who takes
  your calls; it is the one who can build agreement inside their own organization. The
  later work of the same research team is blunt that a friendly advocate with no internal
  credibility is a dead end.
- **The real competitor is the status quo.** Most complex deals are lost to "we decided not
  to do anything this year", which is why an insight about the cost of standing still beats
  a feature comparison.
- **This is a system, not a personality.** The book's argument is that challenger behaviour
  can be taught, scripted and supported — the insight comes from the organization, not from
  a gifted individual.

## When Baker reaches for it

- **They like us but nothing moves** — a deal sitting in *proposal* or *negotiation*
  carrying the *stuck in stage* flag with friendly activity and no decision.
- **The deal is losing to no decision** — recent losses with a reason that amounts to
  budget freeze, other priorities, or nothing at all.
- **Only one contact is linked to a large deal** — a complex purchase with a single
  champion and no other stakeholders on the record.
- **The pitch is a feature list** — the notes show demonstrations and capabilities, never
  an insight about the buyer's business.
- **The user is being asked to discount to restart the deal** — a reframe restarts a deal
  more reliably than a price cut, and costs nothing.
- **A renewal or expansion is drifting** — the account is happy and stationary.

## Applied to the CRM

- **Write the insight down before the meeting** — one sentence on what this buyer believes
  that is costing them, logged with `crm_add_note` on the deal so it can be reused and
  challenged rather than improvised.
- **Map the stakeholders onto real records** — a complex deal with one linked contact is a
  risk you can see. Use `crm_get_company` to pull up the company rollup and create the
  other stakeholders as contacts so the tailoring has somewhere to live.
- **Let the health flags find the stalled deals worth reframing** — `crm_get_deal_health`
  names *stuck in stage* and *no next step* together, which is the exact profile of a deal
  that needs a new idea rather than another check-in.
- **Tension becomes a next step, not a nudge** — book the conversation where you share the
  insight as a task (`crm_create_task`; `todo_create` in GTD task mode), with what you
  intend to challenge in the description.
- **Record the loss reason honestly** — when a deal dies of indecision, `crm_mark_deal_lost`
  with the real reason is what lets `crm_analytics` show how much of the pipeline the
  status quo is taking.

## Go read it

Matthew Dixon and Brent Adamson, *The Challenger Sale* (Portfolio, 2011). The chapters on
building a teaching pitch are the practical half, and the underlying study is large enough
that the uncomfortable finding about relationship builders is hard to argue with.
