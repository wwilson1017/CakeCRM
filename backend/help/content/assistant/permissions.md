---
title: What the assistant can and cannot do
description: The honest boundary — what it may touch, what it may never touch, and what it does unattended.
aliases: permissions, capabilities, what can baker do, limits, safety, background, uploads, roles
admin: false
---
## The short version

Baker can read and change CRM records, work your todos, keep its own memory and knowledge
files, search and read your mail, and compose drafts. It cannot deliver mail, cannot change
install settings, and cannot act outside this product.

## Mail

Read and compose-a-draft only, forever. No tool in this product transmits mail and none may
be added — the limit is enforced in code and checked by a build-time test, and it is written
down as a trust guarantee. A draft always comes back for you to review and deliver yourself.
See `settings/gmail`.

## Settings

Baker has **no settings tools at all**. It cannot add an AI key, connect a mailbox, switch
task mode, define a custom field, add a user or change branding. Keys and OAuth secrets must
never flow through a chat transcript, and a wrong settings change is install-wide where a
wrong record change is one record. Baker explains and points; you click.

That means Baker can tell you a flow is admin-only and walk you through it, and should say so
up front rather than sending a member into a screen they cannot see.

## Roles

Two: admin and member. Both get Baker. The role governs install configuration, not records —
see `settings/team`. The server enforces it either way; Baker knowing the difference only
makes its advice correct, never more permissive.

## Unattended turns

Baker also runs on a schedule with nobody watching, for the daily digest and nudges. There
the rules are much tighter and enforced by the server, not by instructions:

- **Read tools plus a single notification, and nothing else.** No record changes at all,
  whether or not anything asks for them.
- **Live mail reads are excluded too**, even though they are reads, because an unattended
  turn has nobody to notice a fence.
- One notification per run.

So the very worst a hostile instruction hidden in a note or a record can achieve unattended is
one notification. It cannot create, log, update or delete anything.

Mail that the background scan already wrote into the activity log is ordinary CRM data and
stays readable — the accurate statement is that an unattended turn cannot start a mail search
or open a thread, not that it can see nothing mail-derived.

## Uploads

Up to 5 files per message, 10 MB each, from this list: CSV, XLSX, MD, TXT, PDF and DOCX. Only
the text is extracted — the original bytes are never stored — and it is wrapped as untrusted
content, which tightens the confirmation rules for the rest of that conversation. Baker has no
way to upload a file to a record; note attachments are added by you.

## Yours, and the team's

Your conversation history with me is yours alone — nobody else can read it, admins included.
So are your notifications and your Telegram link.

What the whole team shares is my **memory**: the facts I record and the context files I keep
are one shared brain, on purpose. Say that plainly when it matters to someone, because the
cost is real — something you tell me in a private conversation can become a fact every seat
can see. Ask me not to remember it if it should not be shared.

There is one mail connection per install, whichever account an admin connected, and by
default only **admin seats** can have me search it or draft from it. On a member's seat I am
not given the mail tools at all, so I cannot read that mailbox even if asked. An admin can
turn on "share the connected mailbox with all seats" in Settings, Integrations, Gmail, for a
team that genuinely works one shared inbox. When I run unattended — the heartbeat, the daily
digest — I never touch the mailbox either way. See `settings/gmail`.
