---
title: Assistant settings
summary: Open Baker's memory, choose the todo mode, and manage the no-login todo links.
shots: [settings-assistant]
---

The **Assistant** tab has two cards. Everyone sees **Assistant memory**. **Todo mode** is for admins only.

{{shot:settings-assistant}}

## Assistant memory

**Open memory** takes you to the Memory page. There you can read and edit what Baker knows: its description of itself, its running summary, topic notes, daily logs and the individual facts it has recorded. It works with or without an AI provider.

Baker's memory is shared by the whole team. Anyone can read it. Changing the two protected files, `soul.md` and `MEMORY.md`, is for admins only. See [Memory](../07-baker/04-memory.md).

## Todo mode (admins)

Pick one of two buttons:

- **Todo-GTD (default)** — inbox, contexts, projects, repeats and a weekly review.
- **Simple list** — just todos with due dates and priorities, no inbox or contexts.

Switching is safe and reversible. Both modes read the same todos, and nothing is moved, copied or deleted. The setting applies to everyone. See [Todo modes](../05-todos/01-todo-modes.md).

## No-login links (admins)

The same card controls two links that work without signing in. They show in both todo modes.

**Quick capture (write-only)** drops text into the inbox and cannot read anything back. It starts with a **Public link**, and the card warns that anyone who knows the address can add to your inbox. **New secret link** restricts it. **Make public** removes the secret again.

**Full todo app (read and write)** is off by default. Tick **Enable the no-login todo app** to turn it on. A secret link is made at the same time. **New secret link** replaces it at once, breaking the old link, including any home-screen app installed from it.

**Copy** puts a link on your clipboard. These links are shown on this card and nowhere else. Treat them like passwords. See [No-login links](../05-todos/11-no-login-links.md).

## AI provider keys

Connecting an AI provider is not on this tab. Admins use **AI Setup** in the top bar. See [AI setup](../07-baker/06-ai-setup.md).
