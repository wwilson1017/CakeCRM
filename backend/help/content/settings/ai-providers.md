---
title: AI providers and keys
description: Connect an AI provider so the assistant works. Admin only, entered in the app, encrypted at rest.
aliases: api key, ai key, anthropic, openai, google, gemini, together, ollama, model, provider, llm
admin: true
---
## Who can do this

Connecting, disconnecting and switching providers is **admin only**. A member can see
whether AI is available but cannot change it.

## The providers

Four take an API key: **Anthropic**, **OpenAI**, **Google** and **Together AI**. One takes
no key at all: **Ollama**, which runs models locally and is configured with a base address
instead. Anything else is not supported.

## Adding a key

Keys are entered **in the app**, never as environment variables. Paste the key into the
provider's card in setup and save. The server validates it immediately with a live call to
that provider, so you find out at once whether it works:

- On success the model catalogue is refreshed and, if the model previously selected is not
  available under this key, the active model is repointed to a sensible one automatically.
- On failure nothing is stored. You get an error saying the key could not be validated and
  asking you to check the key and that the service is reachable.

Keys are encrypted at rest before they touch the database. The encryption key itself comes
from the environment, or the operating system keychain, or a file beside the data
directory, in that order.

## Ollama

Ollama needs no key. Give it the address it is listening on and pick a model. The server
checks that it is reachable and that at least one model is actually installed — if none is,
it says so and tells you to pull one.

The address must be a plain origin: an `http` or `https` scheme, a host, an optional port,
and nothing else — no username or password, no path, no query string, no fragment. That
check exists so a crafted path or query cannot redirect the server's own request somewhere
else. **It does not restrict which hosts you may name**, and deliberately so: Ollama
normally runs on the same machine or on the local network, so those addresses have to be
allowed. Do not tell anyone this setting cannot reach an internal service — it can, by
design, because the admin who sets it owns the machine.

## Choosing a model, and the three tiers

One provider and one model are active at a time. Beyond that, models are grouped into three
tiers — **top**, **mid** and **light**. Cheap background work uses the light tier so
routine automation never costs top-tier money. An admin can override which model sits in
each tier; otherwise the tiers are inferred from the live catalogue.

## What "ready" means

The CRM reports one flag: whether AI is ready, meaning the active provider has usable
stored credentials. Every AI affordance keys off it:

- Ready — the assistant launcher opens a chat drawer, assisted import accepts more file
  formats, touch counts are estimated.
- Not ready — those affordances are hidden, and a dismissible banner offers to add a key.
  Nothing errors and nothing is blocked.

## Disconnecting

Disconnecting a provider deletes its stored key. If it was the active provider, the active
provider and model are cleared with it.
