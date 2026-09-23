---
title: To Sell Is Human
description: Pink's case that everyone sells now, and his replacement ABCs — attunement, buoyancy and clarity — for reading the buyer, surviving rejection and finding the real problem.
aliases: Pink, attunement, buoyancy, clarity, new ABCs, rejection, morale, non-sales selling, problem finding, perspective taking, positive self-talk, servant selling
admin: false
author: Daniel H. Pink
---
## Core ideas

- **Most of us now sell without the title.** Pink's survey work found people spend a large
  share of their working time persuading, convincing and influencing others without any of
  it being called sales. Teachers, doctors, engineers, founders — the skills are general.
- **Information parity changed the job.** The old model rested on the seller knowing more
  than the buyer. When the buyer can check everything in ten minutes, the caricature of the
  fast-talking closer stops working, and honesty becomes the efficient strategy rather than
  the virtuous one.
- **The old ABCs are obsolete; the new ones are attunement, buoyancy and clarity.**
  Always-be-closing belonged to the information-asymmetry era.
- **Attunement is taking the other person's perspective, not feeling their feelings.** The
  research Pink cites suggests the useful move is cognitive rather than emotional — work
  out what they are thinking and what constraints they are under. Less power in the room
  makes you better at it, so deliberately assuming you have less helps.
- **Buoyancy is staying afloat in an ocean of rejection.** Three parts: what you ask
  yourself before (interrogative self-talk — "can I do this?" beats "I am great", because a
  question generates reasons), your ratio of positive to negative feeling during, and how
  you explain failure afterwards.
- **Explain rejection as temporary, specific and external.** Pink borrows the optimism
  research directly: reps who read a lost deal as "that one, this quarter, that reason"
  outlast reps who read it as "me, always, everything".
- **Clarity means finding problems, not just solving them.** When buyers can solve stated
  problems themselves, the value moves upstream to identifying the problem they had not
  articulated. Curation, framing and a good question outrank a fast answer.
- **Frames change decisions.** Fewer options decide more often than many. Experience sells
  better than a possession. A small blemish honestly disclosed can make the strengths more
  believable.
- **Give people an off-ramp.** Pink's "make it personal and make it purposeful": persuasion
  works better when there is a clear, easy path to act on it and a reason that is bigger
  than the transaction.
- **Servant selling is the frame that holds.** Are the people better off for having met
  you, whether or not they bought? If not, the rest of the techniques are decoration.

## When Baker reaches for it

- **Morale is the problem, not the pipeline** — a run of losses and the user is reading it
  as a verdict on themselves.
- **After a big loss** — how the loss gets explained determines what next week looks like.
- **The buyer has done all the research already** — they arrive knowing the market, and the
  old value proposition has nothing to add.
- **Nobody can name the real problem** — the deal is full of requirements and empty of
  purpose.
- **Too many options are on the table** — a proposal with five paths and no recommendation.
- **The user does not consider themselves a salesperson** — a founder, a technical lead, or
  anyone persuading without the title.

## Applied to the CRM

- **Put the losses in proportion with real numbers** — `crm_analytics` shows wins against
  losses and the sizes involved. A bad fortnight looks different next to a year of
  outcomes, and that is buoyancy done with evidence rather than encouragement.
- **Record the loss reason as temporary, specific and external where it honestly is** —
  `crm_mark_deal_lost` with the real reason is both better data and a better story than
  leaving it blank and remembering it as a personal failure.
- **Turn problem-finding into a question you actually ask** — write the one question you
  would ask to surface an unarticulated problem on the deal with `crm_add_note`, and book
  the conversation as a task (`crm_create_task`; `todo_create` in GTD task mode).
- **Attunement starts with what is already recorded** — `crm_get_contact` and
  `crm_get_company` tell you the constraints the person is working under before you guess
  at them.
- **Find the deals that have gone quiet and treat it as information** —
  `crm_get_stale_deals` shows where attention lapsed, which is a process fact, never a
  judgement about the person working them.

## Go read it

Daniel H. Pink, *To Sell Is Human* (Riverhead, 2012). Read it when selling feels like
something distasteful you have to do — the book's reframing of the whole activity is the
part that lasts, and the research on rejection is genuinely useful on a bad week.
