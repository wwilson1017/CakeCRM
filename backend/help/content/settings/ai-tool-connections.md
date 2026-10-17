---
title: AI tool connections
description: Decide whether members may connect their own AI tools, and see or revoke every connection on the team.
aliases: ai tool connections, mcp connections, members may connect, connected ai tools, revoke connection, ai access
admin: true
---
## Who can do this

Admins only.

## Letting members connect

Settings, Workspace, **AI tool connections**.

**Members may connect their own AI tools** is off until you turn it on. Admins can always
connect, whatever it says. Turning it off stops every member's connection on its next call
without deleting it, so turning it back on restores them as they were.

## Every connection on the team

The card lists every person's connected tools: the tool's name, whether it uses a personal
token or signed in, whether it is read-only, when it was connected and when it was last used.
**Calls** shows a connection's log for the last 90 days (tool, record or row count, result and
time taken; never what it asked or got back). **Revoke** ends a connection at once.

## Why ChatGPT and Claude need a public address

ChatGPT and Claude call CakeCRM from their own servers and sign people in through CakeCRM's own
sign-in page, so the install needs a public https address for them to reach it. Without one,
people can still connect coding tools with a personal token.
