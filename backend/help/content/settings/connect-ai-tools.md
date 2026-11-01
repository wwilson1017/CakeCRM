---
title: Connect an AI tool
description: Let your own ChatGPT, Claude, Claude Code, Codex or another AI tool work in CakeCRM as you, with a personal token or by signing in.
aliases: connect ai, mcp, personal token, claude code, codex, chatgpt connector, claude connector, cursor, revoke token, read only connection, ai tool
admin: false
---
## What this is

You can connect an AI tool you already use (ChatGPT, Claude, Claude Code, Codex, Gemini CLI,
Cursor or VS Code) to CakeCRM. It then works in the CRM **as you**: it sees what you can see,
and anything it adds or changes is recorded as you, made through that tool. CakeCRM needs no
AI key of its own for this, because the tool brings its own.

## Who can do this

Admins always can. Everyone else can once an admin has switched on **Members may connect their
own AI tools** under Settings, Workspace, **AI tool connections**. Until then the card says so
and offers nothing.

## Two ways to connect

Settings, Personal, **Connect an AI tool**.

- **ChatGPT or Claude** sign in. Copy the address the card shows and add it as a connector in
  the app (ChatGPT needs developer mode switched on; in Claude it is Settings, Connectors, Add
  custom connector). A CakeCRM window opens: sign in with your password (and your two-factor
  code, if you use one), check which app is asking and where it will send you back, choose
  **Read and write** or **Read only**, then **Allow**. This needs your install to have a public
  https address; if it has none, the card says so.
- **Coding tools** use a personal token. Give it a name, choose Read and write or Read only, and
  pick when it expires: 30 days, 90 days (the default), a year, or never. The token is shown
  **once**, so copy it straight away. Then pick your tool on the card for the exact command or
  settings block, with the address and token filled in.

## What a connected tool can do

It can read everything the assistant can read about the CRM: contacts, companies, deals,
activities and notes, todos and projects, reminders, the help manual, and the assistant's notes
files and remembered facts. It can make the same changes the assistant can, so it can create
and update records, log activities, add notes and set reminders.

Some changes archive, merge, drop, change many records at once, notify people later or
recompute scores. Those are marked as needing your approval, so ChatGPT and Claude ask you
before running them. A read-only connection can never change anything.

Some things never reach a connected tool, for anyone: Gmail, permanently deleting a contact, and
any change to the assistant's notes files or remembered facts.

## Seeing what it did, and disconnecting

Every connection is listed on the card with when it was connected and last used. **Calls**
shows its log for the last 90 days: which tool it used, which record (or how many rows), whether
it worked and how long it took. The log never records what the tool asked or what it got back.

**Revoke** disconnects a tool at once. A connection also ends when its token expires, when a
signed-in app goes 90 days unused, when your password changes or your account is deactivated,
and while an admin has members switched off. An admin can see and revoke your connections too.
