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
  you approve it, and you see the full recipient/subject/body first. It is deliberately
  **not** in the routine tier described below — that tier covers only edits to your own
  CRM records, so anything addressed to another person always shows you the content
  first.
- **The assistant only searches or opens your mail in a conversation with you.** The two
  read tools are offered in interactive chats; they are withheld from the assistant's
  *unattended* runs (the background heartbeat, the proactive digest).
  Such a run may send you at most one notification, and without the read tools it cannot
  pull your messages in order to put them there — so text planted in a CRM
  record cannot turn that notification into a copy of your mail.
  Two limits on that promise, stated plainly rather than glossed over. First, it covers
  the assistant's own reading; the optional **email touch-scan** is a separate,
  deterministic job that does check your inbox on a schedule without you present — no AI,
  fixed rules, and it records only a sender and subject against a contact you already
  have. Second, once the scan has recorded that sender and subject, an unattended run can
  read it back as ordinary CRM history like any other logged activity. So the precise
  guarantee is that an unattended run cannot *fetch* from your mailbox, not that nothing
  mail-derived can reach it. With the scan switched off, no unattended part of CakeCRM
  reads your mail at all.

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

## The sync-bot guarantee: metadata only, and never a push

CakeCRM's CRM features are ported from a private upstream repo (CAKE OS), and a **sync
bot** notifies this repo when upstream CRM code changes. Two properties of that bot are
guarantees rather than settings.

**It can never push.** The bot files issues. Every port becomes an ordinary pull request
that a human reviews and merges. This is structural: the upstream credential is a
fine-grained token holding *Actions: write* on this repository and nothing else, which
cannot push a commit, cannot open a pull request, and cannot even create an issue. The
intake issue is authored by `github-actions[bot]` through the receiving workflow's own
token. The alternative transport (`repository_dispatch`) was rejected specifically because
its credential would have required *Contents: write* — a push-capable token.

**It carries no upstream text.** The wire format is merge **metadata only** — a commit
SHA, a pull request number, a timestamp, and a changed-file list with line counts. There is
no field for a diff, a title, a body, a commit message, or an author, so those cannot cross
even by mistake.

Upstream *paths* do not cross either, which is less obvious but matters more. A path is
only constrained by its leading directory; the filename after it is free text chosen
upstream and could carry a person's or customer's name. So an intake issue names **this
repository's own files** — the counterpart path, and only when that file already exists
here — and reduces everything else to a count.

Enforced in `backend/tests/test_sync_intake.py`, which feeds sentinel-bearing paths through
the renderer and fails CI if any sentinel appears in the output.

To be precise about what that guarantee covers: it is *no verbatim upstream text*. An
upstream repo whose credential had been **stolen** would still choose the numbers it sends,
and numbers can encode a little data. What it could never do is push code, open a pull
request, or put arbitrary chosen prose into this repository. The full contract, including
the residual risks, is in `docs/SYNC.md`.

## Data handling

- **The Gmail connection is install-wide; access to it is admin-only by default.**
  There is exactly one Gmail connection per install — whichever account an admin
  connected. Connecting, replacing and disconnecting have always been admin-only, and
  since issue #194 so is *using* it: the assistant offers the three Gmail tools only to
  an **admin seat**, and a member's assistant is not shown them at all. The enforcement
  is the same hidden-affordance shape as the connected/disconnected gate — the tools are
  not advertised to the model, so there is nothing for a member to ask for.
  **Unattended work never touches the mailbox**, whatever the setting below says: a
  background turn (the heartbeat, the daily digest) has no seat, and the Gmail tools are
  withheld from a turn with no seat. That is on top of the separate exclusion that keeps
  live external reads out of every background allowlist.
  If your team deliberately works one shared inbox, an admin can turn on **"share the
  connected mailbox with all seats"** under Settings → Integrations → Gmail. It is
  **off by default**, it opens the gate to member seats only, and it resets to off
  whenever the connection changes — disconnecting, replacing the OAuth app, or
  connecting a different mailbox — so a newly connected account always starts private.
  Per-user Gmail, where each seat connects their own account, is tracked as issue #189
  and is not built. The read + create-draft-only guarantee above is unaffected by any
  of this: this decides *who is offered* the tools, never what those tools can do.
