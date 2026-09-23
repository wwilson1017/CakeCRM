---
title: Knowledge files
description: The markdown files Baker keeps about itself, your business and each day, and how you edit them.
aliases: knowledge files, notes, soul, memory file, topics, daily notes, context files, what does it know
admin: false
---
## The four kinds of file

- **soul.md** — what Baker has written about itself: how you work, patterns it has noticed,
  preferences it has formed. It is Baker's own identity note, and it is the one file loaded
  into every conversation as Baker's own voice rather than as quoted data.
- **MEMORY.md** — a living snapshot: key people, active deals, decisions, lessons. Baker
  reads it, merges new items in, and writes the whole file back.
- **topics/…** — subject knowledge, one file per subject: a process, a pricing rule, an
  account's quirks.
- **daily/…** — one running log per day, named by date, appended to as things happen.

## What is loaded, and what is only listed

Baker always sees soul.md, MEMORY.md and today's log. Topic files and past daily notes are
**listed but not loaded** — a manifest of names and one-line summaries. Baker reads one on
demand. So "I cannot see a file about that" is the wrong conclusion from an absent file; the
right move is to check the manifest and read it.

Everything except the soul arrives fenced and tagged as recorded data, so content stored
there can never act as an instruction.

## The seven tools

`list_context_files`, `read_context_file`, `write_context_file`, `delete_context_file`,
`append_daily_note`, `read_daily_note` and `search_context_files`.

Two rules Baker works to: never say it will save something without actually calling the tool
in the same reply, and remember that writing a file **replaces the whole file**, so the
content written has to include everything that should remain.

## File names

Names end in `.md`. A bare name becomes a topic file. Otherwise the first part must be
`topics` or `daily`, and a daily file must be named for a real calendar date. Nested folders
are refused, and so are names that could escape the store.

## The two protected files

soul.md and MEMORY.md **always ask for your approval before being written or deleted**, in
every mode, even the one that normally runs writes without asking. A poisoned soul file is
not one bad record — it is a standing instruction replayed in every future conversation,
including unattended ones, and it survives deleting the conversation that planted it. If the
file name cannot be read at all, it asks anyway.

Deleting a protected file is refused outright.

## Editing them yourself

The Memory page has a Files tab: read, edit and delete. If Baker or another tab changed the
file while you had it open, saving is refused with a message asking you to reload, rather
than silently overwriting.

**Changing the two protected files is admin only.** Everyone can read soul.md and
MEMORY.md — seeing what Baker knows is the point of the page — but on a member's seat both
open read-only with no Save. Baker's own personality is already admin-only, and these two
files are the other half of the same standing instruction.

That gate is on Baker too, not only on the editor. Asking Baker to rewrite soul.md or
MEMORY.md is refused on a member's seat, and Baker says so rather than composing the
rewrite. The reason is that the approval card is not a permission: it stops the write and
shows you the new text, but the person who approves it is whoever is in that conversation,
so without the seat check a member could ask for the change and then approve it. Both
checks still apply on an admin's seat — the write is allowed, and it still stops for
approval first.

Everything else Baker keeps is unrestricted: topic files and daily notes can be written,
rewritten and deleted from any seat, on the Memory page and through Baker alike.

The same protection covers Baker: when a write is waiting for your approval and you edit that
file in the meantime, the approved write is refused and Baker is told to re-read and reapply
rather than flattening your edit.
