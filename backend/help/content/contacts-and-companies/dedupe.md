---
title: Finding duplicates
description: The duplicate scan is exact matching, not fuzzy. Deals and companies can be merged; contacts cannot.
aliases: duplicates, dedupe, merge, duplicate contacts, same person, cleanup, merge deals, merge companies, duplicate companies
admin: false
---
## The scan

`crm_find_duplicates` groups records that match **exactly**, after trimming whitespace and
lowercasing. There is no fuzzy matching, no similarity score and no phonetic matching
anywhere in this product. "Bob Smith" and "Robert Smith" will never be reported as
duplicates, and neither will two spellings of one company.

What it compares:

- **Contacts** — by email address, and separately by name. Contacts with no email are left
  out of the email pass. A group can appear in both passes.
- **Companies** — by domain only. Names are not compared, because the database already
  refuses two companies whose names differ only in case or spacing, so a name pass could
  never find anything.
- **Deals** — by the pair of title and linked contact. Title alone is not enough: the same
  renewal title across ten customers is not a duplicate.

## Merging

**Deals and companies can be merged.** Contacts cannot be merged. If someone asks how to merge two contacts, the honest answer is that the product
cannot, and the practical route is to move what matters onto the record you are keeping and
delete the other.

## Merging two deals

`crm_merge_deals` takes the deal you are keeping and the one you are folding in. Confirm
which survives before running it — it is not symmetric.

What happens:

- **Activity and open todos move** from the source onto the survivor.
- **Notes are copied** onto the survivor, marked as merged from the other deal.
- **Custom fields are gap-filled**: the survivor's own values always win, and only fields it
  left blank are taken from the source.
- The survivor's own title, value, stage and links are **not touched**. A merge consolidates
  history; it never silently edits the deal you kept.
- **The source is archived**, not deleted.

Restoring the source later makes it visible again but does not undo any of the above.

## Merging two companies

Call `crm_preview_company_merge` first and show the counts: they are a **current estimate,
recomputed at merge**. Confirm which company survives, then call `crm_merge_companies` with
both company ids and a required reason. In Ask mode the merge keeps its approval card.
The **Merge into…** button on a company's page offers the same flow without an AI provider:
pick the company to keep, review the preview, say why, then confirm.

- Contacts and deals move, **including archived deals**. Their todos and activity follow
  through the existing contact/deal links; the preview counts open, non-dropped todos on
  live deals.
- Company notes are copied, marked with the source company. The original thread keeps
  its attachments and mentions; those are not copied.
- Custom fields and blank domain, industry, phone, address and source fields are filled
  from the duplicate. The survivor's own populated values win. Different company notes
  are appended with the source name.
- The duplicate is **archived, never deleted**, with who merged it and why in its notes.
  The automatic merge notes do not count as contact.
  Its spelling continues to resolve to the survivor on future imports and company links.
- There is **no undo**. A merged duplicate cannot be restored; open its successor instead.
  A manually archived duplicate can be absorbed, but the survivor must be active.

## Preventing duplicates in the first place

Search before you create — `crm_find_contact`, `crm_search_companies`, `crm_search_deals`. If
a near match turns up that you are not sure about, show it and ask rather than creating a
second record. Duplicate records are the commonest way a CRM rots, and here they are
expensive because contacts cannot be merged back together.