- **BYO OAuth app.** You supply your own Google Cloud OAuth client (client ID +
  secret), entered in-app (never as environment variables). The redirect URI to
  register is shown on the Settings → Integrations → Gmail card
  (`{BACKEND_URL}/api/gmail/oauth/callback`); self-hosters behind a reverse proxy
  must set `BACKEND_URL` to their public URL.
- **Encryption at rest.** The Google client secret and the OAuth access/refresh
  tokens are Fernet-encrypted in Postgres (`enc:v1:…`, with database CHECK
  constraints rejecting any plaintext write). The encryption key resolves from
  `ENCRYPTION_KEY` → OS keychain → a protected local file (mode 0600 under
  `backend/data/`), generated once on first start and reused thereafter — so on a
  machine with a keychain the key lives there, and otherwise in that file. The JWT
  signing secret takes the same ladder from `JWT_SECRET` **minus the keychain
  step**, so it is only ever an environment variable or that 0600 file: a keychain
  entry is scoped to the OS account, and two installs under one login sharing a
  signing key would accept each other's sessions. Persisting it is what keeps a
  restart from invalidating every session. Secrets never appear in any API
  response; the OAuth CSRF state is stored only as a SHA-256 hash.
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
  arrive blank. That is the only case in which attachment storage is read: an actual
  attached **file** is never downloaded — whether it carries a filename or is merely
  marked `Content-Disposition: attachment` — and its name, type, and size are all the
  assistant ever sees. Oversized bodies, and bodies whose size the message doesn't
  declare, are skipped rather than fetched (the former reported as
  `[body too large to display]`).
- **Connection integrity.** The Gmail connection carries a generation counter that
  advances whenever the live connection changes (you save new app credentials,
  disconnect, or complete a new sign-in). Anything that starts under one connection
  and finishes later — the OAuth callback, and a draft awaiting your approval —
  is checked against it, so a sign-in you interrupted cannot resurrect a connection
  you just removed, and a draft approved after you switch Google accounts is
  refused instead of landing in the new account.
- **Untrusted content.** Email you receive is untrusted input, and so is anything typed
  into the public quick-capture page described below. If the assistant reads either
  during a turn, any write actions it proposes for the rest of that turn are routed
  through the human-confirmation gate in **every** mode — "power", and the routine tier
  that normal mode otherwise runs without asking. The same holds for a conversation
  carrying an uploaded document, and it survives compaction: once a thread has seen
  untrusted content, later turns keep confirming even after the message that carried it
  has aged out. So a malicious email — or an instruction a stranger files in your inbox —
  cannot silently drive the assistant to create a draft or change CRM data without your
  approval.
  The two are marked differently, because a mailbox is external while a todo list is
  yours: an email read fences the whole result, while a captured item is fenced
  **row by row**, so your own todos still read as ordinary records and only the ones a
  stranger could have written are marked as data. A turn that lists your todos and finds
  none of them costs you nothing.

## What the assistant does without asking (the routine tier)

The assistant runs in one of three modes, chosen per conversation: **Read** (it cannot
change anything), **Ask** (the default), and **Auto** (writes run immediately).

In **Ask** mode a tool may be classified **routine**, which means it runs without an
Approve card. A tool qualifies only when all five of these hold:

1. the effect stays in a CakeCRM Postgres record;
2. nobody is notified — no email draft, no push, no Telegram, nothing that fires at someone later;
3. nothing is removed from view — no delete, archive, merge or cancel;
4. it is not a bulk write;
5. nothing leaves your install — no Gmail, no outbound HTTP.

Twenty tools qualify. Sixteen are CRM tools: creating and updating contacts, companies
and deals; moving a deal's stage; marking a deal won or lost; logging an activity;
creating, updating and completing a todo; and setting custom-field values on a contact,
company or deal. The other four are the todo tools GTD todo mode uses in place of three
of those: capturing a todo, updating one, and creating or updating a project.

