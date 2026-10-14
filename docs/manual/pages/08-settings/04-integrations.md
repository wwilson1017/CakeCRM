---
title: Integrations settings
summary: Admin-only cards for connecting the team's Telegram bot and a Gmail mailbox.
shots: [settings-integrations]
---

The **Integrations** tab is for admins only. Members do not see it. It holds two cards.

{{shot:settings-integrations}}

## Telegram

Connect one Telegram bot for the whole team, so people can chat with Baker from their phones. Create a bot with @BotFather in Telegram, paste its token into **Bot token** and press **Connect bot**. The token is checked straight away and stored encrypted. It is never shown again.

After that, each person links their own phone from **Settings → Personal → Link my Telegram**. Connecting a different bot later resets everyone's links.

Full steps: [Telegram](../09-integrations/02-telegram.md).

## Gmail

Connect one Gmail mailbox, using your own Google OAuth app. The card shows the **Redirect URI** to add to your Google OAuth client. Paste the **Client ID** and **Client secret**, press **Save app credentials**, then connect. Baker can then search and read mail and create drafts. **Baker never sends email.** A draft always waits in your Gmail drafts folder for you to review and send yourself.

By default only admins' Baker can use the mailbox. The **Share this mailbox with all seats** box gives every member the same access. It switches itself off whenever the connection changes.

Full steps: [Gmail](../09-integrations/01-gmail.md).

## Without AI keys

Both cards can be set up before any AI provider is connected. In GTD mode, Telegram's `capture` message works with no AI at all. Chatting with Baker, in Telegram or about mail, needs a provider. See [AI setup](../07-baker/06-ai-setup.md).
