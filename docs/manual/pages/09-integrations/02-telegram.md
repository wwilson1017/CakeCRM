---
title: Telegram
summary: Chat with Baker from your phone. An admin connects one bot for the workspace, then each person links their own chat.
shots: [settings-integrations]
---

Telegram lets you ask Baker about a deal, log a call or capture a todo from your phone. It has two halves: an admin connects one **bot** for the whole workspace, and then each person **links** their own Telegram account to it.

Telegram is a way to reach Baker, so it needs an AI provider connected, apart from quick capture below.

## Connecting the bot (admins)

{{shot:settings-integrations}}

1. In Telegram, talk to **@BotFather** and create a bot. It gives you a bot token.
2. In CakeCRM, open **Settings**, **Integrations**, paste the token into **Bot token** on the Telegram card, and click **Connect bot**. A bad token is refused.
3. Tell your team to link their phones.

The token is stored encrypted and never shown again. Connecting a different bot later resets everyone's link, and each person links again.

## Linking your phone (everyone)

Go to **Settings**, **Personal**, **Link my Telegram**. If no bot is connected yet, the card says so.

1. Click **Get my link code**.
2. Click **Open Telegram to link**, which opens a private chat with the bot carrying your code. Or send `/link` followed by your code in that chat.
3. The code links the first device that uses it, then expires. **Regenerate code** issues a new one and releases your current device, which is how you move to a new phone.

**Unlink my Telegram** ends your link without affecting anyone else. See [Personal settings](../08-settings/01-personal.md).

## Chatting with Baker

Each linked chat is a full conversation with Baker. It sees the same CRM and tools as the drawer in the app, and anything it changes is recorded as you. Telegram chats run in Ask mode: when Baker wants to make a change that needs approval, the message arrives with **Approve** and **Deny** buttons, and only your account can press them. See [Approvals](../07-baker/03-approvals.md).

When Baker mentions a deal it looked at, the reply links to that deal on the pipeline board.

## Quick capture

In GTD todo mode, a message starting with `capture ` (or `/capture `) goes straight into your todo inbox without Baker running. It is instant, costs nothing, and works with no AI provider connected. See [Todo modes](../05-todos/01-todo-modes.md).

## What is not supported

- Group chats. Baker only answers in a private chat.
- More than one bot per workspace.