**Everything else still asks**, and absence of the classification is the deny state — a
tool nobody has classified confirms, so a capability added later is never silently
exempt. Deletes, archives, merges, bulk moves, lead-score recomputation, Gmail drafts,
notifications, memory facts, and any write to `soul.md` or `MEMORY.md` all
keep their Approve card. Bulk todo updates and both todo deletes are in that group too.
Five of the twenty can take a record out of your lists by setting its `status` —
archiving a contact or a company, or dropping a todo or a project; that
particular call keeps its card even though the tool is routine. Finishing something is
not the same as removing it, so marking a todo done or a project completed runs without
asking, and so does filing either one to someday.

Three rules override the tier entirely, in every mode:

- a write to a protected context file always confirms (see below);
- untrusted content in the conversation — an uploaded document, or email the assistant
  read, this turn or an earlier one — makes every write confirm;
- the unattended assistant (heartbeat, proactive nudges) cannot reach any of
  these tools at all: its allowlist is read tools plus a single notification.

## The no-login todo links

CakeCRM has **two unauthenticated todo surfaces**, and they are asymmetric — read the
Default row in the table below before you assume both are off.

Neither depends on which todo mode you are in. They are mounted always and gated only
on their own settings, so switching between Todo-GTD and normal todos does not turn
either one on or off — and because it does not, Settings → Todos shows their controls in
either mode, whether or not either surface is currently configured. Switching to the
simple todo list never hides a link that is still serving, and never hides the control
that restricts one. Those controls are **admin-only**.

They are authorized by an unguessable **secret token in the URL path**, not by a
session — that is what makes them work as a phone bookmark or a home-screen app. A URL
is a credential: anyone you send it to has the access it grants, it will sit in browser
history and may appear in referrer headers or a proxy log. Treat it like a password.

| | Quick capture | Full todo app |
|---|---|---|
| Path | `/capture` or `/capture/{token}` | `/todo` or `/todo/{token}` |
| Grants | **Write-only** — creates one inbox item | **Read and write** on every todo |
| Default | Reachable without a token (harmless: nothing is readable) | **Off entirely** |
| Manage | Settings → Assistant → Todo mode | Settings → Assistant → Todo mode |

- **Capture is genuinely write-only.** It accepts a block of text, files it in your
  inbox, and answers with nothing but the new item's id. There is no read endpoint on
  that surface — a caller cannot list, search or retrieve anything.
- **The full app is the one that matters.** Its token grants the whole todo store,
  read and write. It is off by default, and switching it on mints a secret token in
  the same action rather than publishing your list at a guessable address. You *can*
  clear the token to make it truly public; the Settings card says plainly what that
  means.
- **These tokens reach the todos and nothing else.** The public mount serves only the
  todo/project endpoints. Contacts, deals, settings, the assistant and every other
  part of the CRM stay behind your login.
- **Rotating a token immediately kills the old link**, including any home-screen app
  installed from it.
- **Unknown tokens get a 404, never a 401 or 403** — an uninvited caller learns
  nothing about whether a surface exists. Comparison is constant-time, and a
  non-ASCII probe is a 404 rather than a server error.
- **Abuse protection ships with the feature**: per-IP rate limits on every public
  endpoint, with a separate, stricter budget that only *wrong* tokens consume — so
  guessing costs an attacker 30 tries per five minutes per IP while normal use never
  touches it. Request bodies are size-checked before they are parsed. Every response
  carries `Cache-Control: no-store` and `X-Robots-Tag: noindex, nofollow`.
- **What a stranger can do at worst**, with capture public: add junk to your inbox,
  up to the rate limit. Three controls keep that from becoming more than junk. Text
  captured this way is wrapped in a tamper-proof marker wherever the assistant reads it,
  with the instruction that content inside such a marker is data to read and never a
  command to follow. A conversation that has actually read one of those items confirms
  **every** write for the rest of that thread, so an instruction hidden in captured text
  cannot reach even the routine tier without your approval. And the unattended assistant
  (heartbeat, nudges) is capped by its allowlist at read tools plus a single
  notification — it cannot write to the CRM at all, whatever it reads.
  Be precise about what the marker is and is not: it is an instruction to the model, not
  an enforced boundary, so treat the confirmation gate and the background allowlist as the
  controls that actually hold. Setting a capture token closes the surface to strangers
  outright, and is the right move if the URL has been shared widely.
