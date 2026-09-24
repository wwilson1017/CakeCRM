---
title: Telegram
description: Connect the workspace's Telegram bot, so the team can chat with Baker from their phones.
aliases: telegram, bot, botfather, bot token, messaging, integration
admin: true
---
## Who can do this

Admin only, and this is the **bot** half: one bot serves the whole workspace. Linking your
own phone to it is personal and every account does that for itself — see "Link my Telegram"
under Personal.

## Setting it up

1. In Telegram, talk to **@BotFather** and create a bot. It hands you a bot token.
2. Open Settings, Integrations, Telegram, paste the token and save. The token is validated
   live — a bad one is refused with a message telling you to make one with BotFather. It is
   stored encrypted and is never returned by the API afterwards.
3. Tell your team to link their phones from Settings, Personal, Link my Telegram.

Connecting a **different** bot resets every existing link: a chat id belongs to one bot, so
every binding made against the old one names a chat the new bot cannot reach. Everyone
re-links from their own card.

## Who sees what

Any signed-in user can see whether a bot is connected and what its username is. The **bot
token is never shown again** after it is saved, and a person's link code is visible only to
that person — nobody, admin included, can see or claim somebody else's.

## What it can do

Each linked chat is a full conversation with Baker. It sees the same CRM, the same memory
and the same tools as the web drawer, and what it writes is recorded as the person whose
chat it is.

When Baker wants to make a change, the message comes with two inline buttons — Approve and
Deny. Only the account that owns that chat can press them. Baker waits until every pending
change in the batch is answered, then carries on.

## Capture

In GTD todo mode, a message beginning with `capture ` (or `/capture `) is intercepted before
the model runs: it creates an inbox todo directly. That costs nothing, is instant, and works
with no AI provider configured at all.

## What is deliberately not supported

- **No group chats.** Baker refuses to link from a group and only answers in a private chat.
- **No webhooks.** The integration polls; connecting removes any webhook already set on that
  bot so polling can work.
- **One bot per workspace.** Each person links their own chat to it; there is no second
  bot, and no way to point one account at a different one.
