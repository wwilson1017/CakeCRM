---
title: Connecting Gmail
description: Bring your own Google OAuth app so Baker can search and read mail and draft replies. It can never deliver mail.
aliases: gmail, email, google, mailbox, inbox, oauth, drafts, mail, send, deliver, reply
admin: true
---
## The guarantee, first

Baker gets exactly two abilities over your mailbox: **read** and **create a draft**. There
is no tool anywhere in this product that transmits mail, and none may be added. A draft is
always handed back for you to review and deliver yourself from Gmail.

Google's scopes cannot express "compose but never deliver", so the limit is enforced in
this product instead: only three mail tools exist, a runtime allow-list refuses any other
Gmail operation, and a test fails the build if a delivery surface ever appears. This is
written down as a trust guarantee in the project's security document.

## Who can do this

Admin only. The whole Integrations section of Settings is hidden from members.

## Setting it up

You bring your own Google OAuth application, so your mail never passes through anyone
else's project.

1. Open Settings, Integrations, Gmail. The card shows the exact redirect address to
   register — copy it.
2. In the Google Cloud console, under APIs and services: enable the **Gmail API**, create an
   **OAuth client** of type **Web application**, and add that redirect address to it.
3. Paste the client ID and client secret into the card and save.
4. Click Connect. You are sent to Google's consent screen and back again. The card then
   shows the connected account.

Two scopes are requested and no others: read-only mail access, and compose access for
drafts. No identity scopes are requested.

## If connecting fails

The connection is refused rather than half-made, and the page says why. The usual causes
are declining consent, Google not returning a refresh token, or not granting both scopes.
In every one of those cases the fresh grant is revoked immediately so nothing dangles. Fix
the cause and click Connect again.

## What Baker can do once connected

- `gmail_search` — find messages.
- `gmail_read_thread` — read one conversation.
- `gmail_create_draft` — compose a draft in your Gmail drafts folder. This one asks for your
  confirmation before it runs.

Mail read into a conversation is treated as untrusted third-party text: it can never act as
an instruction to Baker, and reading it tightens the confirmation rules for the rest of that
conversation. See `assistant/confirmations`.

## When the connection breaks

If Google revokes or expires the grant, the card says your Gmail connection expired and
offers to reconnect. Your saved client ID and secret are kept, so reconnecting is one click.

## Disconnecting

Disconnect clears the connection and revokes exactly the grant it held. The app credentials
stay, so you can reconnect later without going back to the Google console.

## The quiet part: touch logging

While Gmail is connected, a background job periodically reads recent inbox mail, matches
senders to contacts by exact email address, and logs an email touch against that contact —
so exchanges nobody wrote down still count toward activity. It attributes the touch to a
deal only when the contact has exactly one open deal, and never invents one. It uses the
same read-only ability already approved above; it needs no AI key. A stranger who writes
three times with no matching contact raises a single "create a contact?" alert.
