---
title: AI setup
summary: An admin connects an AI provider and picks a model; until then, AI features stay hidden and the CRM works as normal.
shots: [ai-setup]
---

Connecting an AI provider is **admin only**. Admins find **AI Setup** in the top bar on a computer, or in the menu on a phone. Members do not see it.

{{shot:ai-setup}}

## With no provider

CakeCRM works fully without any AI key. Until a provider is connected:

- the Baker chat is hidden, and the corner button reads **Hire your assistant**;
- importing contacts accepts CSV and vCard only;
- the estimated touch-count on deal cards is hidden.

A dismissible banner offers to add a key. It is a prompt, never a gate. Lead scores, duplicate checks, reports and the rest of the CRM need no AI at all.

## The providers

The AI Assistant Setup page has one card per provider:

| Card | How it connects |
|---|---|
| Anthropic (Claude) | API key |
| OpenAI (GPT) | API key |
| Google (Gemini) | API key |
| Together AI | API key |
| Ollama (Local) | The address of an Ollama server, no key |

Ollama runs models on your own hardware. Its card is not shown on a Railway deploy, which has no local machine to reach. The address must be a plain `http` or `https` origin, with no path or query.

## Connecting

1. Paste the key into the provider's card and click **Connect**.
2. CakeCRM checks the key straight away with a live call to the provider. If it fails, nothing is saved and you are told to check the key.
3. Once connected, choose a **Model**. If this is not the active provider yet, click **Set as active**.

Only one provider and model are active at a time. Keys are entered in the app, never as environment variables, and are encrypted before they are stored.

## Model tiers

Models are grouped into three tiers: top, mid and light. Cheap background work uses the light tier, so routine automation does not cost top-tier prices. Each connected card except Ollama's lets an admin override which model sits in each tier, or leave it on **Use inferred default**.

## Disconnecting

**Disconnect** deletes that provider's stored key. If it was the active provider, the active provider and model are cleared too, and AI features hide again.
