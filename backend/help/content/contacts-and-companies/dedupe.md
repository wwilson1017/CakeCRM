---
title: Finding duplicates
description: The duplicate scan is exact matching, not fuzzy. Deals can be merged; contacts and companies cannot.
aliases: duplicates, dedupe, merge, duplicate contacts, same person, cleanup, merge deals
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

**The only merge in this product is deal-to-deal.** There is no contact merge and no company
merge. If someone asks how to merge two contacts, the honest answer is that the product
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

## Preventing duplicates in the first place

Search before you create — `crm_find_contact`, `crm_search_companies`, `crm_search_deals`. If
a near match turns up that you are not sure about, show it and ask rather than creating a
second record. Duplicate records are the commonest way a CRM rots, and here they are
expensive because two of the three kinds cannot be merged back together.
