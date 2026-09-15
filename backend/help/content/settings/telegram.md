---
title: Telegram
description: Talk to Baker from Telegram, approve its writes with buttons, and capture todos from your phone.
aliases: telegram, bot, botfather, phone, chat, mobile, messaging
admin: true
---
## Who can do this

Admin only. One Telegram account is linked per install.

## Setting it up

1. In Telegram, talk to **@BotFather** and create a bot. It hands you a bot token.
2. Open Settings, Integrations, Telegram, paste the token and save. The token is validated
   live — a bad one is refused with a message telling you to make one with BotFather. It is
   stored encrypted and is never returned by the API afterwards.
3. The card then shows a link. Open it in Telegram and it starts a private chat with your
   bot carrying a one-time code, which claims the link.

The link code is **single use**. Regenerate it from the card if you need a new one.

## Who sees what

Any signed-in user can see whether Telegram is connected, the bot's username, and whether
someone is linked. The **link code and link address are shown to admins only**, because that
code is what claims the one binding.

## What it can do

The linked chat is a full conversation with Baker. It sees the same CRM, the same memory
and the same tools as the web drawer.

When Baker wants to make a change, the message comes with two inline buttons — Approve and
Deny. Only the linked account can press them. Baker waits until every pending change in the
batch is answered, then carries on.

## Capture

In GTD task mode, a message beginning with `capture ` (or `/capture `) is intercepted before
the model runs: it creates an inbox todo directly. That costs nothing, is instant, and works
with no AI provider configured at all.

## What is deliberately not supported

- **No group chats.** Baker refuses to link from a group and only answers in a private chat.
- **No webhooks.** The integration polls; connecting removes any webhook already set on that
  bot so polling can work.
- **One linked person per install.** Per-user Telegram is future work.
