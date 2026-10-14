---
title: Baker's memory
summary: See and edit what Baker knows, from its knowledge files to the individual facts it has recorded.
shots: [memory]
---

Baker keeps two kinds of memory: **knowledge files** it reads at the start of a conversation, and **recorded facts** it looks up as you talk. Both are shared by the whole team, and both work with or without an AI provider connected.

To open them, go to **Settings**, **Assistant**, **Assistant memory** and click **Open memory**. The Memory page has two tabs: **Knowledge files** and **Recorded facts**.

{{shot:memory}}

## Knowledge files

| File | Shown as | What it holds |
|---|---|---|
| soul.md | Identity | Baker's own description of itself: how you work, patterns it has noticed. |
| MEMORY.md | Snapshot | A living summary of key people, active deals, decisions and lessons. |
| topics/... | Topic | One file per subject, such as a process or a pricing rule. |
| daily/... | Daily note | A running log for each day. |

Pick a file to read it. Each file shows who last edited it: Baker, You, or Built-in. Edit the text and click **Save**, or **Revert** to throw your edits away. If Baker or another tab changed the file while you had it open, saving is refused and you are asked to reload, so nobody's edit is silently overwritten.

## The two protected files

soul.md and MEMORY.md are protected because Baker reads them in every conversation:

- **Everyone can read them.** On a member's seat they open read-only, with no Save.
- **Only an admin can change them**, on this page or by asking Baker. On a member's seat Baker refuses the rewrite.
- **Baker always asks before changing them**, in every mode, even Auto.
- **They cannot be deleted.**

Topic files and daily notes are open to everyone: any seat can edit or delete them here or through Baker.

## Recorded facts

Baker records durable things it learns as short facts, such as who prefers what or when a decision was made. On the **Recorded facts** tab you can search them and **Delete** any one. Deleting here is permanent. When Baker decides a fact is no longer true, it retires it with an end date instead, so the history stays readable.

Each night a background tidy archives facts that have gone unused, without deleting them. It uses no AI, never archives decisions or preferences, and leaves anything under a month old alone.

## Memory is team memory

What Baker remembers, every seat can see. A fact recorded from your private conversation shows up on this page for your colleagues too. If something should not be shared, ask Baker not to remember it.
