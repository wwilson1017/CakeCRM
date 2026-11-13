---
title: Duplicates
summary: How to find duplicate records, and why contacts cannot be merged.
shots: []
---

Duplicate records are the commonest way a CRM goes stale. CakeCRM helps you avoid them, but it cannot merge contacts, so prevention matters most.

## Preventing duplicates

- **Search before you add.** Type the name or email in the Contacts or Companies search box first.
- **Pick, don't retype.** When you link a company on a contact or deal form, pick it from the list rather than creating a new one. Company names are already matched without regard to capitals or extra spaces, so two spellings that differ only that way become one company.
- **Clean files before importing.** Import does no duplicate check. See [Importing contacts](06-importing.md).

## Finding duplicates with Baker

With an AI provider connected, ask Baker to look for duplicates. The check is **exact matching only**, after ignoring capitals and extra spaces. It never guesses, so "Bob Smith" and "Robert Smith" are not reported.

| Record | Compared by |
|---|---|
| Contacts | Email address, and separately by name |
| Companies | Domain only |
| Deals | Title together with the linked contact |

There is no duplicate screen outside Baker. On an install with no AI provider, search the lists by hand.

## Merging

**Deals and companies can be merged.** Contacts cannot be merged. To clean up two contacts for one person, move what matters onto the one you keep, then delete the other.

Baker can merge two deals for you. In Baker's **Ask** mode, a merge always waits for your approval (see [Approvals](../07-baker/03-approvals.md)), so check which deal it keeps. The deal you keep gets the other's activity, open todos and copied notes. Its own title, value, stage and links stay as they were. The other deal is archived, not deleted.

On a company's page, **Merge into…** lets you pick the company to keep, shows a current estimate
of what will move (recomputed at merge), and asks why. Contacts and deals, including archived
deals, move to the survivor; their todos and activity follow. Company notes are copied and
blank fields are filled without replacing the survivor's own values. The duplicate is archived,
with who merged it and why recorded. Its spelling resolves to the survivor on future imports.
There is no undo, and a merged duplicate cannot be restored. Baker can do this too; in
**Ask** mode the merge always waits for your approval.
