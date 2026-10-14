---
title: Approvals and modes
summary: Choose how much Baker may change on its own, and what still asks for your approval in every mode.
shots: []
---

## Three modes

At the bottom of the Baker drawer are three buttons: **Read**, **Ask** and **Auto**. Hover one to see its description. **Ask** is the default.

| Mode | What it means |
|---|---|
| Read | Read-only. Baker can look things up but not change data. It is not even offered the tools that change things, and would be refused if it tried one. |
| Ask | Everyday CRM edits run right away. Deletes, archives, merges, bulk changes, email drafts, notifications and memory always ask. |
| Auto | Changes run without asking, apart from the exceptions below. |

Looking things up never asks, in any mode.

## What Ask runs straight away

In Ask mode, a change runs without a card only when it stays inside a CakeCRM record, notifies nobody, removes nothing from view, is not a bulk change, and sends nothing outside your install. In practice that means:

- creating and updating contacts, companies and deals;
- moving a deal's stage, and marking a deal won or lost;
- logging an activity;
- creating, updating and completing todos, and creating or updating projects;
- setting custom-field values on a contact, company or deal.

A few of those calls still ask, because of what they would do: archiving a contact or company, dropping a todo or project, or setting a bring-back date that hides a todo. Marking a todo done, completing a project, or filing either to someday runs without asking.

## What always asks in Ask mode

Deleting, archiving, merging, bulk moves and bulk todo updates, recomputing lead scores, creating a Gmail draft, sending a notification, recording a memory fact, and any change to Baker's protected files. Anything new that has not been classified asks too.

## What asks even in Auto

- **Writing Baker's protected files**, soul.md and MEMORY.md. These always ask, in every mode, and only an admin can change them. See [Memory](04-memory.md).
- **A conversation that has read outside content.** Once Baker has read an email or an uploaded file in a conversation, every change it proposes asks for approval for the rest of that conversation, even after that message has scrolled out of view. Text someone else wrote can never quietly steer Baker into changing your data.

## Approving a change

When Baker wants to make a change that needs approval, its message carries a card naming the action and showing exactly what it will do, with **Approve** and **Deny**. Until you answer, nothing has happened.

In Telegram the same thing arrives as two buttons, Approve and Deny, and only the linked account can press them. Telegram chats always run in Ask mode.
