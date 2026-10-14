---
title: Gmail
summary: Connect one Gmail mailbox so Baker can search and read mail and write drafts. Baker can never send email.
shots: [settings-integrations]
---

## The guarantee

Baker gets exactly two abilities over your mailbox: **read** and **create a draft**. It can never send an email. No tool in CakeCRM sends mail, and none may be added: only three mail actions exist, any other Gmail operation is refused, and a test fails the build if a send ability ever appears. Every draft waits in your Gmail Drafts folder for you to review and send yourself.

## Who can do this

Setting Gmail up is **admin only**; members do not see the Integrations section of Settings.

Using it is admin only too, by default. There is one mailbox per install, and Baker offers its mail tools only on an admin's seat. If your team works one shared inbox, an admin can tick **Share this mailbox with all seats** on the Gmail card to give every member the same access. It is off by default, and it switches itself off again whenever the connection changes, so a newly connected mailbox always starts private.

## Connecting

You bring your own Google OAuth app, so your mail never passes through anyone else's project.

{{shot:settings-integrations}}

1. Open **Settings**, **Integrations**. The **Gmail** card shows a redirect URI. Copy it.
2. In the Google Cloud console, under APIs and Services, enable the **Gmail API**, create an **OAuth client (Web application)**, and add that redirect URI to it.
3. Paste the **Client ID** and **Client secret** into the card and click **Save app credentials**.
4. Click **Connect Gmail**. Google asks you to approve, then sends you back, and the card shows the connected account.

CakeCRM asks Google for two permissions only: read-only mail access, and compose access for drafts. Leave both ticked on Google's screen, or the connection is refused.

If you run CakeCRM behind your own domain or reverse proxy, set `BACKEND_URL` to your public address so the redirect URI is right. See [Deploying CakeCRM](../10-self-hosting/01-deploying.md).

## What Baker can do once connected

- Search your mail.
- Read one conversation.
- Create a draft in your Gmail Drafts folder. In Ask mode (the default) this asks for your approval first; in Auto it runs straight away unless the conversation has read mail or a file. Either way it is only a draft: Baker never sends it.

Email Baker reads is treated as untrusted text. It can never act as an instruction, and once Baker has read mail in a conversation, every change it proposes asks for approval. See [Approvals](../07-baker/03-approvals.md). Baker never runs mail searches on its own schedule; only a conversation with a person in it reaches the mailbox.

What Baker reads, and any draft it writes, is sent to your AI provider as part of the conversation. Attached files in an email are never downloaded; Baker sees only their name, type and size.

## Email touches, logged for you

While Gmail is connected, a background job reads recent inbox mail, matches senders to contacts by exact email address, and logs an email touch on that contact, so conversations nobody wrote down still count. It links the touch to a deal only when the contact has exactly one open deal. It needs no AI key. Someone who writes three times with no matching contact raises a single "create a contact?" alert.

## Expired or disconnected

If Google revokes or expires access, the card says your Gmail connection expired and offers to reconnect; your app credentials are kept. **Disconnect Gmail** ends the connection and revokes its access, and also keeps the app credentials for next time.
