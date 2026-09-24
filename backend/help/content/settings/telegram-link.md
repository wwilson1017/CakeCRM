---
title: Link my Telegram
description: Connect your own Telegram account to the assistant so you can chat with it from your phone.
aliases: link telegram, my telegram, link my phone, link code, unlink, personal telegram
admin: false
---
## Who can do this

Anyone with an account. The link is **yours** — it connects Telegram to your account and to
nobody else's, and each person on the team links their own phone separately.

Connecting the bot itself is a different job and is admin only. See the Telegram topic under
Integrations for that.

## Linking your phone

Settings, Personal, **Link my Telegram**.

If no bot has been connected for the workspace yet, the card says so and there is nothing to
do here until an administrator adds one.

Once a bot is connected:

1. Press **Get my link code**. That mints a single-use code for your account.
2. Press **Open Telegram to link**, which starts a private chat with the bot carrying the
   code. If you are already in that chat, send `/link` followed by your code instead.
3. The code links the first device that uses it, then expires. **Regenerate code** issues a
   fresh one, which releases the device currently linked — that is how you move to a new
   phone.

## After you are linked

The chat is a full conversation with the assistant. It sees the same CRM and the same tools
as the drawer in the web app, and anything it changes is recorded as **you**, the same as if
you had done it here.

When it wants to make a change, the message arrives with two inline buttons, Approve and
Deny. Only your linked account can press yours.

In GTD todo mode, a message beginning with `capture ` (or `/capture `) creates an inbox todo
directly, without the model running at all — so it is instant and works even when the
workspace has no AI provider configured.

## Unlinking

**Unlink my Telegram** on the same card ends your link. It does not affect anyone else's,
and it does not disconnect the bot. You can link again later with a new code.

## Who sees what

Your link code and link address are visible only to you. Everyone can see whether a bot is
connected for the workspace and what its username is; nobody can see or claim your code.
