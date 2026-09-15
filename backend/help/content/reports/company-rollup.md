---
title: The company report
description: One page with everything the CRM knows about one company, plus where the other numbers come from.
aliases: reports, report, company rollup, account, dashboard, analytics, weekly touches, totals
admin: false
---
## What the Reports page is

**One report.** Pick a company and get everything on one scroll. There are no other report
types — no forecasting, no cohort analysis, no custom report builder. If someone asks for one
of those, the answer is that this product does not have it.

Read-only: nothing on the page writes.

## What it shows

- **Headline figures** — open deal count, open deal value, and active contacts. These are
  computed over the whole company, never by adding up the lists below, so expanding or
  filtering the page can never move a headline number.
- **Every linked contact**, including inactive and archived ones, which are marked as such.
  Note the "Active contacts" figure counts only active ones, so it is deliberately smaller
  than the list.
- **Every linked deal**, each expandable in place to show its activity, custom fields and
  open tasks.
- **A merged timeline** of notes and activity in one feed.

## Archived is opt-in

A toggle includes archived deals and archived notes. It does not affect contacts, which have
a status rather than an archive flag and are always listed.

## Mixed currencies

If the open deals do not all share one currency, the value chip says **Mixed currencies**
instead of a total. Adding different currencies together produces a number that is simply
false, and this page declines to. The rest of the app does sum and prefix a single symbol,
which is a convention rather than a guarantee.

## Where an activity is counted

An activity that names a deal belongs to that deal, always — even if the deal is archived or
belongs to a different company. It never also appears under the contact. Every activity has
exactly one home, so nothing is double counted and nothing disappears.

## Caps

Long lists are capped, and the page says when it has truncated one rather than quietly
serving a prefix.

## The dashboard, for comparison

- A **Today panel**: one ranked list of what needs you now — starred todos, hot deals that
  have gone quiet, overdue items, and things due today. Plain database work, no AI.
- Four always-present counts: contacts, companies, pipeline value and overdue tasks.
- A snapshot: win rate, average days to close, average won deal size, open deals.
- **Weekly touches** per person — how many of their open deals were touched inside a calendar
  week — with a drill-down page per person. A touch is an edit, a logged activity or a live
  note. The per-deal number beside it is the AI touch estimate, so that column is empty with
  no provider configured while the card itself still works.
- The five largest open deals and the last ten activities.

## The two analytics tools

`crm_analytics` answers outcome questions: wins, losses, deal sizes, activity volume, stale
deals, per-person pipeline. `crm_get_pipeline_analytics` answers movement questions: time in
stage, conversion between stages, how long deals took to win. They are complementary, not
alternatives. See `pipeline/deal-health-and-scores` for the caveat about how far back the
stage history really goes.

## Notes and activity

Two different things, and the distinction matters when advising someone:

- A **note** is standing commentary on a record. It can be edited and archived, it threads,
  and it takes attachments up to 10 MB each and ten per note. Notes attach to deals, contacts
  and companies.
- An **activity** is a dated interaction — a call, an email, a meeting, a follow-up. It is
  history and is not edited afterwards. Activities attach to contacts and deals, not to
  companies.

Baker uses `crm_add_note` for the first and `crm_log_activity` for the second.
