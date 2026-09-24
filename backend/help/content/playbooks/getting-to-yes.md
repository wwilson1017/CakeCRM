---
title: Getting to Yes
description: Fisher and Ury's principled negotiation — separate the people from the problem, focus on interests not positions, invent options, insist on objective criteria, know your BATNA.
aliases: Fisher and Ury, principled negotiation, BATNA, interests not positions, objective criteria, walk-away, concessions, positional bargaining, options for mutual gain, best alternative, fair standard, separate people from problem
admin: false
author: Roger Fisher and William Ury with Bruce Patton
---
## Core ideas

- **Positional bargaining wastes what it is trying to protect.** Two sides state numbers
  and grind toward the middle. It is slow, it damages the relationship, and it reliably
  produces worse agreements than the same two sides could have reached by talking about
  what they actually need.
- **Separate the people from the problem.** Negotiators are human beings with egos and
  bosses. Be hard on the problem and soft on the person — attack the proposal, never the
  counterpart, and treat their emotions as facts about the situation rather than as
  tactics.
- **Focus on interests, not positions.** A position is what they say they want; an interest
  is why they want it. Two sides with incompatible positions very often have compatible
  interests — the classic illustration is two people fighting over one orange when one
  needs the juice and the other needs the peel.
- **Interests are often unstated and sometimes unflattering.** Budget authority, internal
  politics, a promise made to somebody else, a fear of being blamed. Asking "what would
  make this work for you?" opens more ground than another counter-offer.
- **Invent options for mutual gain before deciding.** Generate possibilities without
  judging them, separately from the decision about which to take. The instinct to evaluate
  each idea as it arrives kills the ideas that would have been worth developing.
- **Insist on objective criteria.** When interests genuinely conflict, look for an external
  standard both sides can accept — market rate, precedent, an independent valuation. It
  turns a contest of wills into a shared search for a fair answer, and it lets someone
  concede without losing face.
- **Know your BATNA — your best alternative to a negotiated agreement.** It is the only
  real source of negotiating power, and most people overestimate theirs. Work out what you
  will actually do if this falls through, improve it if you can, and measure every offer
  against it rather than against the last offer.
- **Do not set a bottom line too early.** A rigid number fixed in advance protects you from
  a terrible deal at the cost of blinding you to a creative good one. A well-understood
  alternative does the same job without the blindness.
- **When they will not play, change the game.** Negotiation jujitsu: meet an attack on your
  proposal with a question about their interests, and a personal attack with silence.
  Refusing to counter-punch often drags the conversation back to the problem.
- **Dirty tricks are negotiable too.** An escalating demand, an unnamed decision-maker,
  extreme anchoring — name what you see happening, and negotiate the process before
  continuing on the substance.

## When Baker reaches for it

- **The deal is in negotiation and both sides are trading numbers** — the classic
  positional grind, and the moment interests are worth surfacing.
- **The user is preparing concessions** — knowing the walk-away is what makes a concession
  a decision rather than a reflex.
- **A procurement team has entered the deal** — process games and manufactured deadlines
  are their normal tools.
- **The relationship is straining under the deal** — friction that started as a
  disagreement has become personal.
- **Price has become the only topic** — an objective standard usually exists and nobody has
  looked for it.
- **A deal is worth less than the effort it needs** — the honest answer may be that the
  alternative is better, and the book gives a clean way to see that.

## Applied to the CRM

- **Write down the interests behind the positions** — a short list of what each side
  actually needs, logged on the deal with `crm_add_note`, is the artefact this method
  produces and the one that survives a handover.
- **Record the alternative before the call, not after** — what the user does if this deal
  dies belongs on the record, so a late-night concession gets measured against something
  written down. Use `crm_add_note`, or a configured field if `crm_get_deal_fields` shows
  one that fits.
- **Read the deal's real shape first** — `crm_get_deal_health` gives the value, the stage,
  and how long this has run. A deal that has been in *negotiation* for months is usually
  about an unnamed interest, not about price.
- **Every concession becomes an activity** — log what was given and what came back with
  `crm_log_activity`, so the pattern is visible and the same ground is not conceded twice.
- **Agreement earns a stage change** — once the criteria are settled, move it with
  `crm_update_deal_stage`, and create the paperwork step as a todo (`crm_create_todo`;
  `todo_create` in GTD todo mode).

## Go read it

Roger Fisher and William Ury with Bruce Patton, *Getting to Yes* (Houghton Mifflin, 1981;
revised editions since). It is short, it is about forty years old, and it is still the
clearest account of why splitting the difference is a failure of imagination.
