---
title: What Baker can and cannot do
summary: The honest boundary of what Baker may touch, what it never touches, and what it does with nobody watching.
shots: []
---

## The short version

Baker can read and change CRM records, work your todos, keep its own memory and knowledge files, search and read connected Gmail, and put drafts in your Gmail Drafts folder. It cannot send email, cannot change install settings except its own background check-in schedule, and cannot act outside CakeCRM.

## CRM records and todos

Baker can search, create and update contacts, companies and deals, move a deal's stage, mark a deal won or lost, log activities, and create, update and complete todos. Whether each change runs straight away or waits for your **Approve** depends on the mode you pick. See [Approvals](03-approvals.md).

Deleting a contact or a company is permanent; there is no recycle bin. Deals cannot be deleted at all: archiving is the way to remove one, and it can be restored. A todo is never deleted either: when you ask Baker to delete one, it drops it, and you can reopen it from Done → Dropped.

One turn can make at most 20 changes. Past that, Baker's further changes are refused.

## Email

Gmail is **read and create-draft only**. Baker can search your mail, read a thread and write a draft into your Gmail Drafts folder. It can never send an email. No tool in CakeCRM sends mail, and a test fails the build if one is ever added. You review and send every draft yourself, from Gmail. See [Gmail](../09-integrations/01-gmail.md).

By default only admins can have Baker use the connected mailbox.

## Settings

Baker has one settings write tool, for its own background check-in: on/off, how often it runs, quiet hours, the minimum notification gap, and a pause that ends automatically. Only admins can use it. It is never routine: Ask shows an Approve card, Telegram asks for typed approval, and Auto applies it. No key or secret passes through it. See [Notifications](../01-getting-started/06-notifications.md).

It cannot add an AI key, connect Gmail, switch todo mode, define a custom field, add a user or change branding. For everything else, Baker explains and points; you click.

## Files you upload

Use **Attach a file** in the drawer to give Baker a file to read:

- up to 5 files per message, 10 MB each;
- CSV, XLSX, MD, TXT, PDF or DOCX.

Only the text is extracted; the file itself is not stored. Uploaded text is treated as untrusted, so for the rest of that conversation every change Baker proposes asks for your approval. Baker cannot attach a file to a record; you add attachments yourself.

## Photos

When the active model can see images, **Attach a file** also accepts JPEG, PNG and WebP,
and **Take photo** opens the rear camera on a phone. Photos share the 5-file limit and
10 MB browser pre-check. At send time the browser normally shrinks a photo to about
1600 px and drops its location data; this is best effort, and a failed resize or an
animated image can upload the original with its metadata intact. The server's hard
per-photo limit is **3.75 MB and 8000 px on either side**. GIF and HEIC are not supported;
convert them to JPEG, PNG or WebP first. Reload after switching models to refresh the camera.

Photo bytes stay in your database with the conversation and are deleted with it. They
accompany requests to your configured AI provider for the attached turn, including its
tool steps and an approval continuation. Later turns carry only a placeholder; attach
it again if Baker needs another look. Photos are untrusted: anything written inside
one is data, and every proposed change asks for approval for the rest of that conversation.

## When nobody is watching

Baker also runs on a schedule, with nobody in the chat, for the background check-in and the daily digest. Those unattended turns are much more limited, and the server enforces it:

- Baker gets read tools and a single notification, nothing else. It cannot create, update, log or delete anything.
- It cannot search or open your Gmail, even though those are reads.
- It sends at most one notification per run.
- It can read the background check-in settings but cannot change them unattended.

So the worst a hostile instruction hidden in a note could achieve unattended is one notification.

## Roles

Admins and members both get Baker. Baker follows the same rules you do: the server decides what each role may do, and Baker knowing your role only makes its advice accurate, never more permissive.