- **The app keeps link tokens out of its own logs.** Every line CakeCRM writes — the
  request log, error tracebacks, the app's own messages — replaces the secret part of a
  `/todo/…`, `/capture/…` or `/api/todo-web/…` address with `<redacted>`, including on
  the "wrong token" path. In the Docker image the server writes no request log at all
  unless you turn gunicorn's access log on, and that is scrubbed too.
- **A proxy in front of the app keeps its own log, and CakeCRM cannot scrub it.** On
  Railway, the platform's HTTP log records each request's method, full path and query
  string, status and client IP before the request reaches CakeCRM, so a full todo link is
  visible there to anyone with access to your Railway project. The same holds for any
  reverse proxy, load balancer or CDN you put in front of a self-hosted install. Treat
  access to those logs like access to the link, and rotate the token if the log has been
  shared. Moving the secret out of the address, into a cookie set by a one-time landing
  page, would close this; it is a planned follow-up, not something this release does.

## The assistant's self-written identity (`soul.md`)

The assistant keeps its own knowledge in markdown files you can read and edit at
**Settings → Assistant → Assistant memory**. One of them, `soul.md`, is its description of
itself, and the assistant can rewrite it. That file is loaded into the assistant's
system prompt **unfenced** — as instructions rather than as data — because an
identity the model is told to distrust is not an identity at all.

That is a real escalation over anything else the assistant can save, and it is
treated as one. A poisoned `soul.md` would not be one bad record: it would be a
standing instruction replayed on every future conversation, including unattended
background ones, and it would survive deleting the conversation that created it.
Five things bound that risk:

- **Every edit needs your approval.** Writing or deleting `soul.md` or `MEMORY.md`
  always routes through the human-confirmation gate — including in "power" mode, where
  ordinary writes run automatically, and including normal mode's routine tier, which no
  context-file tool may ever join. You see the file and the new content before anything
  is stored.
- **Background turns can never write them.** The unattended assistant (heartbeat,
  proactive nudges) runs under a read-only allowlist, so a prompt
  injection arriving through a CRM record cannot reach these files
  at all.
- **Only the identity file is unfenced.** `MEMORY.md`, topic files and daily notes
  are wrapped in the same tamper-proof data fence as email and uploaded documents,
  so text inside them is never read as instructions. The assistant's working rules,
  confirmation contract and safety instructions are also assembled *after* the soul
  text, so a rewritten soul can add to who the assistant is but cannot override how
  it behaves.
- **Changing them is admin-only, on both paths.** Writing `soul.md` or `MEMORY.md` needs
  an admin seat whether you edit the file on the Memory page or ask the assistant to
  rewrite it. The assistant's personality is already admin-only, and these two files are
  the other half of the same standing instruction, so they are held to the same bar.
  Everything else — topic files and daily notes — stays open to every seat through both
  doors, and *reading* the protected files does too: seeing what the assistant knows is
  the point of the page.

  The two doors are gated separately and the wording differs, so here is each one.
  Issue #194 closed the **form**: `PUT /api/context-files/file/{filename}` refuses a
  non-admin with a 403, and the Memory page renders both files read-only for a member so
  the error is never the first thing they learn. Issue #213 closed the **assistant**: the
  tool that writes these files is still offered to every seat — members write topic files
  and daily notes through it constantly — but it refuses a protected filename when the
  seat that approves the write is not an admin. Both checks run on the same normalized
  name, so asking for `SOUL.MD` is the same request as asking for `soul.md`.

  Why the assistant needed its own gate: the confirmation card above is not a role. It
  stops the write and shows you the new content, but the person who approves it is
  whoever is in that conversation — so before #213 a member could ask for the rewrite and
  then approve their own request. The card and the role gate are independent and both
  still apply: an admin's protected write stops for confirmation exactly as it always
  did.
- **Changes are visible.** Every file on the Memory page shows who last wrote it
  ("Baker" or "You") and when, so an unexpected rewrite is discoverable rather than
  silent. Note the limit on a multi-seat install: `written_by` records only whether a
  human or the assistant wrote the file, not WHICH person, so a colleague's edit also
  reads as "You". It tells you a human changed it, never who.

## Reporting a vulnerability

Please use GitHub's **private vulnerability reporting** on this repository
(Security → Report a vulnerability). Do not open a public issue for security
reports.
