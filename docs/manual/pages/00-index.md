---
title: CakeCRM user manual
summary: How to use every part of CakeCRM, screen by screen. Pick a section below or use the contents on the left.
shots: []
---

CakeCRM is a free, self-hostable CRM with a built-in AI sales assistant called Baker. The CRM is the product and Baker is one feature of it: every part of the CRM works with no AI provider connected. AI features stay hidden until an admin connects one, and they never show errors in the meantime.

## How the pieces fit

The navigation has six destinations, and each one answers a different question.

| Destination | What it is for |
|---|---|
| Dashboard | What needs you today, and how the pipeline looks overall |
| Pipeline | Your deals, by stage, as a board or a list |
| Contacts | The people you sell to |
| Companies | The organisations they belong to |
| Todos | Your follow-ups, the one follow-up tool in the product |
| Reports | Everything known about one company, on one page |

A typical loop: create a contact, create a deal linked to it, log an activity against the deal, and add a follow-up todo. That loop is the whole CRM in miniature. Start with [What CakeCRM is](01-getting-started/01-what-cakecrm-is.md) if you are new.

Baker sits in the bottom-right corner of every page. Once an admin connects an AI provider it can read your CRM, make changes for you, and answer questions about your pipeline. You choose how much it asks first: see [Approvals and modes](07-baker/03-approvals.md). See [Meet Baker](07-baker/01-meet-baker.md).

## Admins and members

There are exactly two roles.

- **Members** can do everything with CRM records: create, edit and close deals, contacts, companies and todos. Owning a record is not a permission; any member may edit any record.
- **Admins** can also change how the install is set up: branding, the team, custom fields, integrations, the todo mode, AI providers and Baker's protected memory files.

In Settings, members see the Personal and Assistant sections; Workspace and Integrations are admin-only and do not appear for a member. Where a page in this manual describes something only an admin can do, it says so.

## Running your own copy

If you are installing CakeCRM rather than using an existing install, start with [Deploying](10-self-hosting/01-deploying.md) and [The first admin and passwords](10-self-hosting/02-first-admin-and-passwords.md).
