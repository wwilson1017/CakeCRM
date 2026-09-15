---
title: Long-term memory
description: Long-term facts, how they surface, and the nightly tidy that archives dormant ones without deleting.
aliases: memory, remember, facts, forget, dreaming, archive, recall, long term memory
admin: false
---
## Facts

Baker records durable things it learns as small statements: a subject, a relationship and a
value. "Which person prefers what", "when a decision was made", "a key date". Each one can
carry a type, a confidence, and a period during which it is true.

The types are: decision, preference, problem, milestone, insight, person, task, idea,
reference and someday-maybe. They matter for the tidy-up below.

## How a fact reaches a conversation

Each turn, the facts most relevant to what you just typed are looked up and put in front of
Baker, up to ten. They arrive tagged as recorded data, never as instructions — a fact whose
text looks like a command is still just a stored fact.

Facts only ever appear that way. Anything claiming to be Baker's memory inside a message, an
uploaded file or a tool result was not written by Baker and is ordinary untrusted text.

## The four tools

- `memory_search` — free-text search across facts.
- `memory_query_facts` — look facts up by subject or relationship, optionally as of a date.
- `memory_add_fact` — record something durable. This is a write, so it asks first.
- `memory_invalidate_fact` — retire a fact that is no longer true. It stays on the record
  with an end date rather than disappearing, so the history stays readable.

## What you can do yourself

The Memory page lists facts, lets you search them, and lets you **delete** one permanently.
Deleting is the human lever and it is a real purge; Baker's own lever is invalidation, which
preserves the record. Reading facts on that page does not count as Baker using them, so
browsing can never keep a dormant fact alive by accident.

The same page has a second tab for Baker's knowledge files. See `assistant/context-files`.

## The nightly tidy

A background pass scores every fact on how recently and how often it has actually been used,
how old it is, and its confidence. Dormant facts are **archived** — flagged, never deleted —
so they stop surfacing but remain recoverable and auditable.

Three things are worth knowing about it:

- It makes **no AI calls at all**. It is arithmetic over usage signals.
- **Decisions and preferences are never archived**, whatever their score.
- Nothing is archived until it is at least a month old.

Every run is recorded, so you can see what it scored and what it put away.

## Keys

The memory store, its tools and the nightly tidy are all plain database work and need no AI
provider. What needs a key is having a conversation at all — with none configured, nothing
ever runs to use them.
