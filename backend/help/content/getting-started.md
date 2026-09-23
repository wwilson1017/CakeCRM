---
title: Getting started
description: What a fresh install gives you, and the shortest path to a useful CRM with no AI keys at all.
aliases: setup, first run, new install, onboarding, sample data, demo data, tour, what is this
admin: false
---
## What this product is

A free, self-hostable CRM with a built-in AI assistant called Baker. The CRM is the
product; Baker is a feature of it. Every part of the CRM works with no AI provider
configured — AI features hide themselves rather than showing errors.

## What a fresh install needs

- **A PostgreSQL database.** The backend refuses to start without one. Locally it comes
  from the bundled container setup; on a one-click cloud deploy the template provisions it.
- **A first admin account.** On the very first boot the server creates one admin from the
  email and name in the environment, with a starting password also from the environment.
  That starting password stops mattering the moment the admin changes their password in
  Settings.
- **Nothing else.** No Redis, no object store, no mail server. AI keys are optional and are
  entered in the app, never as environment variables.

## The first ten minutes

1. Sign in as the admin the install created.
2. If the CRM is empty you are offered sample data — fictional contacts, deals and tasks so
   you can see how everything behaves. While it is loaded a banner stays on screen with a
   Clear action, so there is no way to confuse examples with real records.
3. Walk the navigation: Dashboard, Pipeline, Contacts, Companies, Todos, Reports.
4. Create a contact, create a deal linked to it, log an activity against the deal, and add a
   follow-up todo. That loop is the whole CRM in miniature.

## Where things live

- **Dashboard** — a Today panel ranking what needs you now, four headline counts, weekly
  touches per person, and recent activity.
- **Pipeline** — the deal board, by stage, with a list view and filters. See
  `pipeline/stages`.
- **Contacts** and **Companies** — searchable lists with detail pages that roll up linked
  records, notes and activity.
- **Todos** — your follow-ups. Todos are the one follow-up primitive in this product. See
  `tasks/modes`.
- **Reports** — one report: everything known about a single company. See
  `reports/company-rollup`.
- **Settings** — four sections: Personal, Assistant, Workspace and Integrations. Members
  see Personal and Assistant only; the other two are admin-only and do not render at all
  for a member.

## Working with zero AI keys

Everything above works untouched. What is hidden rather than broken without a provider:
Baker's chat itself, assisted import of file formats beyond CSV and vCard, the estimated
touch-count pill on deal cards, and AI provenance badges. Lead scores, duplicate scans,
staleness reads, analytics and every report are plain database work and always available.

A dismissible banner offers to add a key. It is a prompt, never a gate, and dismissing it
is remembered.

## Adding AI

An admin connects a provider and a model. See `settings/ai-providers`. Once a key is
connected the assistant launcher opens a chat drawer instead of pointing at setup.
