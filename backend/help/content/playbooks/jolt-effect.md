---
title: The JOLT Effect
description: Dixon and McKenna on customer indecision — late-stage deals die of the buyer's fear of getting it wrong, so judge it, offer a recommendation, limit exploration, take risk off.
aliases: Dixon and McKenna, JOLT, indecision, still deciding, stalled deal, no decision, fear of failure, offer a recommendation, limit exploration, take risk off the table, late stage, omission bias
admin: false
author: Matthew Dixon and Ted McKenna
---
## Core ideas

- **Most lost deals are lost to indecision, not to a competitor.** The study behind the
  book analysed a very large body of recorded sales conversations and found that the
  majority of "no decision" outcomes came from customers who wanted to buy and could not
  bring themselves to commit.
- **Indecision is a different thing from the status quo.** Classic sales training treats a
  stalled deal as a buyer who is happy where they are, and responds by amplifying the pain
  of standing still. But a buyer paralysed by fear of choosing wrongly already accepts the
  pain — more of it makes them freeze harder.
- **Three fears drive it.** Valuation problems (am I sure this is worth it?), lack of
  information (have I done enough homework?), and outcome uncertainty (what if it does not
  work and I am the one who chose it?). The last is personal, and it is the strongest.
- **Judge the indecision.** Before anything else, work out whether you are dealing with
  someone who is satisfied with today, or someone who wants to move and is scared. The
  behaviour looks identical — vague delays, requests for more material — and the two need
  opposite responses.
- **Offer a recommendation.** Counter-intuitively, the winners stopped presenting
  balanced option sets and told the customer what they should do and why. A menu of
  choices is a kindness that transfers the risk of being wrong onto the buyer.
- **Limit the exploration.** Endless discovery, another demonstration, one more reference
  call — these feel like progress and usually deepen the paralysis. Good reps cap it: we
  have enough to decide, here is what the remaining questions would and would not change.
- **Take risk off the table.** Make the decision safer rather than more attractive. A
  smaller first phase, a defined checkpoint, a clear exit, explicit ownership of what
  happens if a milestone is missed. The buyer's private question is "what happens to me if
  this fails", and it needs an actual answer.
- **Do not confuse activity with intent.** A buyer asking for more information late in a
  deal is often signalling anxiety, not interest. Answering the literal question misses it.
- **Omission feels safer than commission.** Doing nothing produces no story anybody can be
  blamed for. That asymmetry, not a rational cost comparison, is what a late-stage deal is
  really fighting.
- **Pressure is the wrong lever here.** Urgency tactics against an anxious buyer confirm
  the fear that they are being pushed into a mistake.

## When Baker reaches for it

- **The buyer is "still deciding"** — the phrase itself, and its cousins: circling back,
  waiting on bandwidth, one more look at the numbers.
- **A late-stage deal has stalled** — *proposal* or *negotiation* carrying the *stuck in
  stage* flag, with the relationship still warm.
- **The user keeps sending more material** — another deck, another reference, another
  demonstration, and nothing moves.
- **A deal died with no competitor named** — losses recorded as no decision or budget,
  rather than lost to somebody else.
- **The buyer asks for options** — the moment to give a recommendation instead.
- **The champion has gone quiet after being enthusiastic** — enthusiasm followed by silence
  is usually fear, not a change of heart.

## Applied to the CRM

- **Separate the two kinds of stall on the record** — `crm_get_deal_health` shows *stuck in
  stage* and how long, and the notes say whether the buyer wants to move. Write which
  diagnosis you reached with `crm_add_note`, because the response depends entirely on it.
- **Turn the recommendation into the next step** — one todo (`crm_create_todo`;
  `todo_create` in GTD todo mode) that says what you will recommend and on what call, so
  the deal stops carrying *no next step* and stops accumulating more exploration.
- **Record the de-risking terms** — the smaller first phase, the checkpoint, the exit. Log
  it with `crm_log_activity` so what was promised is on the deal rather than in somebody's
  memory.
- **A real decision earns a stage move** — when the buyer accepts the recommendation, move
  it with `crm_update_deal_stage`; letting a decided deal sit in *proposal* is how a second
  round of doubt gets invited in.
- **Name indecision as the loss reason when it is one** — `crm_mark_deal_lost` with an
  honest reason. "No decision" and "lost to a competitor" are the same row to
  `crm_analytics`, which counts losses without grouping them by reason, so the written
  reason on each deal is what tells you later how much of the pipeline went to nobody.

## Go read it

Matthew Dixon and Ted McKenna, *The JOLT Effect* (Portfolio, 2022). The data is the
argument — several hundred thousand recorded conversations — and the finding that giving a
recommendation beats presenting options is worth having in full rather than in summary.
