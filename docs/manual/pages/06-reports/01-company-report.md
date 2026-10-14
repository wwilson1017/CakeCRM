---
title: The company report
summary: One page with everything CakeCRM knows about one company: its deals, its contacts, and every note and activity across the account.
shots: [reports]
---

**Reports** has one report: pick a company and get everything known about it on a single scroll. There are no other report types, no forecasting and no custom report builder. The page is read-only; nothing on it changes your data.

## Building a report

1. Open **Reports**.
2. In the **Company** box, start typing a company name and pick it from the list.

The report appears below. The page address includes the company, so you can bookmark a report or send the link to a teammate.

{{shot:reports}}

## What it shows

**Headline figures.** Three chips at the top: **Open deals**, **Open value** and **Active contacts**. They are counted over the whole company, never by adding up the lists below, so expanding rows or including archived records never moves them.

**Company details.** Industry, domain, phone, address, source, owner and dates, the company's own notes and its custom fields.

**Deals.** Every deal linked to the company, with its stage, value, touch count (with an AI provider) and lead score. Click a deal to expand it in place and see its details, custom fields, activity, notes and open todos.

**Contacts.** Every contact linked to the company, including inactive and archived ones, which are marked. Click a contact to expand it. The Active contacts chip counts only active ones, so it can be smaller than this list.

**Timeline.** Notes and activity from across the account in one feed, with **Load more** at the bottom.

**Expand all** and **Collapse all** beside each section heading open or close every row at once. Expand all is not offered when a section is very long.

## Archived deals and notes

Archived deals and archived notes are left out unless you tick **Include archived deals and archived notes**. Contacts are always listed; they have a status rather than an archive flag.

## Mixed currencies

If the open deals do not all share one currency, the Open value chip reads **Mixed currencies** instead of a total. Adding different currencies together gives a number that is simply wrong, so this page declines to.

## Where an activity is counted

An activity logged against a deal belongs to that deal, always, even if the deal is archived or belongs to a different company. It never also appears under the contact. Every activity has exactly one home, so nothing is counted twice.

## Long lists

Long lists are capped. When a section has been cut short, the report says so rather than quietly showing only part of it.

## Other numbers

The dashboard holds the pipeline-wide figures: win rate, average days to close, deal aging and per-person activity. See [The dashboard](../02-dashboard/01-dashboard.md). With an AI provider connected, you can also ask Baker questions about wins, losses and how long deals spend in each stage.
