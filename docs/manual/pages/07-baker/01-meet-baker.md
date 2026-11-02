---
title: Meet Baker
summary: Baker is the AI sales assistant built into CakeCRM. It is optional, and the CRM works fully without it.
shots: []
---

Baker is the one assistant built into CakeCRM. It reads your pipeline, contacts, companies and todos, answers questions about them, and can make changes for you with your approval. The name is fixed; every install's assistant is called Baker.

Baker is optional. Every part of the CRM works with no AI provider connected. Until an admin connects one, Baker's chat stays hidden rather than showing errors. See [AI setup](06-ai-setup.md).

## Opening Baker

Baker lives behind the **Ask Baker** button in the bottom-right corner of every CRM page. After a few seconds the button folds down to an icon; move your pointer near it and the label comes back. Click it to open the chat drawer.

On an install with no provider connected, the same button reads **Hire your assistant** and leads to AI setup instead of opening a chat.

## Baker knows what you have open

The drawer is aware of the page behind it. Open a deal, a contact or a company and Baker sees which record you mean, and the empty chat offers suggestions to match, such as:

- "Summarize this deal" or "What should my next step be on this deal?"
- "Catch me up before I call them" on a contact
- "Which deals here need attention?" on a company

You can also just type. Press Enter to send, Shift+Enter for a new line.

## The drawer at a glance

| Control | What it does |
|---|---|
| Conversations | Your past chats with Baker. Pick one to reopen it, or delete it. |
| New chat | Starts a fresh conversation. |
| Assistant settings | Shows Baker's name and personality. Only an admin can edit the personality. |
| Attach a file | Adds files — and, when the model can see images, photos — to your message for Baker to read. |
| Take photo | On a phone, opens the camera. The browser normally shrinks the photo and drops location data before sending. Shown only when the model can see images. |
| Read / Ask / Auto | How much Baker may change without asking. See [Approvals](03-approvals.md). |
| Stop | Ends a reply Baker is still working on. |

## Leaving mid-answer

Closing the tab, locking your phone or switching to another conversation does not stop a reply. It keeps running on the server, and when you come back the drawer shows what Baker is doing and how long it has been at it. Press **Stop** to end it; Baker stops at its next step, and a step already running may still finish.

## When a reply doesn't finish

If the AI service stops after starting a reply or completing a step, Baker saves a
short note explaining why no complete reply arrived. This also covers an empty answer,
a response cut off by its length limit, or too many tool rounds. Any partial reply is
kept with the note. If steps already ran, check their results before asking again:
a missing reply does not mean that no changes were made.

For Anthropic, OpenAI and OpenAI-compatible providers, Baker waits briefly and retries
the same model once per turn when a rate limit arrives before any output from that
attempt. It uses the service's retry delay, capped at 10 seconds, or waits 2 seconds
when no delay is supplied. If the rate limit persists, the error names the model.

Stopping a reply yourself or a server restart does not add this note. A failure before
any reply or completed step still shows an error without adding an assistant message.

## Yours, and the team's

Your conversations with Baker are yours alone. Nobody else can read them, admins included.

Baker's memory is different: the facts it records and the files it keeps are shared by the whole team. Something you tell Baker in a private chat can become a fact every seat can see. See [Memory](04-memory.md).

## On your phone

Each person can link their own Telegram account and chat with Baker from there. See [Telegram](../09-integrations/02-telegram.md).

## Talk to Baker and hear replies

When an OpenAI key is connected and your browser can record audio, a mic appears beside Send. Hold to speak and let go to send, slide left to cancel, or tap or swipe up for hands-free recording. Stop in hands-free mode puts the words in the box to review. Unless an admin enables Live transcription, text arrives after recording ends; a timer and wave show progress while you speak. Recordings stop after three minutes. The mic needs a secure page and browser permission. An OpenAI key can handle speech even when another provider handles chat.

The speaker toggle in the header reads new replies once they finish. It starts off and is remembered on this device. Sending a message arms that thread for automatic reading, including replies after approvals; opening history alone does not. Each finished reply also has a Read aloud button. The browser's speech engine needs no additional key; some voices use a network service from your browser vendor. Starting the mic stops playback. Recordings go through your install to the transcribing provider and are never stored by CakeCRM.

## Live transcription

Admins can opt in under **Settings → Assistant → Live transcription** when a transcribing provider is ready. The switch starts off. Changes apply on this page immediately; other open tabs pick them up when reloaded.

Live transcription shows your words while you speak. To do that, your browser hands the microphone audio to its own speech recognition service, which is usually run online by the browser maker (for example Google for Chrome, Apple for Safari). That audio goes straight from the browser, not through this install, under that company's terms, and cancelling a recording cannot take back audio it has already heard. The text that is sent or saved still comes from the normal transcription after you let go. When the switch is off (the default), audio goes only through this install to your transcribing provider.

Desktop Chrome, Edge and Safari, and iOS Safari where speech recognition exists, can show a preview. Some browsers, including Firefox and Android, show no live text; the recording works as usual.
