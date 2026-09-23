---
title: Fanatical Prospecting
description: Blount's prospecting discipline — protect the daily block, work every channel, obey the 30-day rule, and keep the pipeline full so no single deal can hold you hostage.
aliases: Blount, prospecting, prospecting cadence, 30-day rule, thin pipeline, pipeline discipline, cold call, outreach, time blocking, weekly touches, law of need, empty pipeline
admin: false
author: Jeb Blount
---
## Core ideas

- **The pipeline is the job.** Blount's central claim is unglamorous: consistent, daily
  prospecting outperforms every clever technique, and most underperformance traces back to
  an empty pipeline rather than to weak closing.
- **The 30-day rule.** The work you do this month fills the pipeline for the next ninety
  days. Skip thirty days of prospecting and you create a hole that shows up a quarter
  later, long after you have forgotten what caused it.
- **The law of need — desperation repels.** A rep with a thin pipeline negotiates badly,
  discounts early, chases unqualified deals and sounds needy on the phone. A full pipeline
  is what buys the composure to walk away, which is what makes the deals close.
- **Time-block prospecting and defend the block.** Prospecting only happens if it is
  scheduled and protected from everything that feels more urgent. Interruptions do not just
  cost the minutes they take, they cost the rhythm.
- **Work every channel, and stop arguing about which one is dead.** The phone, email,
  social, referrals, networking, the occasional visit. The mix varies; the balance is the
  point. Anybody insisting that one channel is finished is usually avoiding it.
- **Rejection is the toll, not the verdict.** Most people will say no, and the numbers only
  work at volume. Blount is direct that the emotional work — expecting the no, not taking
  it personally, making the next call anyway — is the real skill.
- **Get the call opener right and then get out of the way.** Say who you are, why you are
  calling, ask for what you want. Long warm-ups make the call worse, not softer.
- **Referrals and existing relationships are the cheapest pipeline.** Current customers and
  past contacts convert far better than strangers and are routinely neglected in favour of
  cold outreach that feels more like work.
- **Quality of the list beats quantity of the effort.** Volume against a badly chosen list
  produces activity metrics and no pipeline. Research enough to know why this person, this
  month.
- **Track what you actually do.** Not to feel productive — to notice the week you stopped,
  before the quarter notices for you.

## When Baker reaches for it

- **The pipeline is thin** — few deals in *lead* and *qualified*, and the user is asking
  why nothing is closing.
- **Weekly touches have dropped** — the touch and activity counts show a quiet week or
  month, which is the leading indicator the 30-day rule is about.
- **Deals are going stale in bulk** — many deals carrying the *stale* flag at once is a
  cadence problem, not a deal problem.
- **The user is discounting to rescue a deal** — the law of need, visible in the CRM.
- **Existing customers are untouched** — won deals with no recent activity are the pipeline
  nobody is working.
- **The user is avoiding the phone** — the channel argument usually resolves into whichever
  one they dislike.

## Applied to the CRM

- **Let the CRM name the neglected list** — `crm_get_stale_deals` and
  `crm_get_contact_staleness` produce today's prospecting list from real silence, which
  beats any list assembled from memory.
- **Put the block in the system, not in your intentions** — in GTD task mode
  `todo_create` takes a repeat, so the daily prospecting block can be created once and
  recur. `crm_create_task` has no recurrence, so outside GTD mode it is one task at a
  time: create tomorrow's when you close today's, and treat the slot as unmovable.
- **Draft the outreach, review it, then put it out yourself** — `gmail_create_draft`
  prepares an email touch for the user to check; Baker drafts and never delivers.
- **Log the attempts, including the ones that went nowhere** — `crm_log_activity` on the
  contact is what makes a cadence visible. Untracked outreach is indistinguishable from no
  outreach a month later.
- **Watch the top of the funnel, not just the bottom** — to see whether new business is
  arriving, list the newest deals in *lead* with `crm_search_deals` sorted by creation
  date. Do not read that off `crm_get_pipeline_analytics`: its "entered" count is stage
  transitions, and a deal created straight into *lead* never transitions into it, so the
  number stays near zero however much prospecting is done. `crm_analytics` is the one that
  shows activity volume alongside outcomes, which is where the 30-day rule shows up.
- **Work the customers you already won** — `crm_search_deals` with the stage set to won
  lists them; `crm_get_company` then gives one account's contacts, deals and recent
  activity in a single read, which is where the silence since signing shows up. The
  activity log filters by contact or deal, not by company, so go through the company
  rollup rather than asking it for an account.

## Go read it

Jeb Blount, *Fanatical Prospecting* (Wiley, 2015). Read it when the pipeline is thin and
the temptation is to look for a better technique — the book's uncomfortable answer is that
the calendar, not the script, is the problem.
