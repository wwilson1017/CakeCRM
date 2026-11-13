---
title: Talk to Baker and dictate notes
description: Hold to talk to Baker, dictate a note for review, or read replies aloud with your browser's voice.
aliases: live transcription, voice, microphone, mic, dictate a note, dictation, hands-free recording, hold to talk, speak, read replies aloud, transcribe, voice note
admin: false
---

## Talking to Baker

Hold the mic beside Send to speak, then let go to send your words to Baker along with anything already typed. Slide left to cancel without uploading. Swipe up or tap for hands-free recording; Stop puts the words in the box for you to check before sending. The timer and wave show recording progress. Text arrives after you let go unless an admin turns on Live transcription. A recording stops after three minutes and puts the text in the box for review.

## Dictating a chatter note

Use the mic after Attach and Take photo on a contact, company or deal. Speak and let go: the text lands in the note box, with a best-effort tidy of ums, repetitions and false starts. Nothing is saved until you press Post. The tidy is asked to keep names, numbers, dates, times and the original language. A conservative word check falls back to the raw transcript if it detects a dropped name or number. It cannot guarantee the meaning is unchanged: always review the text before saving.

## Hearing replies

The speaker toggle in the panel header reads new replies once they finish, including continuations after an approval. It is off until you turn it on, and remembered on this device. Opening an old conversation does not start reading it; sending a message arms that conversation for new replies. Each finished reply also has a Read aloud button and a Stop button while playing. Read-aloud uses your browser's voices and needs no additional provider key. Starting a recording stops speech playback.

## When the mic is missing

Baker must be ready and a provider that can transcribe must be connected. Today that means an OpenAI key, even if another provider handles Baker's chat. The mic also hides if your browser cannot record, including when microphone access is unavailable on a page without HTTPS. Allow microphone permission when asked. If you decline, your browser settings let you enable it later.

## What leaves your install

The recording goes through your install to the transcribing provider and is never stored by CakeCRM. A dictated note's transcript also goes to your active chat provider for the tidy pass. Audio is kept only in memory while recording or transcribing; a failed upload keeps the clip for Retry until you discard it or leave the composer. Read-aloud uses the browser's speech engine; CakeCRM sends no speech request, though some browser voices use the browser vendor's network service.

## Live transcription

Live transcription shows your words while you speak. To do that, your browser hands the microphone audio to its own speech recognition service, which is usually run online by the browser maker (for example Google for Chrome, Apple for Safari). That audio goes straight from the browser, not through this install, under that company's terms, and cancelling a recording cannot take back audio it has already heard. The text that is sent or saved still comes from the normal transcription after you let go. When the switch is off (the default), audio goes only through this install to your transcribing provider.

An admin can enable this for the install under Settings → Assistant → Live transcription when a transcribing provider is configured. The change takes effect on this page immediately; other open tabs pick it up when reloaded. Desktop Chrome, Edge and Safari, and iOS Safari where speech recognition is available, can show the preview. Some browsers, including Firefox and Android, show no live text; the recording works as usual.
