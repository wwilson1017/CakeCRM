---
title: What Baker can and cannot do
summary: The honest boundary of what Baker may touch, what it never touches, and what it does with nobody watching.
shots: []
---

## The short version

Baker can read and change CRM records, work your todos, keep its own memory and knowledge files, search and read connected Gmail, and put drafts in your Gmail Drafts folder. It cannot send email, cannot change install settings, and cannot act outside CakeCRM.

## CRM records and todos

Baker can search, create and update contacts, companies and deals, move a deal's stage, mark a deal won or lost, log activities, and create, update and complete todos. Whether each change runs straight away or waits for your **Approve** depends on the mode you pick. See [Approvals](03-approvals.md).

Deleting a contact, a company or a todo is permanent; there is no recycle bin. Deals cannot be deleted at all: archiving is the way to remove one, and it can be restored. A todo can be dropped instead of deleted.

One turn can make at most 20 changes. Past that, Baker's further changes are refused.

## Email

Gmail is **read and create-draft only**. Baker can search your mail, read a thread and write a draft into your Gmail Drafts folder. It can never send an email. No tool in CakeCRM sends mail, and a test fails the build if one is ever added. You review and send every draft yourself, from Gmail. See [Gmail](../09-integrations/01-gmail.md).

By default only admins can have Baker use the connected mailbox.

## Settings

Baker has no settings tools at all. It cannot add an AI key, connect Gmail, switch todo mode, define a custom field, add a user or change branding. It can explain how to do these things and point you to the right screen; you do the clicking.

## Files you upload

Use **Attach a file** in the drawer to give Baker a file to read:

- up to 5 files per message, 10 MB each;
- CSV, XLSX, MD, TXT, PDF or DOCX.

Only the text is extracted; the file itself is not stored. Uploaded text is treated as untrusted, so for the rest of that conversation every change Baker proposes asks for your approval. Baker cannot attach a file to a record; you add attachments yourself.

## When nobody is watching

Baker also runs on a schedule, with nobody in the chat, for the daily digest and nudges about deals and leads that have gone quiet. Those unattended turns are much more limited, and the server enforces it:

- Baker gets read tools and a single notification, nothing else. It cannot create, update, log or delete anything.
- It cannot search or open your Gmail, even though those are reads.
- It sends at most one notification per run.

So the worst a hostile instruction hidden in a note could achieve unattended is one notification.

## Roles

Admins and members both get Baker. Baker follows the same rules you do: the server decides what each role may do, and Baker knowing your role only makes its advice accurate, never more permissive.
