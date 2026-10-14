---
title: What CakeCRM is
summary: A free, self-hostable CRM with an AI assistant built in, and a CRM that works fully without it.
shots: []
---

CakeCRM is a free, open-source CRM you run on your own server. It keeps your contacts, companies, deals and follow-up todos in one place, and it comes with a built-in AI sales assistant called Baker.

## The CRM comes first

Every part of the CRM works with no AI provider configured. Lead scores, duplicate scans, deal staleness, the dashboard, analytics and the company report are plain database work and are always available.

What stays hidden until an admin connects a provider:

- Baker's chat itself.
- Assisted import of file formats beyond CSV and vCard.
- The estimated touch-count pill on deal cards, and the Weekly touches card on the dashboard.
- AI provenance badges.

These hide rather than break. You never see an error because no key is set. An admin may see a dismissible banner offering to add a key; it is a prompt, never a gate, and dismissing it is remembered.

## Where things live

| Page | What you find there |
|---|---|
| [Dashboard](../02-dashboard/01-dashboard.md) | A Today panel ranking what needs you now, headline counts, a snapshot and recent activity |
| [Pipeline](../03-pipeline/01-board.md) | The deal board, by stage, with a list view and filters |
| [Contacts](../04-contacts-and-companies/01-contacts-list.md) and [Companies](../04-contacts-and-companies/03-companies.md) | Searchable lists with detail pages that roll up linked records, notes and activity |
| [Todos](../05-todos/01-todo-modes.md) | Your follow-ups |
| [Reports](../06-reports/01-company-report.md) | One report: everything known about a single company |
| [Settings](../08-settings/01-personal.md) | Personal, Assistant, Workspace and Integrations |

## Your first ten minutes

1. [Sign in](02-signing-in.md).
2. If the CRM is empty, you are offered [sample data](03-sample-data.md): fictional contacts, deals and todos so you can see how everything behaves.
3. Walk the navigation: Dashboard, Pipeline, Contacts, Companies, Todos, Reports.
4. Create a contact, create a deal linked to it, log an activity against the deal, and add a follow-up todo.

## Adding AI

An admin connects a provider and a model on the AI Setup page. Once a key is connected, the Baker button in the bottom-right corner opens a chat drawer instead of pointing at setup. See [AI setup](../07-baker/06-ai-setup.md).
