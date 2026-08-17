# Security

## The Gmail guarantee: read + draft only, never send

CakeCRM's built-in assistant can **read** your email (search and read threads) and
**create drafts** in your Gmail Drafts folder. **It can never send email.** This is
not a setting you can toggle — the capability does not exist in the codebase. Every
draft the assistant creates waits in your Drafts folder for you to review and send
(or discard) yourself, from Gmail.

### Why OAuth scopes can't enforce this

Google's OAuth scopes cannot express "may draft but may never send." The scope that
lets an app create a draft — `gmail.compose` — *also* permits sending
(`drafts.send` / `messages.send`) at the Google API level. There is no narrower
scope. CakeCRM requests exactly two Gmail scopes, the minimal set for its features:

| Scope | Why |
|---|---|
| `https://www.googleapis.com/auth/gmail.readonly` | search & read threads; read the connected account's address |
| `https://www.googleapis.com/auth/gmail.compose` | create drafts |

It does **not** request `gmail.send` or `gmail.modify`, and does not request the
`openid` / `email` / `profile` identity scopes (the connected address comes from
`users.getProfile`).

### How it *is* enforced — at the tool layer

Because scopes can't guarantee it, the guarantee lives in code:

- The assistant's tool registry exposes exactly **three** Gmail tools:
  `gmail_search`, `gmail_read_thread`, and `gmail_create_draft`.
- **No send or reply operation, executor, or tool definition exists anywhere in
  `backend/`.** The Gmail operations module (`backend/gmail/ops.py`) contains only
  read and `drafts.create` calls; the runtime execution seam
  (`backend/gmail/client.py`) additionally *allow-lists* the four read/draft
  operations, so no other Gmail method can be invoked.
- `gmail_create_draft` is a **write** tool, so in the assistant's default (normal)
  mode it passes through the human-confirmation gate: the draft is not created until
  you approve it, and you see the full recipient/subject/body first.

### The automated guard

`backend/tests/test_gmail_guard.py` fails CI if a send surface is ever introduced —
it scans the runtime source for `messages().send`, `drafts().send`, `/messages/send`,
`send_email`, `reply_to_email`, `gmail.send`, or `gmail.modify`, and asserts the
Gmail tool surface is exactly the three tools above with the correct write flags.

You can run the same proof yourself:

```bash
grep -rnE "messages\(\)\.send|drafts\(\)\.send|/messages/send|send_email|reply_to_email|gmail\.(send|modify)" \
  backend/ --include="*.py" --exclude-dir=tests --exclude-dir=.venv
```

It returns nothing.

## Data handling

- **BYO OAuth app.** You supply your own Google Cloud OAuth client (client ID +
  secret), entered in-app (never as environment variables). The redirect URI to
  register is shown on the Settings → Gmail card
  (`{BACKEND_URL}/api/gmail/oauth/callback`); self-hosters behind a reverse proxy
  must set `BACKEND_URL` to their public URL.
- **Encryption at rest.** The Google client secret and the OAuth access/refresh
  tokens are Fernet-encrypted in Postgres (`enc:v1:…`, with database CHECK
  constraints rejecting any plaintext write). The encryption key resolves from
  `ENCRYPTION_KEY` → OS keychain → a protected local file. Secrets never appear in
  any API response; the OAuth CSRF state is stored only as a SHA-256 hash.
- **Email content and the AI provider.** When you ask the assistant to work with
  your email, the search results, message bodies, addresses, and any draft text are
  sent to whichever AI provider you have configured (Anthropic/OpenAI/Gemini/etc.)
  as part of the conversation, and are stored in the assistant's conversation
  history in your database (replayed to the provider on later turns of the same
  conversation). Gmail bodies and threads are size-capped before they enter that
  context. Treat email content the assistant reads as data shared with your chosen
  AI provider.
- **Message bodies only, never your files.** Gmail stores an unusually large *text
  body* outside the message and behind an attachment id. Reading a thread will pull
  such a body back in, under a hard byte cap, so a long email doesn't silently
  arrive blank. That is the only case in which attachment storage is read: a part
  carrying a **filename** — an actual attached file — is never downloaded, and its
  name, type, and size are all the assistant ever sees. Oversized bodies are
  reported as `[body too large to display]` rather than fetched.
- **Connection integrity.** The Gmail connection carries a generation counter that
  advances whenever the live connection changes (you save new app credentials,
  disconnect, or complete a new sign-in). Anything that starts under one connection
  and finishes later — the OAuth callback, and a draft awaiting your approval —
  is checked against it, so a sign-in you interrupted cannot resurrect a connection
  you just removed, and a draft approved after you switch Google accounts is
  refused instead of landing in the new account.
- **Untrusted content.** Email you receive is untrusted input. If the assistant
  reads email during a turn, any write actions it proposes for the rest of that turn
  are routed through the human-confirmation gate even in "power" mode — so a
  malicious email cannot silently drive the assistant to create a draft or change
  CRM data without your approval.

## Reporting a vulnerability

Please use GitHub's **private vulnerability reporting** on this repository
(Security → Report a vulnerability). Do not open a public issue for security
reports.
