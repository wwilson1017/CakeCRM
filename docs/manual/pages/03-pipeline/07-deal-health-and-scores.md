---
title: Deal health and scores
summary: What the lead score, temperature and touch count on a deal mean, and which of them need AI.
shots: []
---

A deal card carries up to three signals. Two of them are plain arithmetic or your own judgement and always work; one needs an AI provider.

## The lead score

A number from 0 to 100 on every deal (and every contact). It is pure arithmetic with no AI involved. For a deal it starts from a baseline for the current stage and adjusts it for:

- **engagement**: how much has been logged against it;
- **value**: the size of the deal;
- **relationship**: whether it is linked to a contact and a company;
- **recency**: how long since the last touch;
- **age**: how long since it was created;
- **temperature**: your own read on it, below.

An open deal always scores between 1 and 99. A won deal is exactly 100 and a lost one exactly 0. Nobody can type a score in, and neither can Baker. It is recalculated when the deal changes and refreshed daily.

On its own the score tells you little you can act on. Use it to sort, then look at what the deal needs.

## Temperature

Your own judgement, with three values: **hot**, **warm** and **cold**, plus not set. Not set is neutral: it is not the same as cold and does not lower the score.

Click the icon on a card, or the Temperature row on the deal page, to cycle it: not set, hot, warm, cold, back to not set. Each state has its own shape as well as its own colour.

Hot deals that have gone quiet for 14 days or more get their own place on the dashboard's [Today panel](../02-dashboard/02-today-panel.md).

## Deal health flags

Baker can check a deal's health and report these flags, and only these:

| Flag | Meaning |
|---|---|
| stale | Nothing has touched it for 14 days |
| stuck in stage | It has sat in the same stage for 30 days |
| no next step | It has no open todo |
| overdue todo | It has a todo past its due date |
| missing contact | No contact is linked |
| missing company | No company is linked |

The dashboard's **Needs a touch** list shows the stale deals without asking Baker.

## The touch-count pill (needs AI)

An estimate of how many real touches a deal has had, read from its notes and activities by an AI model. Under 5 is the dead zone, 5 to 12 is roughly where deals close, 13 and up is a long cycle.

With no AI provider the pill is simply not drawn; there is no error and no empty badge. Where a count exists, the deal page lets you open the line-by-line reasons behind it.

## How Top deals are picked

The dashboard's **Top deals** shows five open deals past the lead stage, ranked by size with probability tipping the order: a deal counts at 40% of its value at 0% probability, rising to its full value at 100%. This is separate from the lead score. If the list is empty while the pipeline is not, every open deal is still a lead.
