---
title: Meet Baker
summary: Baker is the AI sales assistant built into CakeCRM. It is optional, and the CRM works fully without it.
shots: []
---

Baker is the one assistant built into CakeCRM. It reads your pipeline, contacts, companies and todos, answers questions about them, and can make changes for you with your approval. The name is fixed; every install's assistant is called Baker.

Baker is optional. Every part of the CRM works with no AI provider connected. Until an admin connects one, Baker's chat stays hidden rather than showing errors. See [AI setup](06-ai-setup.md).

## Opening Baker

Baker lives behind the **Ask Baker** button in the bottom-right corner of every CRM page. After a few seconds the button folds down to an icon; move your pointer near it and the label comes back. Click it to open the chat drawer.

On an install with no provider connected, the same button reads **Hire your assistant** and leads to AI setup instead of opening a chat.

## Baker knows what you have open

The drawer is aware of the page behind it. Open a deal, a contact or a company and Baker sees which record you mean, and the empty chat offers suggestions to match, such as:

- "Summarize this deal" or "What should my next step be on this deal?"
- "Catch me up before I call them" on a contact
- "Which deals here need attention?" on a company

You can also just type. Press Enter to send, Shift+Enter for a new line.

## The drawer at a glance

| Control | What it does |
|---|---|
| Conversations | Your past chats with Baker. Pick one to reopen it, or delete it. |
| New chat | Starts a fresh conversation. |
| Assistant settings | Shows Baker's name and personality. Only an admin can edit the personality. |
| Attach a file | Adds files to your message for Baker to read. |
| Read / Ask / Auto | How much Baker may change without asking. See [Approvals](03-approvals.md). |
| Stop | Ends a reply Baker is still working on. |

## Leaving mid-answer

Closing the tab, locking your phone or switching to another conversation does not stop a reply. It keeps running on the server, and when you come back the drawer shows what Baker is doing and how long it has been at it. Press **Stop** to end it; Baker stops at its next step, and a step already running may still finish.

## Yours, and the team's

Your conversations with Baker are yours alone. Nobody else can read them, admins included.

Baker's memory is different: the facts it records and the files it keeps are shared by the whole team. Something you tell Baker in a private chat can become a fact every seat can see. See [Memory](04-memory.md).

## On your phone

Each person can link their own Telegram account and chat with Baker from there. See [Telegram](../09-integrations/02-telegram.md).
