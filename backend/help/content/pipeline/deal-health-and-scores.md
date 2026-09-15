---
title: Deal health, scores and temperature
description: What the number on a deal card means, where it comes from, and what health flags exist.
aliases: lead score, deal score, deal health, scoring, scored, temperature, hot, warm, cold, touch count, stale, funnel
admin: false
---
## The lead score

A number from 0 to 100 on every deal and every contact. It is **pure arithmetic — no AI is
involved** and it works on an install with no keys at all.

For a deal it multiplies a baseline for the current stage by several factors:

- **engagement** — how much has been logged against it.
- **value** — the size of the deal.
- **relationship** — whether it is linked to a contact and a company.
- **recency** — how long since the last touch.
- **age** — how long since it was created.
- **temperature** — your own read on it, below.

An open deal is clamped to between 1 and 99. A won deal is exactly 100 and a lost one exactly
0. Nobody can write the score directly — not you, not Baker. It is recomputed when a deal
changes and refreshed in small batches daily.

The score alone tells you nothing you can act on. Lead with the flags.

## Deal health

`crm_get_deal_health` returns the score, the factors behind it, and a list of flags. The
flags are exactly these, and no others:

- **stale** — nothing has touched it for the staleness window, 14 days by default.
- **stuck in stage** — it has sat in the same stage for 30 days.
- **no next step** — it has no open task.
- **overdue task** — it has a task past its due date.
- **missing contact** — no contact is linked.
- **missing company** — no company is linked.

An archived deal still answers, marked as archived, so you can look before deciding to
restore it.

## Temperature is your judgement, not a calculation

A separate, human-set field with three values — **hot**, **warm** and **cold** — plus "not
set", which is the honest state of a deal nobody has triaged. Not set is neutral: it is not
the same as cold and it does not push the score down.

Click the icon on a card or in the detail panel to cycle it: not set, hot, warm, cold, back
to not set. Each state has its own shape as well as its own colour. Hot deals that have also
gone quiet get their own slot in the dashboard's Today panel.

## The touch-count pill

An estimate of how many real touches a deal has had, read from its notes and activities by a
cheap AI model. Under 5 is the dead zone, 5 to 12 is roughly where deals close, 13 and up is
a long cycle.

**This one needs an AI provider.** With no key nothing is computed and the pill is simply not
drawn — there is no error and no empty badge. Where a count exists, opening the deal shows
the line-by-line judgement behind it, and says plainly when that explanation has gone stale
relative to the number.

## Funnel analytics and the history caveat

`crm_get_pipeline_analytics` reads the stage-change log for time in stage, conversion between
stages, and how long won deals took. `crm_analytics` covers outcomes instead: win and loss
counts, deal sizes, activity volume, and a per-person pipeline breakdown.

The stage-change log only started when that feature shipped, so the funnel can cover less
ground than the window you asked for. The response carries the earliest date it really has
and a flag saying whether that covers your window. When it does not, say how far back the
data actually goes rather than presenting a partial funnel as the whole picture.

Stage durations count only intervals a deal has actually finished. A deal still sitting in a
stage is left out rather than counted as if it had left today.
