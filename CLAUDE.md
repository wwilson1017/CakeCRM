# CakeCRM — Project Instructions

## What This Is

Free, open-source, self-hostable CRM with a built-in AI sales assistant. Seeded from
Chatty's product shell and agent engine; CRM features ported from the CAKE OS CRM;
assistant capability bar is Casey (CAKE OS's sales agent). **Multi-user since #60**
(accounts, admin/member roles, record ownership; assistant + channel isolation is
Phase B). PostgreSQL, FastAPI, React/Vite.
Deploy targets: `python run.py` locally (Postgres via Docker Compose), Railway
one-click in the cloud (the template provisions a PostgreSQL service).

- **Repo**: `wwilson1017/CakeCRM`, default branch `main`. **Private until launch.**
- **Local convention (Will's machine)**: this repo lives at `~/ai/CakeCRM`.
- **Blueprints**: CAKE OS at `~/ai/cake_os` (CRM features, Casey, auto-issues loop
  conventions), Chatty at `~/ai/chatty` (product shell, providers, agent engine,
  Telegram, Google integration). When a task says "port X", read the source there.
- **Planning docs**: `docs/CRM_OSS_PIVOT.md` and `docs/CAKECRM_ISSUE_SPEC.md` in the
  cake_os repo (branch `claude/chatty-crm-rebranding-a0ffsf` until merged) — the why
  and the what. The issue tracker here is the live spec; issues #1–#25 map to spec
  items S1–S25.

## Product Rules

- **The agent is a feature of the CRM, not the product.** If a capability doesn't
  make the CRM or its assistant better, it doesn't ship. One built-in assistant —
  no multi-agent roster, no training mode, no knowledge-import adapters.
- **The CRM must be fully usable with zero AI keys.** Every AI feature degrades
  gracefully when no provider is configured — hidden affordances, never errors.
- **Gmail is read + draft only, forever.** There is no email-send tool anywhere in
  the codebase, and none may be added. Google scopes can't express "draft but not
  send", so the guarantee is enforced at the tool layer: the registry exposes read
  and create-draft tools only. This is a documented trust guarantee (SECURITY.md).
  Landed #8 as `backend/gmail/` (mounted `/api/gmail`) + `frontend/src/crm/components/GmailCard.tsx`:
  BYO Google OAuth app (client_id/secret + tokens Fernet-encrypted in the
  `gmail_connection` singleton), scopes `gmail.readonly` + `gmail.compose` only, the
  three tools `gmail_search`/`gmail_read_thread`/`gmail_create_draft` collected via
  `gmail.tools.get_gmail_tools()` (defs gated on connection, executors follow),
  `gmail_create_draft` marked `writes:true`. Enforcement artifacts: the guard test
  `backend/tests/test_gmail_guard.py` (CI fails if any send surface appears), a
  runtime op-allow-list in `gmail/client.py`, and `SECURITY.md`. Untrusted email read
  into the assistant taints the turn (power→normal confirmation) to blunt prompt
  injection. A **read-only Gmail touch scan** (#17, `backend/gmail_scan/`) runs as its
  own `gmail_scan` heartbeat job (60s due-check, gated only on Gmail being connected,
  no AI keys needed): it lists recent inbox mail via the *existing* approved
  `list_messages_op` (no new op/scope — still read+draft-only), matches senders to
  contacts by exact case-insensitive email, and logs `email` touches to `activity_log`
  so #16's counts see exchanges nobody logged. Idempotent per Gmail message-id via the
  `gmail_scanned_messages` ledger (one txn per message; audit columns carry NO FK so the
  CRM-reset TRUNCATE still works); a deal is attributed only when the contact has exactly
  one open deal (never fabricated). Senders recurring ≥3 times with no contact raise one
  deduped "create contact?" alert (`source=gmail_touch_scan`).
  **Connection-race hardening** (#43) adds `gmail_connection.connection_generation`, an
  optimistic-lock counter bumped by every mutation that changes WHICH connection is live
  (`save_app_credentials` / `clear_connection` / `save_tokens`) and deliberately NOT by
  `update_access_token` or `mark_broken` (same account; bumping there would invalidate
  in-flight reconnects and every pending draft on each hourly refresh). Two CAS mechanisms
  coexist **by design**, each matching its invariant: generation-CAS guards connection
  *identity* — `claim_oauth_state()` returns the generation (`int | None`, so callers test
  `is None`, never truthiness) and `save_tokens(..., expected_generation)` CASes on it
  (miss → revoke the fresh grant, redirect `reason=conflict`); ciphertext-CAS guards
  *credential material* — `update_access_token` (unchanged) and now `mark_broken(prev_refresh_enc)`.
  A pending `gmail_create_draft` is bound to the connection it was proposed against: the
  engine stamps `gmail_generation` into the **pending-result placeholder** (safe — history's
  status helpers read only `"status"`) via `_pending_placeholder()`, and
  `resolve_confirmation` refuses a stale one via `_binding_conflict()` →
  `gmail.tools.binding_conflict()`, keyed off the hand-maintained
  `engine._CONNECTION_BOUND_WRITE_TOOLS` (same shape as `_UNTRUSTED_SOURCE_TOOLS`);
  `claim_pending_tool` now also returns the pre-claim `content`. Disconnect/app-replace are
  one atomic statement (CTE `SELECT … FOR UPDATE` → clear → `RETURNING` the OLD ciphertext;
  plain `UPDATE … RETURNING` yields post-update values), so the router revokes exactly the
  grant it ended. Read fidelity: a large text body Gmail stored under `attachmentId` is
  recovered inside the **existing** `get_thread_op` (no new `_APPROVED_OPS` entry, no scope
  change) under a 256 KB pre-check that **fails closed on an undeclared `body.size`** (Gmail
  has no ranged read) plus a **thread-scoped** 4-fetch budget (per-message would scale with
  message count, letting a sender shape one read into dozens of round-trips), HTML flattened
  like inline HTML, degrading to `""` on failure and `[body too large to display]` when
  oversize; file attachments are NEVER fetched (guarded on `filename` OR a
  `Content-Disposition: attachment` header — a nameless attachment is still a file).
  Fetching is deferred until after the inline walk and resolves plain-before-HTML, so
  a discarded alternative never spends a budget slot, and the budget is spent
  newest-message-first so long threads don't return the latest replies blank. Note `store.get_row()` swallows
  read errors and returns `{}`, so `gmail.tools._live_generation()` distinguishes
  "unreadable" from generation 0 — reading it as `... or 0` would mint a bogus binding and
  falsely refuse a valid draft. The two-step thread fetch was evaluated and
  **declined** (it doubles common-case calls/latency/quota to bound memory only for rare
  long threads).
  **The HTTP transport is ours since #64, and the issue's premise was wrong about why.**
  It reported that `build("gmail","v1",credentials=creds,…)` left requests able to "block
  indefinitely". Measured, a silent socket already could not: passing `credentials=` hands
  transport construction to the SDK, whose `build_http()` applies
  `socket.getdefaulttimeout()` if set and otherwise `DEFAULT_HTTP_TIMEOUT_SEC = 60`. Be
  precise about what that bought, since the same precision applies to our own 20s: it is a
  **socket-inactivity** timeout, bounding how long one read or connect may sit silent — not
  total request duration, and not DNS. There was no aggregate budget of any kind.
  The real defect was that **aggregate**, because one `call_gmail` fans out *sequentially*
  with a fresh ceiling per request: `list_messages_op` is 1 × `messages.list` + 1 ×
  `messages.get` **per message**, so `gmail_search(max_results=25)` is 26 requests — at one
  60s stall each, **≈26 minutes** parked on one SSE turn (`engine.py` awaits `execute_tool`
  with no `wait_for`), and a `gmail_scan` pass 51 requests ≈ 51 min. A per-request timeout
  alone would not have fixed the reported symptom. Secondary, but the reason a value must be
  *owned* rather than inherited: `build_http()` reads the process-global
  `socket.setdefaulttimeout()` first, so any dependency could silently redefine Gmail's
  timeout in either direction.
  So `build()` now takes **`http=` and never `credentials=`** — the SDK treats them as
  mutually exclusive and raises if given both — carrying `AuthorizedHttp` over a
  `_BudgetHttp(httplib2.Http)` with an explicit `_HTTP_TIMEOUT_SECONDS = 20`. That 20s is a
  **per-socket-op stall detector, not a total-duration cap**, so a large response that keeps
  flowing is never cut off. `_BudgetHttp.request` additionally refuses to **start** a request
  once the call's budget (`_CALL_BUDGET_SECONDS = 90`; `gmail_scan` passes its own
  `_SCAN_CALL_BUDGET = 60`) is spent. **The gate is the INNER http, not a wrapper around
  `AuthorizedHttp`, and that placement is load-bearing**: `AuthorizedHttp` builds its refresh
  transport as `Request(self.http)`, so the token-refresh round-trip is gated too — an outer
  wrapper would let it past entirely — and `build()` still receives a genuine
  `AuthorizedHttp`, so the SDK's `get_credentials_from_http` (universe-domain resolution) and
  every property proxy work with no delegation code. State the guarantee precisely: it covers
  every top-level SDK request and every OAuth refresh, and because it gates request *starts*
  the ceiling is the budget plus one request — a peer trickling bytes forever would defeat it,
  which is out of threat model when the peer is Google's API.
  `call_gmail` resolves an **absolute deadline before its own setup** (store read, decrypt,
  `build()`), not after — that shared epoch is the whole reason `_SCAN_CALL_BUDGET +
  _HTTP_TIMEOUT_SECONDS < _SCAN_HTTP_DEADLINE` means anything, since a budget starting after
  setup would run from a later, unknown instant. A non-finite or non-positive budget is
  refused rather than accepted: **NaN would disable the gate silently**, every comparison
  against it being False.
  A timeout raises **`GmailTimeoutError`, a plain `Exception`** — deliberately not a
  `TimeoutError`/`OSError` subclass, so googleapiclient's `_retry_request` socket-error
  handling can never retry it and multiply the wall clock the budget exists to bound. The
  translation happens **in `_BudgetHttp.request` itself, not in `call_gmail`**: a raw
  `TimeoutError` escaping the transport frame is visible to `_retry_request`, so translating
  one level up would be too late (`call_gmail` keeps a backstop `except TimeoutError` anyway).
  It never means a broken connection: disjoint from `RefreshError`, so it cannot reach
  `store.mark_broken` (verified through the real SDK refresh path, not assumed — google-auth
  wraps only `HttpLib2Error` into `TransportError`, so a stalled socket surfaces raw), and the
  three executors return it **without `needs_reconnect`**.
  **It carries `started`, because "retryable" is not one answer for a write.** `False` = the
  budget refused before the socket was touched, so nothing reached Gmail and a retry is safe;
  `True` = a socket stalled mid-flight, so the outcome is unknown — Gmail may have acted and
  lost only the response. `gmail_create_draft` words its result off that flag rather than
  claiming nothing happened, which would invite a duplicate draft. The OAuth callback keeps
  its revoke-on-any-failure behavior — a deliberate, documented exception, since from its seat
  a timeout is indistinguishable from a broken grant and reconnecting is one click.
  `gmail_scan` **keeps** its daemon-worker + `_inflight` + join-deadline machinery, now as a
  backstop rather than as a workaround for a missing timeout. Its budget sitting inside the
  join deadline (`_SCAN_CALL_BUDGET + _HTTP_TIMEOUT_SECONDS < _SCAN_HTTP_DEADLINE`, pinned by
  test) makes the abandoned-worker leak unreachable for the **common single-stall** case —
  **necessary, not sufficient**, and the machinery stays precisely because of the gap: the
  timeout bounds one socket *operation*, so a request stalling separately on connect, TLS and
  read can still outlast the join, as can a DNS stall or CPU starvation. The docstrings now say
  plainly that a worker abandoned for those reasons may still be unbounded and that `_inflight`
  caps concurrency without guaranteeing recovery.
  `httplib2` and `google-auth-httplib2` moved from incidental transitives to **pinned direct
  dependencies** (the `pillow` precedent — a transitive extra is not a dependency contract).
  All of it is **hermetically tested against the real SDK**, which retires the issue's stated
  reason for deferral ("cannot be verified in the automation environment"): Gmail uses static
  discovery so `build()` makes no network call, `backend/tests/test_gmail_transport.py` drives
  the real `build()` and the real ops with a fake wire installed *below* the gate (never
  monkeypatching the gate under test), and a loopback black-hole socket proves a genuine
  read-hang is bounded. A parity test pins that dropping `credentials=` loses nothing for an
  authorized-user credential (`requires_scopes` is False, so the SDK's scoping step is the
  identity; the JWT branch is service-account-only) — an invariant rather than an argument,
  because a service-account credential would *not* be equivalent.
- **Multi-provider AI** via the `AIProvider` ABC (Anthropic, OpenAI, Gemini, Ollama,
  Together). Never call a provider SDK directly from feature code. Cheap background
  AI work (touch counts, classification) uses the light tier via
  `resolve_tier_model()`. The layer lives in `backend/providers/` (ABC
  `providers/base.py`, factory `providers.get_ai_provider()`, key-based only — no
  OAuth). Provider SDKs (`anthropic`/`openai`/`google-generativeai`/`httpx`) are
  imported **lazily inside methods only, never at module top level**, so a missing
  SDK or absent key never breaks import/startup. Keys are stored **single-tenant,
  admin-global** in Postgres — `ai_providers` (Fernet-encrypted `api_key_enc`),
  `ai_settings` (active provider/model singleton), `ai_model_tiers`
  (overrides/inferred JSONB). Keys enter in-app via `POST /api/providers/{provider}/
  connect-key` (validated live); `GET /api/setup/status` returns `ai_ready` — the
  degradation gate the CRM UI keys AI affordances off. The chat-loop surface
  (`stream_turn`/`add_tool_results`/`build_tool_turn`) is consumed by the built-in
  assistant engine (`backend/assistant/`, landed #4): an SSE streaming tool loop
  with write-tool confirmation modes and file uploads, mounted at `/api/assistant`
  and gated off `ai_ready`.
  **The assistant is named Baker and that name is a brand, not a setting** (#71):
  `identity.NAME` is the only source (the old `DEFAULT_NAME` spelling is gone — a
  "default" implies something may override it), `get_identity()` does not select the
  `assistant_identity.name` column at all, `update_identity()` has no `name` parameter,
  and `build_system_prompt` interpolates `{name}` from the constant rather than from its
  argument — that last one is what makes the brand unrenameable rather than merely
  un-editable through the UI, since the prompt is the one seam where the name reaches
  the model. Interpolation alone is **not** sufficient, though, and that is the correction
  the Codex stage forced: `personality` is free text an admin writes and `soul.md` is free
  text the assistant writes, and either can rename the assistant just by spelling a name
  out ("You are Ace") — which a pre-#71 install that renamed its assistant very likely
  still does. So the brand rides the same lever every other immutable contract uses:
  `identity.NAME_NOTE` is a static block placed **after personality and soul and before
  `SALES_GUIDE`**, making it the first thing neither text can override. The ordering is
  the mechanism, so a test asserts the *positions*, not merely the presence.
  The **personality stays fully editable**. `IdentityUpdateRequest` dropped
  `name`, so a stale client still sending it has the field ignored (Pydantic's default),
  not 422'd — rejecting would break the old UI for no gain, while accepting would be the
  bug. The column is deliberately **not dropped**: a pre-#71 binary still runs
  `SELECT name, personality`, so dropping it would make a rollback a *dead* assistant
  rather than a misnamed one — the same call `auth_credential` got. A one-shot migration
  resets every row to 'Baker' so the stored value agrees with the code even on that path.
  The assistant's sales working practices live in
  `identity.SALES_GUIDE` — a **static** constant appended alongside
  `CONFIRMATION_NOTE`/`MEMORY_NOTE`, deliberately NOT inside `DEFAULT_PERSONALITY`,
  because a user-written personality replaces that string wholesale and would silently
  switch off every CRM discipline with it. The assistant's **second execution mode** (landed #6,
  `backend/assistant/background.py`) is a non-SSE `run_background_turn` for
  autonomous work (the heartbeat + reminder firing): it reuses the same
  `ToolRegistry`/`build_tool_turn` loop but, having no human to confirm writes,
  runs under a **server-enforced tool allowlist of READ tools + `notify_user` only**
  (no CRM writes at all — enforced at both advertisement and execution) plus a
  `WRITE_BUDGET_BACKGROUND`, with untrusted reminder/CRM text kept in the user
  message, never the system prompt. So a prompt injection via reminder/CRM content
  can at worst send one notification, never create/log/update/delete a record.
  **Since #22 that ceiling is unchanged but the on-ramp is wider, deliberately:** the
  new read tools put raw per-record free text in front of the unattended turn for the
  first time — `crm_scan_gaps` returns `crm_field_provenance.value_snapshot` verbatim
  and `crm_find_duplicates` returns deal titles / contact names, where the earlier
  background-callable reads (`crm_dashboard`, `crm_analytics`) exposed only structured
  aggregates. Accepted because the blast radius is still exactly one `notify_user` and
  the alternative — a second, narrower payload shape per tool for background turns —
  buys nothing against a ceiling that already holds. Mitigated in
  `heartbeat.service._heartbeat_prompt`, which states plainly that everything a CRM
  tool returns is DATA the user or a third party typed, never instructions. Any future
  background-callable read should assume its payload can carry hostile text.
  **Since #114 the allowlist is no longer derived from `writes` alone**, because
  `writes:False` means "changes nothing", not "safe unattended": `background_allowlist()`
  now subtracts `background.BACKGROUND_EXCLUDED_TOOLS`, which IS
  `delimiters.UNTRUSTED_SOURCE_TOOLS` (the live external-account reads — today
  `gmail_search`/`gmail_read_thread`) rather than a second hand-maintained list. For a
  future *Gmail* reader that is automatic: `test_gmail_guard`'s existing pin fails CI
  until the new tool is added to that set, and adding it excludes it here with no second
  edit. **It is NOT automatic for a future non-Gmail external read** (some other
  connector): the set stays a reviewed denylist, so such a tool is background-callable
  until someone adds it. Deny-by-default would need per-tool background-safety metadata
  on every def — a real design change, deliberately not built here; the rule for now is
  that adding a connection-gated read means adding it to `UNTRUSTED_SOURCE_TOOLS`. The one-notification ceiling had held
  *mechanically* while still being an exfiltration channel: the interactive engine answers
  those reads with the power→normal taint, but an unattended turn has no analogue — nobody
  reads the fence — so injected reminder/CRM text could steer it `gmail_search` →
  `gmail_read_thread` → private mail inside the one permitted `notify_user` (web push +
  Telegram). `_run_turn` ALSO clamps the caller-supplied `allowed_tools` by the same
  subtraction, one line covering both enforcement points (advertisement and execution both
  read that variable), so the exclusion is a property of the background *mode*, not of one
  builder. It is deliberately narrow: the wider "reads + `notify_user`" ceiling is still
  the CALLER's declaration, and a caller-supplied write still executes — which is what
  `test_write_executes_without_confirmation` pins and what the write budget exists to
  bound. Intersecting `_run_turn` with `background_allowlist()` to make that whole ceiling
  mode-enforced was raised twice in review and **declined here**: it changes #6's designed
  contract (the mode exists to let a caller permit a write) and is a decision for its own
  issue, not a side effect of #114. What holds it today is that all three callers use the
  builder, each pinned by a test — `test_heartbeat`'s two (added with #114) and
  `test_proactive_service`'s. **Scope, stated precisely:** this
  removes LIVE mailbox access only. Sender + subject that #17's deterministic `gmail_scan`
  already wrote into `activity_log` stay readable through ordinary CRM reads
  (`crm_get_activity_log`, `crm_dashboard`) — that is CRM data by design, and out of scope
  per the issue's gate decision. So the accurate claim is "a background turn cannot
  initiate a Gmail search or thread read", never "cannot see anything mail-derived".
  Context-file reads stay background-callable (they are Baker's own notes — the
  `_RECORDED_CONTEXT_MARKER` call), and `gmail_scan` itself is untouched: it never goes
  through the assistant registry.
  The assistant has a **long-term memory + nightly dreaming**
  (landed #5, `backend/memory/` + `backend/dreaming/`, **pure-algorithmic — no AI
  calls**): temporal facts in Postgres (`memory_facts`, generated `tsvector` + GIN,
  searched via an OR-of-keywords `to_tsquery('simple', …)` tokenizer) with four `memory_*` tools carrying
  the `writes` flag; relevant facts are injected into the **volatile** half of the
  system prompt each turn (never the cached static half) with once-per-hour retrieval
  tracking. **Dreaming** adapts chatty's file-archival to the single-assistant layout —
  the unit is the fact, so it soft-archives dormant non-tier-1 facts (`archived_at`, never
  deleting; `decision`/`preference` are never archived), scored purely from usage signals
  (14-day-half-life recency, recency-gated frequency, age, confidence; active/stale/dormant
  at 0.4/0.1) and audited in `dreaming_runs`. Its entrypoint
  `dreaming.processor.run_dreaming_if_due()` is scheduler-agnostic (advisory-lock +
  due-guard) and is driven by **#6's 60s `reminder_tick`** (via
  `heartbeat.service._maybe_run_dreaming`) — #5's interim lifespan task was absorbed
  when #6 landed, exactly as planned.
  **Context files** (#72 Phase 1+2, `backend/context_files/` + `frontend/src/crm/MemoryPage.tsx`)
  restore chatty's *other* memory unit, which #5 deliberately skipped: `soul.md` (the
  assistant's self-written identity), `MEMORY.md` (its living snapshot), `topics/<name>.md`
  and `daily/YYYY-MM-DD.md`, all rows in `assistant_context_files`. So the #5 note above
  that "dreaming's unit is the fact because CakeCRM has no context-file store" now states
  a *history*, not a constraint — file-dreaming is #72 Phase 4 and nothing sets
  `archived_at` yet, though every read already carries the live predicate so that phase is
  purely additive. `kind` and `is_protected` are **GENERATED columns** derived from
  `filename` (a code-only convention drifts; a generated column cannot), and the filename
  grammar is the single normalizer — `soul.md` | `MEMORY.md` | `topics/<slug>.md` |
  `daily/<ISO date>.md`, case-canonicalized, NFC-normalized, with a bare `x.md` normalized
  to `topics/x.md` rather than rejected. Seven keyless tools ride
  `context_files.tools.get_context_file_tools()`.
  **The prompt split is by TRUST, not by file, and that is a correction the plan review
  forced:** `soul.md` loads UNFENCED into the **static** half (fencing an identity as
  untrusted data defeats it), while `MEMORY.md` + the topic/daily manifests load
  nonce-fenced as `<recorded_context>` in the **volatile** half. They cannot go in static
  even though they change rarely, because `delimiters._wrap` mints fresh entropy per call —
  a fence in the cached prefix would re-key Anthropic's prompt cache *every turn*. The real
  invariant is **no per-turn entropy in static**, not "static never changes". Ordering is
  load-bearing too: static is `personality → soul → SALES_GUIDE → CONFIRMATION_NOTE →
  MEMORY_NOTE → CONTEXT_FILES_NOTE → safety` (since #71, `NAME_NOTE` sits between soul and
  SALES_GUIDE), so a self-rewritten soul can add to who Baker
  is but never override a tool or security contract — or its own name. `DEFAULT_SOUL` is the
  blank-means-default fallback constant (same pattern as `personality`); the migration seeds
  **empty** content so a later boot can never overwrite an edited soul, and the constant is
  scanned by `test_prompt_genericization.py`.
  Security: writes carry `writes:true` (so `background_allowlist()` excludes them — asserted
  explicitly, not left to the derivation), and a write to a **protected** file additionally
  **always confirms, power mode included**, via `context_files.tools.requires_confirmation()`
  consulted from the engine's gate — `writes:true` alone is NOT enough there, because a
  poisoned `soul.md` is a permanent system instruction, not one bad record. That hook
  **fails closed** on a missing/unparseable filename. `read_context_file`/`read_daily_note`
  results are fenced but deliberately do NOT taint the turn (`_UNTRUSTED_SOURCE_TOOLS`
  encodes *third-party* origin; Baker reading its own notes must not kill power mode) — the
  same call #5 made for facts — while `_NON_USER_MARKERS` DOES exclude the fence from
  `_last_user_text`, so file content can never choose which memories surface. Note the four
  context READS are background-callable, widening the hostile-text on-ramp exactly as #22's
  reads did; the ceiling is still one `notify_user`.
  REST is two routers — `/api/context-files` (files; `{filename:path}`, since topic names
  contain `/`) and `/api/memory` (facts + dreaming runs) — both keyless, both behind
  `get_current_user`. The editor carries an `updated_at` precondition returning **409** on a
  stale save, because the single-statement `append_daily_note` upsert guarantees
  append-vs-append only; a whole-file overwrite racing an append is still last-write-wins.
  **A CONFIRMED tool write carries that same precondition**, and the confirmation gate is
  the reason it needs one rather than a substitute for it: the gate is what opens a
  human-length gap between composing an overwrite and running it, and a protected file
  always waits. So `engine._VERSION_BOUND_WRITE_TOOLS` stamps the row version into the
  pending placeholder via `context_files.tools.pending_binding()` and `_with_version_binding()`
  injects it as `expected_updated_at` on approval — the server reads the version because a
  model asked to echo its own token could silently opt out of the guard. Unlike the Gmail
  connection binding this is **not** a pre-check: the kwarg rides into `write_file`'s
  in-UPDATE comparison, so no check-then-write window remains. It **fails open** (an
  unreadable version binds nothing, exactly as `gmail.tools._live_generation` does), and a
  stale write returns a *model-facing* conflict telling Baker to re-read and re-apply — the
  service's own message tells a browser to reload, which would just make the model retry the
  same stale overwrite. An unconfirmed power-mode write binds nothing, having no gap; a file
  that does not exist yet binds nothing either (documented simplification — needs an
  expect-absent insert the service has no primitive for). The Memory page's editor likewise
  confirms before a file switch discards an unsaved draft.
  `memory.service.delete_fact()` is a **human-only** hard purge with no agent tool — the
  assistant retires a fact with `invalidate_fact`, which preserves the temporal record.
  **Conversation compaction is #72 Phase 3** (`backend/assistant/compaction.py`), and it
  closes the one place the assembler was unbounded: it capped each ROW and nothing capped
  the TOTAL, so a long thread grew until the provider refused the whole request. Past ~70%
  of the window the aged MIDDLE is replaced by a light-tier gist and a boundary `seq` is
  stored; the assembler keeps the opening exchange verbatim, folds the gist onto the first
  RETAINED user turn, and keeps the tail verbatim. Folding onto a real user row rather than
  inserting a synthetic turn is what keeps role alternation valid on every provider.
  Rows are **never deleted** — compaction changes only what is ASSEMBLED, so the history UI
  still shows everything and clearing two columns restores full context. Keyless is not a
  degradation path but an unreachable one: chat is gated on `ai_ready`, so a keyless install
  never reaches compaction; any other failure (no light tier, a timeout, an empty answer)
  returns False and the turn assembles exactly as it did before.
  **The fence is minted ONCE, at write time, and stored wrapped — this is load-bearing, not
  a style choice.** `anthropic_provider` applies `cache_control` to the last user message
  for CONVERSATION-PREFIX caching, and the gist rides an EARLY user turn, so re-wrapping per
  assembly would mint a fresh nonce and re-key that cache every single turn — the same
  defect the Phase 1 review caught for the static system prompt, one layer down. Capping
  therefore happens on the raw text BEFORE wrapping (truncating a wrapped block severs its
  closing tag), and `test_the_assembled_gist_is_byte_identical_across_turns` pins it.
  Chatty regex-scrubs a fixed `</conversation_summary>` instead; this repo already replaced
  that approach once, so the gist is nonce-fenced like everything else and so is every tool
  result the summarizer READS.
  **Two engine invariants read the assembled messages, and compaction removes rows, so both
  needed work — this is the part to preserve.** (1) `_context_has_untrusted_upload` decides
  the power→normal write downgrade by scanning the assembled context for untrusted fences;
  once a Gmail read or an upload ages out, that scan comes up clean and the mitigation would
  silently stop firing. So `assistant_conversations.untrusted_content_seen` records it
  durably, written at **INGRESS** — the moment the content arrives — because an assistant
  row is saved with its `tool_calls` and its `tool_results` merged afterwards, and a
  compaction pass reading between the two would see no marker and record nothing.
  Compaction's own scan stays as the backfill path for rows written before this shipped and
  as a backstop if the ingress write failed; `is_conversation_tainted` **fails closed**.
  (2) `_last_user_text` picks the memory-retrieval keywords and refuses any message carrying
  an untrusted marker — but when a thread is dominated by old content the boundary falls
  back to "gist everything but the last turn", so the gist lands on the CURRENT message.
  Rejecting it would silently match memory on the conversation's FIRST message, so the
  nonce-delimited block is stripped and the typed remainder kept.
  Four smaller rules, each a real defect first: a row whose tool work is **unfinished**
  (results not merged, or a write awaiting approval) is never gisted, because summarizing it
  produces a gist describing work whose outcome is not in it while the results land behind
  the boundary — and that scan starts at the CURRENT boundary, never at row 0, because the
  preserved head and the already-gisted span cannot enter the middle anyway, so scanning
  from 0 pinned the ceiling inside the head whenever the opening exchange held an abandoned
  approval and, since that row never changes, disabled compaction for the life of the
  thread; the middle transcript is capped **per ROW as well as in total**
  (`_MAX_ROW_CHARS` <= `_MAX_MIDDLE_CHARS`, which is the inequality that lets the newest row
  obey the cap like every other rather than being waved through on an empty budget) —
  a chat message has no length limit and an upload row carries several capped files, so one
  row could put the summarizer prompt past the LIGHT tier's own window, and a summarizer
  that refuses writes nothing, so every later turn rebuilt the identical oversized request
  while the thread grew; every clip lands on RAW text before wrapping, so no cut can sever a
  fence; an oversized row budgets the gist and the request **separately**, dropping
  the gist WHOLE past half the row rather than truncating what the user just typed; and the
  fast path compares the stored reading against the **target**, not the trigger, because
  that reading describes the PREVIOUS model input and counts neither the user row this turn
  saved nor the assistant text that answered the one before. That reading is also written
  as the **GREATER** of old and new, because two turns racing on one conversation finish out
  of order and the slow one's stale-low number landing last would send the next turn down
  the fast path and skip compaction — and once the real context is past the provider's
  limit every turn fails, none records a corrective reading, and the thread stays stuck
  there. `set_compaction` **clears** the reading, which is the one moment a decrease is
  real and is what lets the write otherwise keep the greater of the two. **That clear is
  necessary and NOT sufficient**, which is worth stating because it reads as if it were: it
  settles the stored value but cannot reach a turn already in flight. Turn A assembles a
  150k context, turn B compacts and NULLs the meter, then A finishes and `GREATEST` restores
  150k — a number describing rows that are no longer assembled, which the next turn takes as
  current fullness and sheds, gisting recent rows the thread still had room for. So every
  reading is **versioned by the boundary it was assembled against**
  (`save_message(context_boundary_seq=)`), and one produced under an older boundary is
  dropped rather than defended. No new column: `compaction_first_kept_seq` only ever moves
  forward (`set_compaction` is a compare-and-set), so it already IS the generation counter.
  The engine reads that boundary **before** assembling, deliberately — a compaction landing
  in the gap then makes the stamp OLDER than the context and costs one meter reading, where
  reading it after would make the stamp NEWER and wave through exactly the stale reading the
  stamp exists to catch. Only Anthropic reports usage or
  a context window today, so the other five providers run on the chars/4 estimate against
  `DEFAULT_BUDGET_TOKENS` — bounded, but a real window smaller than that could still refuse
  a request before the trigger fires. Giving them real windows is its own issue.
  Phases 4 (observer/extractor/commitments-as-tasks/file-dreaming) and 5
  (optional embeddings re-ranker) are follow-ups; #72 stays open as the tracker.
  Assistant chat history and memory are still install-wide — Phase B of #60.
- **Accounts, roles and record ownership** (#60 Phase A) — the install has real
  `users` (email + bcrypt + `admin`/`member` + `is_active` + per-user `token_epoch`),
  and the shared `AUTH_PASSWORD` login is gone. Authorization has exactly **two**
  enforcement points: `core.auth.get_current_user` (authentication AND liveness — one
  indexed PK lookup per request, so a deactivation, a demotion or a password change
  bites on the *next* request, not at token expiry) and `require_admin`, applied
  per-route to an enumerated set that `backend/tests/test_route_authz.py` pins **in
  both directions** — adding an ungated admin route fails CI, and so does removing a
  gate. The JWT carries `sub` (user id) and `pwd_epoch` and deliberately **not**
  `role`: a role in a token can only ever be stale. `get_current_user` is a sync
  `def` on purpose — it does blocking psycopg2 I/O, and an `async` dependency would
  run it on the event loop and stall every SSE stream.
  **Ownership is NOT access control.** `owner_id` on contacts/companies/deals/tasks
  is an assignment, a filter and an analytics dimension; any member can read, edit,
  delete and reassign any record (no per-object ACLs — a product decision, stated
  rather than implied). It is nullable forever: `NULL` = unassigned, a real state the
  Gmail scan, the assistant and #61's importer all legitimately produce. Owner
  filters live in the **shared** WHERE builders so they reach the COUNT and the page
  query together — a one-sided filter doesn't fail, it silently reports a total that
  disagrees with the rows. The pipeline board deliberately has **no** server-side
  owner param: every other #21 facet is client-side over the complete corpus, and
  `get_pipeline` returns deals alongside a separately-computed `stage_summary` that a
  one-sided filter would put out of step with the cards. #59 made the FETCH paged
  without weakening that — the board still assembles every live deal client-side, so a
  server-side owner filter would still be a second filtering model, not a page of one.
  **Ownership and authorship are different columns** (the cake_os #1454/#1532
  lesson): `activity_log.actor_id` and `crm_chatter.author_id` record who DID the
  work, so per-rep activity credits a rep for work on a colleague's record. Only the
  human REST paths stamp them — the assistant's tool executors don't thread identity
  in Phase A, so their writes stay NULL and roll up as "Unattributed". Phase A
  therefore **undercounts** assistant-delegated work but never **misattributes** it.
  The per-rep query excludes `provenance_service.confirm`'s housekeeping notes and
  `merge_deals`' copies (the copies leave the originals on the archived source, so
  both read `archived = 0` and one note would count twice).
  **Bootstrap** (`users/bootstrap.py`, lifespan, after migrations, before
  `apply_password_reset_env`) seeds the first admin when `users` is empty and, on an
  upgrade, **carries the bcrypt hash out of the pre-#60 `auth_credential` singleton**
  — an owner who changed their password in-app doesn't know `AUTH_PASSWORD` any more
  (#78 made it inert), so re-deriving from the env var would lock them out. It also
  claims the orphaned `totp_config`/`trusted_devices` rows and backfills `owner_id`,
  all in one transaction under an advisory lock. It does **not** clear
  `auth_credential`: pre-#60 code reads a NULL hash as permission to fall back to
  `AUTH_PASSWORD`, so clearing it would let a rolled-back build accept the superseded
  env password. That table is vestigial and a later release drops it.
  `AUTH_PASSWORD_RESET` now also re-activates the target admin and **clears their
  2FA** — a password-only rescue cannot help an operator who lost their
  authenticator. The same reasoning gives `POST /api/users/{id}/password` an **opt-in**
  `clear_two_factor`, which is the only recovery for a MEMBER who lost both their
  authenticator and their backup codes; it is opt-in rather than automatic because it
  genuinely weakens that account. That route also **refuses a self-reset** — it skips
  the current-password and 2FA checks `/api/auth/change-password` enforces, which is
  right for helping a colleague and wrong as a second, weaker self-service path. 2FA
  itself is re-keyed per user, and enabling or disabling it wipes that user's trusted
  devices in the SAME transaction — as does a password change, which is one
  transaction covering the hash, the epoch bump and the revocation.
  **The upgrade is a one-way door.** The migration drops `totp_config.id`, so a
  pre-#60 binary cannot complete a login for a 2FA account afterwards; rolling back
  means restoring a `pg_dump`, and the README says so. Preserving `auth_credential`
  buys only that an old process would still check the credential the user chose
  rather than the stale `AUTH_PASSWORD` — it does not make the schema reversible.
  Login is rate-limited **per account and per IP** (10/5min and 50/5min): a single
  per-IP bucket is a denial of service on your own team, since one office shares a
  NAT address. `login`, `/api/me`, change-password and the `/api/users` handlers are
  sync `def`s for the same reason `get_current_user` is — they do blocking psycopg2
  and bcrypt work and would otherwise run it on the event loop.
  In the UI, which Settings cards a member sees is decided in ONE place,
  `crm/settingsSections.ts` (#103): each card declares `adminOnly`, and a section is
  visible iff one of its cards is — so an all-admin group (Workspace, Integrations)
  can never render as an empty section or a dead tab for a member. Team also keeps
  its internal `return null` as defence in depth. Members see exactly Notifications,
  Change password and Assistant memory. **Task mode is admin-only since #102**:
  `task_mode` is a `crm_meta` singleton, so one member flipping it changes everyone's
  task surface, and the card's no-login section can mint an unauthenticated read+write
  link to the whole todo store whose lifetime is **not** tied to the account that
  created it (deactivating that user revokes their JWT via `token_epoch`/`is_active`,
  not the URL). `/api/crm/task-mode` and both `/api/crm/todo-surfaces` methods are
  `require_admin`, pinned in `test_route_authz.ADMIN_ONLY`. #102 gated that at its
  call site; this page **replaced** that call site, so the gate is now the registry's
  `adminOnly` flag — same semantics, one place. The UI partition mirrors the server's
  rather than inventing one, and `settingsSections.test.ts` pins it in both directions.
  The **assistant drawer's identity panel** obeys the
  same rule (#106, `frontend/src/assistant/IdentitySettings.tsx`): `PUT
  /api/assistant/identity` is `require_admin` while the GET is member-legal, so members
  see the personality **read-only** with no Save rather than a control that can only
  403 — "don't offer what can only 403" is about controls, not cards, and hiding the
  panel outright would deny a member the text governing an assistant every seat gets.
  That file is the whole exposure: the rest of `frontend/src/assistant/` calls only
  `/chat`, `/confirm` and `/conversations*`, none of which appear in
  `test_route_authz.ADMIN_ONLY`.
  The registry is **card-granular**, and one card straddles that line: Notifications'
  Web Push half configures this browser (everyone's), while its "Daily digest and
  nudges" half writes install state through the `require_admin`
  `POST /api/heartbeat/proactive` — so that block carries its own `isAdmin` gate
  *inside* a member-visible card (pinned in `SettingsPage.test.tsx`). Any future card
  mixing personal and install controls needs the same second gate: the registry cannot
  express it, and "don't offer what can only 403" is a rule about controls, not cards.
  **Still install-wide, deliberately (Phase B):** assistant chat history and memory,
  the Gmail connection, the Telegram binding, reminders, notifications and alerts.
  Every active seat gets the assistant (Will's §15 ruling — no temporary admin gate
  someone has to remember to remove), so a member can have it read the admin's
  connected mailbox. `GET /api/telegram/status` redacts the link code for members,
  since that code claims the one binding and would otherwise defeat the admin gate on
  connect/disconnect. The dead `MULTI_USER_ENABLED` flag was deleted — grep found
  only its own definition and the docstring advertising it.
- **One database: PostgreSQL, and it's mandatory** — the backend refuses to start
  without `DATABASE_URL` (decided 2026-07-18; single engine, ready for multi-user
  growth). Locally `docker compose up -d`; on Railway the template provisions
  Postgres and injects `DATABASE_URL`. No Redis or other external services.
  **Where a FILE can live, stated once because two places in this repo used to imply
  different answers:** the Railway container filesystem is ephemeral and is replaced on
  every redeploy — EXCEPT `/app/backend/data`, which `railway.json` requires as a mounted
  volume (`requiredMountPath`), so a deploy without one does not start. That is the
  directory `backend/data/` resolves to (Dockerfile `WORKDIR /app` + `COPY backend/
  ./backend/`), and it is why the branding logo and the `.encryption-key` fallback persist
  today. So "Railway filesystems are ephemeral" (the `assistant_context_files` migration
  header) and "the volume is real" (#57's) are both true and are not in conflict. Anything
  written OUTSIDE `backend/data/` is gone on the next deploy. New durable state should
  still default to a Postgres row — one store, one transaction, one `pg_dump` — and #57
  put attachment bytes there for exactly that reason even though the volume would have
  held them.
  Required env vars: `AUTH_PASSWORD` + `DATABASE_URL`; `ADMIN_EMAIL`/`ADMIN_NAME`
  seed the first admin's identity; `JWT_SECRET` and `ENCRYPTION_KEY` auto-generate. **The login credential is DB-backed** (#78): the
  `auth_credential` singleton holds a bcrypt hash the logged-in user changes from
  `/crm/settings`, and `core.auth.verify_password()` resolves DB-hash-first, falling
  back to `AUTH_PASSWORD` only while that hash IS NULL — so the env var is a
  *bootstrap* value that goes inert once the user sets their own password, and can
  never silently override it on a later boot. Every credential check in the app routes
  through that one function (login, the three 2FA confirmation endpoints, and
  change-password's pre-check), so the resolution order has exactly one definition.
  `POST /api/auth/change-password` verifies the current password and writes the new
  hash in ONE `SELECT … FOR UPDATE` transaction (`set_password`); the migration
  **seeds** the singleton row with a NULL hash so that lock always has a row to hold
  — locking an absent row is a no-op, which would let two concurrent first-time
  changes both pass. The endpoint also runs a **non-consuming `verify_password`
  pre-check before the 2FA code**, because verifying a code spends it (`verify_totp_code`
  burns the timeslot, `consume_backup_code` destroys a single-use code) and a typo in the
  current-password field must not cost the user a recovery code; `set_password`'s locked
  re-check stays authoritative. It answers a wrong current password with **400, not 401**,
  because the frontend `api()` wrapper treats every 401 as an expired session and
  ejects the user to `/login`. On success it revokes trusted 2FA devices and returns
  a fresh token. **Changing the password ends every other session immediately**:
  `auth_credential.token_epoch` is bumped in the *same statement* as the hash and
  stamped into every JWT as `pwd_epoch` (injected centrally in `create_access_token`,
  so all four mint sites carry it), and `get_current_user` rejects a token whose epoch
  is stale. The epoch is cached in-process — the deploy pins `gunicorn --workers 1` and
  the **happy path does zero DB reads**; it is loaded once in the lifespan. A *mismatch*
  re-reads before rejecting, so a stale cache (a multi-worker fork) self-heals instead of
  spuriously signing valid sessions out. A token predating the feature has no claim and
  reads as epoch 0 — deploying this signs nobody out; the first password change does.
  `AUTH_PASSWORD_RESET` bumps the epoch too (a rescue must end the sessions that may have
  caused the lockout). `AUTH_PASSWORD_RESET` is the operator's
  recovery lever, consumed in the lifespan right after `run_migrations()`: it
  overwrites the stored hash on **every** boot while set (a lever that disarms itself
  can only be pulled once) and logs a loud warning to remove it. Login **fails
  closed** — an unreadable credential is a 503, never a fallback to the env var.
  Schema is owned by `backend/migrations/*.sql`,
  applied automatically at startup in lexicographic order — name migrations
  `YYYYMMDDHHMMSS_<name>.sql` (use `date +%Y%m%d%H%M%S`), never sequential
  prefixes. Access Postgres through `core/postgres.py` helpers
  (`pg_fetchall`/`pg_fetchone`/`pg_execute`/`get_connection`/`row_to_dict`).
- **The CRM is first-class core** (`backend/crm/`, mounted at `/api/crm`; frontend
  `frontend/src/crm/` + `frontend/src/shared/`) — always-on, no enable flag. Ported
  from chatty's `crm_lite` and translated to Postgres (companies/contacts/deals/tasks/
  activity_log, plus `crm_chatter` — editable/archivable notes threads on deals and
  contacts, landed #15; query idioms follow the matching `cake_os/backend/apps/crm`
  services so later feature ports diff cleanly). Companies are a first-class entity (#13):
  contacts/deals carry a nullable `company_id` FK and a company detail page rolls up
  the linked contacts/deals/activity. **Company link coherence** (#35) makes the *link*
  the single source of truth: every ingestion path (CSV import, smart-import confirm,
  `crm_create_contact`/`crm_update_contact`, the REST routes) resolves free-text company
  names and auto-creates the company through one shared primitive,
  `crm.service.resolve_or_create_company_ids()` — 2 queries per batch regardless of row count
  (the bulk loops pre-resolve and pass `company_id`, and degrade to per-row resolution
  if the batch call fails, preserving per-row error isolation); it is also the helper
  the #61 importer consumes. An explicit `company_id` always wins — including an
  explicit `null` on *update*, which means unlink (on *create* there is nothing to
  unlink and the REST route can't express absent-vs-null, so text always links there).
  Contact list/search LEFT JOIN `companies` and expose `company_name`; the UI renders
  `company_name || company` and search matches the joined name, so a linked contact is
  findable/displayable even with empty or stale legacy text. The legacy
  `contacts.company` column stays in place, non-authoritative (**the freetext↔link merge
  landed #126 in `ContactForm`; the COLUMN stays**); a second one-shot backfill migration
  (`20260816203810_company_link_backfill.sql`) repairs installs that imported between
  #13 and #35 by linking contacts that are still `company_id IS NULL` with matching
  text. Unlike #13's it deliberately does **not** inherit company onto deals: `NULL`
  no longer unambiguously means "never set" (a user can clear a deal's company), and
  no ingestion path creates deals anyway.
  **Inline quick-create** (#123) makes the deal form the first surface that can create a
  linked record without leaving it: `frontend/src/crm/components/RecordCombobox.tsx` is a
  generic server-searching picker with a `Create "<name>"…` row, and `DealForm` uses two of
  them for Contact and Company. It **replaces** the "fetch the first 200 rows into a
  `<select>`" pattern, and with it the capped-page hazard each form used to hand-patch: an
  out-of-page link had no `<option>`, so the control rendered blank and read as *none* —
  `DealForm` carried an append guard for the deal's own company and never had one for its
  contact. The selected record's LABEL is now a prop seeded from `deal.contact_name` /
  `deal.company_name`, so the display is correct by construction and both guards are gone.
  **That makes the joined name a wire-format requirement, not a nicety:** any query whose
  rows can reach this form must carry BOTH names or the picker renders empty for a link that
  exists, reproducing the bug it replaced. `get_deal` and `get_pipeline` already did;
  `get_dashboard_stats`' `top_deals` did **not**, and the dashboard hands its rows straight
  to the deal sheet and on to `DealForm` — so #123 added the `companies` join there and
  `test_top_deals_joins_the_company_name` pins it. The picker is deliberately NOT built on
  `shared/search`: `SearchInput` has no listbox, and that module documents itself (in
  `shared/search/index.ts`) as client-side-only over an already-loaded dataset, explicitly
  disclaiming server-paginated contacts and companies. **Three corrections to that issue's own pointers, each verified:**
  the search param is **`q`**, not the `?search=` it names (which `list_contacts` ignores, so
  building against it ships a picker that always shows the unfiltered first page); `DealCreate`
  has no free-text `company` field the way `ContactCreate` does, so a typed company must be
  resolved to an id before the deal is saved; and `POST /companies` does **not** go through
  the #35 resolver — it INSERTs unconditionally and surfaces a `uq_companies_name_ci`
  collision as a 400. That last one is why **`POST /api/crm/companies/resolve`** exists: it
  delegates to `resolve_or_create_company_ids` verbatim, so quick-create is get-or-create
  (race-safe by construction, one normalizer owned by SQL) while the full New Company form
  keeps its honest "already exists" error. Contact quick-create needs no new endpoint —
  `POST /contacts` with a name already works. **The ownership split is deliberate**: the
  contact is created with the name ONLY, so `_create_payload` assigns the caller, while the
  company rides the resolver and is left unassigned — the rule
  `test_auto_created_companies_are_left_unassigned` pins by reading that function's source.
  The trimmed query is the single form used for BOTH searching and creating, which is what
  closes the leading/trailing-whitespace duplicate hole for free. Reusable by design for
  **#126** (ContactForm's company field), which landed on it unchanged — see the paragraph
  below.
  Three rules inside the picker are subtle enough to state, because each was a bug first:
  **closing is not cancelling** — an outside click (the form's own Save button is one)
  closes the popover but does NOT abandon an in-flight quick-create, since the record is
  written either way, and `onBusyChange` lets `DealForm` refuse to submit underneath one
  rather than saving a deal without the link that is about to exist; **Enter is swallowed
  but does not select** until the user has typed or arrowed, because the list opens on focus
  and there is no "nothing highlighted" state, so a habitual Enter in an already-linked
  field would otherwise replace the link with whatever sorted first; and **display text and
  match text are different things** (`getMatchText`), because the company picker decorates
  an archived row `"Acme (archived)"` and matching on that would offer to create a duplicate
  of the row directly above. The contact side's exact-match dedupe only sees the 20-row
  page — companies are immune, resolving server-side — which is documented at
  `PICKER_LIMIT` as an accepted single-install trade.
  **#126 is the second surface**, and it is the freetext↔link merge the #35 bullet deferred:
  `ContactForm`'s free-text `Company` input and its capped `Linked Company` `<select>` become
  ONE `RecordCombobox`, consumed unchanged. So the form no longer has a way to record a
  company name without a company row behind it — typing an unmatched name offers
  `Create "…"`, which resolves to a real deduplicated row. The `contacts.company` COLUMN is
  untouched and still non-authoritative; the form keeps writing it in step with the link so
  the two cannot contradict. The one rule worth stating, because nothing on screen shows it:
  **an untouched company field omits BOTH `company` and `company_id` from a `PUT`.** The
  update route reads its body with `model_dump(exclude_unset=True)`, so an omitted key is not
  written at all — and that is the only thing standing between a pre-#35 contact holding
  unmatched free text and the silent erasure of the only record of that name, by someone who
  opened the form to fix a phone number. Sending `company: ''` would do it. Touching the
  field (pick, create, or the × clear) sends both keys; a CREATE always sends both, having no
  prior value to protect and no way to express absence (`POST /contacts` dumps without
  `exclude_unset`). Such a contact renders its free text as the picker's empty label plus a
  *not linked* hint, which both disappear the moment the user speaks for the field — left up,
  the label would keep naming a company they had just cleared. **Divergence from the
  blueprint, deliberate:** cake_os's `QuickAddModal` (#2016/#2049) defers the company create
  to submit so an abandoned form leaves nothing behind; CakeCRM creates on row press, because
  that is `RecordCombobox`'s shipped contract and the issue mandates one component for both
  surfaces. The cost is bounded — quick-create goes through the `/resolve` get-or-create, so
  an abandoned form leaves at most one unowned company and a retry reuses it.
  Two consequences of reusing the component unchanged are **accepted, not overlooked**.
  (1) The picker's × is gated on a non-null value and `companyTouched` is set only by
  choosing or clearing, so an unlinked contact would have had NO way to delete a wrong
  legacy name without first linking some company to it — `ContactForm` therefore renders its
  own inline **Remove** action on the not-linked hint. (2) Typing a name and pressing Save
  **discards it**: the widget keeps its query private and reports only choose/create/clear,
  where the removed free-text input committed on Save. `Create "…"` is the commit
  affordance. That is `RecordCombobox`'s contract and has been true of `DealForm`'s two
  pickers since #123, so it is a property of the component, not a regression this issue
  introduced; both are pinned by tests in `ContactForm.test.tsx`. The one capability
  genuinely retired is editing unlinked free text to a DIFFERENT arbitrary string without
  linking — which is the merge the #35 bullet deferred, not a side effect of it.
  User-defined **custom fields** (#19) add a
  two-table EAV (`crm_field_definitions` + `crm_field_values`) on contacts/companies/
  deals, managed in `/crm/settings`, rendered in the entity forms and detail pages, and
  exposed to the assistant via `crm_{get,set}_{contact,company,deal}_fields`;
  `crm_field_values` is polymorphic (no entity FK), so it is cleaned at every
  entity-delete + `_truncate_all` site (definitions survive demo-clear, wiped only by
  `clear_all`), and `is_required` is advisory-only (never enforced server-side).
  **Deal lifecycle + sales intelligence** (#22 Phase 1) add `deals.lost_reason` and a
  soft-archive `deals.archived_at` (NULL = live) plus an append-only `deal_stage_events`
  log. Every deal column update funnels through `service._write_deal_update`
  (`create_deal` and `archive_deal` are the two writes that don't — neither has an old
  stage to transition from), which in ONE transaction takes `SELECT stage … FOR UPDATE`, writes the row, appends a stage event
  when the stage moved, and CLEARS `lost_reason` when a deal leaves `lost` (the bug the
  blueprint fixed after our snapshot). `archived_at` is a **sweep**: `LIVE_PREDICATE`
  is carried by every deal-reading query (pipeline, dashboard, analytics, list/search,
  contact/company rollups, touch-count backfill, the Gmail-scan open-deal attribution),
  and `crm/analytics_service.py` IMPORTS those predicate constants rather than re-typing
  them. The line the sweep draws is **work items follow the deal, history does not**: an
  archived deal's open tasks drop out of `list_tasks` (archiving is the user's "stop
  nagging me" gesture, and the heartbeat is told to read that list), while `activity_log`
  is never filtered — it records what actually happened, and you need it to decide
  whether to restore. Deliberate exceptions: `get_deal` (fetch-by-id must still resolve
  an archived deal so it can be shown/restored/merged), the is-the-CRM-empty counts, and
  the **two opt-in holes** that keep an accidental archive or a wrong merge recoverable —
  `crm_search_deals(include_archived=true)` (the assistant's route back, needs a provider)
  and, since **#83**, `get_pipeline(include_archived=true)` (the keyless one). A stage
  change on an archived deal is refused outright: won + archived would book revenue no
  report can see.
  **#83's hole is exactly one query wide, and the asymmetry is the rule, not a lapse.**
  The flag opens `get_pipeline`'s *deals* query only; `stage_summary` keeps
  `LIVE_PREDICATE` unconditionally. That looks like precisely the one-sided filter the
  function's own no-`owner_id` comment forbids, and the difference is that owner is a
  symmetric facet (cards and totals must describe the same set or the page lies) while
  archived is not: an archived deal must be **findable** or it is unrecoverable, and must
  never be **money**. The client mirrors the same split — `liveVisibleItems` feeds every
  $ aggregate, the open-pipeline header, the select-all ids and the bulk payload, while the
  cards render from `state.kanbanItems` and do not — so cards and totals still agree about
  value. Reaching it: an **Archived facet** (`'include' | 'only'`, default null = live only).
  Since #74 it is a plain `single` `FacetDef` on `pipelineCollection.ts` and its value lives
  in the layer's `collection_crm_pipeline_v1` envelope, so the layer counts it, **Clear
  filters** resets it, and #83's hand-rolled coercion is gone — the facet's predicate fails
  an unrecognised value toward LIVE-ONLY rather than toward a wider board. It is the ONE
  facet that also widens the FETCH, because a client predicate cannot filter rows the server
  never sent; `PipelinePage.load` keys the query param off a derived boolean read from
  `state.facetSelections.archived`, so an unrelated facet change never refetches — and a
  second `loadGen` ref (distinct from `writeGen`, which guards against racing *writes*)
  discards a superseded load, since two quick facet flips can otherwise resolve out of order.
  A failed non-silent load now toasts: with `data` already populated a swallowed failure
  renders the previous payload, which under `'only'` is an empty board indistinguishable
  from "no archived deals".
  **Two things the collection layer cannot express, and where they went instead (#74).**
  (1) The layer skips an INACTIVE facet's predicate, so the resting "hide archived" state is
  not enforceable as a facet at all — it is the server's `LIVE_PREDICATE`, which is exactly
  why the facet widens the fetch. The gap that leaves is a live-only refetch that never
  lands: deferred behind an in-flight write, or failed outright, with archived rows still in
  `data` and the facet already off. `load` closes it by pruning them from `data` on both
  paths (`pruneArchivedFromBoard`), which is where #83's null predicate branch went. (2) An
  empty `items` makes the layer render its own empty state INSTEAD OF its toolbar, so the
  facet would be unreachable exactly when it matters — archive your last open deal and the
  recovery view is behind a control that is no longer on screen. A **Show archived deals /
  Show live deals** link therefore sits in the page header beside "Show all", for the same
  reason and above `CollectionView` for the same reason.
  Archived cards render **inert** — dimmed with an ARCHIVED chip, no bulk checkbox, never
  selected, excluded from select-all, and genuinely un-draggable via `shared/dnd`'s
  `dragDisabled`, widened from `boolean` to `boolean | ((item) => boolean)`. That widening
  needs the two named helpers in `shared/dnd/dragDisabled.ts` rather than an inline check,
  because the prop now answers two different questions and conflating them is silent: a
  **function is truthy**, so `KanbanBoard`'s old `!dragDisabled` overlay test would have
  unmounted the `DragOverlay` for *every* card the moment any per-item policy was supplied —
  live cards would drag with nothing following the pointer. `boardDragDisabled` (=== true)
  gates the overlay, `resolveDragDisabled` answers per card, and a test pins that a
  predicate is board-*enabled*. Since #74 the board reaches `shared/dnd` through the
  collection layer, so `CollectionKanbanProps.dragDisabled` carries the SAME union and
  `KanbanView` unwraps the `{id, item}` wrapper before calling it — a naive pass-through
  compiles, throws nothing, and simply reads `undefined` off the wrapper, answering
  "draggable" for every card. There the layer's own `dragLocked` is checked FIRST and
  collapses to a literal `true` rather than being OR-ed in, for the truthiness reason above.
  **The LIST view owes that same ceiling and did not pay it until #74's settle**: it is the
  shared layer's own table, not the page's markup, so the board's `selectable={!archived}`
  had no counterpart there and every archived row rendered a checkbox. Fixed with
  `CollectionSelectionProps.isSelectable` — id-keyed, so the interface stays non-generic and
  every consumer keeps passing `selection` as an inline literal — which withholds the
  checkbox ENTIRELY on an ineligible row (not a disabled one, not an unchecked one) and
  narrows select-all to the selectable rows. Both halves were live bugs, and the second is
  the one a reviewer misses: the page prunes archived ids out of `selectedIds` so the bulk
  count and the bulk payload describe one set, which made a row checkbox a control that
  stored an id on every click and never ticked — and with one archived row on screen "every
  visible row is selected" is unreachable, so the header box could neither tick nor, its
  clear branch being gated on that same flag, clear. The payload was never at risk
  (`applyBulkMove` recomputes from `liveVisibleItems`); what was broken was two dead
  controls. Same shape of rule as `dragDisabled`, for the same reason — one class of row is
  inert while the surface around it stays live.
  `POST /api/crm/deals/{id}/restore` (member-accessible, sync
  `def`, calls the already-bidirectional `archive_deal(archived=False)` — no new service
  logic) backs the deal sheet's archived banner; the banner reads the **re-fetched**
  `archived_at`, not the frozen list row, since the assistant can archive a deal between the
  board's load and the sheet opening. Restore hands the returned row UP (`onRestored`) for
  the board to patch in place, which is why the route returns the deal rather than
  `{"ok": true}`: a silent refetch can fail invisibly and leave the board still showing a
  deal as archived after a restore that happened. The board is also refreshed after the patch
  (a restore closes the sheet the way `onClose` does, which refreshes so an in-sheet note
  reaches `last_activity_at`) — the patch is what makes the board *correct*, the refresh only
  makes it *fresher*. `CrmDashboardPage` wires `onRestored` too: the banner renders on any
  host, since it reads the sheet's own re-fetched `archived_at`. Mark Won/Lost hide on an
  archived deal, and `DealForm` both disables its stage `<select>` **and omits `stage` from
  the PUT** — the server refuses the stage change and rejects the **whole** update, so an
  editable control would discard every other field the user just typed, and a *disabled* one
  still would if the deal had moved stage elsewhere since the board loaded: a disabled
  control's value is not user intent, so the field is not sent at all. `include_archived` is **refused (400), not ignored**, alongside
  `stage`/`contact_id`: that branch is `list_deals`, which keeps the sweep, and honoring it
  there would be a second hole. Scope ceiling: view + restore only — no archive route, no
  merge UI, no archived-deals page. Note restoring a **merge source** is not an undo: the
  merge already repointed activity/tasks, copied notes and gap-filled custom fields onto the
  target; restore only makes the source visible again.
  `deal_stage_events` is the one CRM table with a real FK to `deals`, so it MUST stay in
  every `TRUNCATE` sweep or the CRM reset errors out. `merge_deals` repoints
  activity/tasks, copies notes with a `[Merged from deal #N]` marker, gap-fills custom
  fields (the target's own values always win), and archives — never deletes — the
  source. **Bulk stage moves** (#55) run through `service.bulk_move_deals` in ONE
  transaction, capped at `BULK_MOVE_MAX` (200 — the cap bounds both the lock window and
  the post-commit rescore, since CakeCRM sends one unchunked request): rows are locked
  `ORDER BY id ... FOR UPDATE` (`merge_deals`' deadlock rule), then flushed as one
  `UPDATE` per distinct field map plus ONE multi-row `unnest` INSERT into
  `deal_stage_events` **on the same cursor**, so "deals moved but the history is
  missing" is unreachable. The stage-change RULES live in exactly one place —
  `_classify_deal_update`, a pure helper shared with `_write_deal_update` — so the
  single-deal and set-based paths cannot drift about WHAT to write (an integration test
  pins twin deals moved through each path to identical rows *and* identical stage
  events). The classifier deliberately does not decide WHETHER to write; each path decides
  that for itself, and **since #96 they agree**. Bulk skips a same-stage deal in Python on
  its locked pre-image; `_write_deal_update` instead carries an `IS DISTINCT FROM` test
  over exactly the columns it is about to SET, so a write that changes nothing matches no
  row and `updated_at` never moves. **The decision is made in SQL, not Python, and that is
  load-bearing**: the assistant's tool arguments are not runtime schema-validated, and
  `1.0 == True` is True in Python where Postgres correctly refuses a boolean into a
  `DOUBLE PRECISION` column — so a Python pre-image comparison would turn invalid writes
  into silent no-ops, and would mishandle NULL (an unlinked `contact_id`) besides. Bulk can
  stay in Python because it writes exactly one caller-controlled column, a `DEAL_STAGES`
  string validated before the connection opens. Postgres coerces on **assignment** but
  promotes on **comparison**, so the distinctness test casts the parameter to the column's
  destination type, declared once in `_DEAL_COLUMN_TYPES`. The two contexts disagree in
  OPPOSITE directions, so the cast is not optional: INTEGER promotes on comparison
  (`probability=40.1` stores 40 unchanged, but an uncast `IS DISTINCT FROM 40.1` calls it
  distinct and bumps `updated_at` anyway), while TEXT accepts an I/O conversion on
  assignment and has NO comparison operator (`title = 12345` has always stored `'12345'`,
  but an uncast comparison raises `operator does not exist: text = integer` — live, since
  `crm_update_deal` forwards raw unvalidated LLM arguments). Only the comparison is cast,
  so assignment behavior and its type errors are untouched. **`_DEAL_COLUMN_TYPES` is
  deliberately NOT the allowlist**: it covers internal-only columns (`lost_reason`) and grows
  whenever a new internal write path routes through the chokepoint, so deriving
  `update_deal`'s allowlist from it would be default-OPEN — declaring a type for an internal
  column would silently make it writable by `crm_update_deal`'s raw model kwargs and
  `PUT /api/crm/deals/{id}` in the same commit. The boundary is the hand-maintained,
  default-CLOSED `_DEAL_USER_WRITABLE` (no `lead_score`, no `archived_at`), and a **hermetic**
  test asserts only the safe direction, `_DEAL_USER_WRITABLE ⊆ _DEAL_COLUMN_TYPES` — so
  "no writable column without a declared type" still holds, in the suite CI actually runs.
  An integration test pins each declared type against `information_schema`. The `deal_stage_events` INSERT is
  gated on the UPDATE's rowcount as well (a real stage change always differs, so this is
  structural rather than reachable). Two consequences are accepted rather than incidental: a
  **custom-field-only save no longer bumps `deals.updated_at`** (`DealForm` always PUTs the
  standard fields and then writes changed custom fields separately, and `set_field_values`
  never touches the parent row — so that bump was a side effect of an unchanged-form PUT,
  and the detail page's `CustomFieldsSection` never produced one at all), and a no-op save
  no longer floats a deal up an `updated_at DESC` ordering — including `crm_get_pipeline`'s
  first-25-per-stage window.
  The custom-field one **resolves an inconsistency by picking uniformity, not by picking
  the more accurate answer**, and that is worth stating plainly: a user who edits only a
  custom field has done real work on that deal, and nothing in `LAST_TOUCH_SQL` now records
  it, so the deal keeps getting nudged until someone logs a note. It was arbitrary before
  (bumped from the edit modal, not from the detail page) and is consistently
  **not-a-touch** now. Taking the other branch belongs in `set_field_values`, which already
  holds the entity row `FOR UPDATE` — but it is a product call about what a "touch" means
  across contacts and companies too, and it needs its own change detection first: both UIs
  send only changed values, while the `crm_set_*_fields` tools can send unchanged ones, so
  a naive bump there would reopen exactly this bug against `crm_field_values`.
  **Deliberate divergences between the two paths**, each with its own reason: *where* the
  no-op is decided (SQL vs Python, above); the **error contract** — `_write_deal_update`
  raises, bulk isolates per deal (missing/archived deals report in `errors` while the rest
  still commit), because one archived deal must not sink a 50-deal selection; the
  post-commit **rescore**, which the single-deal path runs unconditionally while bulk
  rescores only `updated_ids` (correct rather than an oversight — `score_on_event` is
  swallowed on failure and `_maybe_refresh_scores` excludes terminal deals that already
  carry a score, so re-calling `mark_deal_won` is the only repair route for a won deal
  whose rescore failed); and **provenance**, where `crm_update_deal_stage` badges a skipped
  write while `crm_bulk_move_deals` badges only `updated_ids` — left alone because
  `provenance_service.record` documents re-badging an identical rewrite as intended
  ("EVERY AI (re)write resets confirmation"), which makes bulk the outlier there, not the
  single-deal path.
  In **bulk specifically**, a deal already in the target stage is dropped from the write
  plan after the locking `SELECT … FOR UPDATE` and before any write SQL is issued — so no
  `updated_at` bump, which `LAST_TOUCH_SQL` would otherwise read as a touch and reset the
  staleness clock on a deal nothing changed. (The single-deal path reaches the same end
  state differently: it *issues* the UPDATE, which then matches no row.)
  Whole-request refusals come back as `ok:false` with HTTP 200, never a 4xx, because the
  board's honesty depends on only transport/5xx failures throwing: a refusal means
  nothing was written (revert), a thrown 5xx means the outcome is genuinely unknown
  (never revert — a connection can drop after the commit). **The open-stage-only promise
  is kept at the TOOL layer only** (#99): `crm_update_deal_stage` and
  `crm_bulk_move_deals` both advertise it, so both carry a schema `enum` of
  `service.OPEN_STAGES` *and* an executor refusal of `CLOSED_STAGES` — the enum only
  steers (nothing validates tool args server-side), so the executor is the enforcement
  point. `CLOSED_STAGES` sits beside `DEAL_STAGES` and `OPEN_PREDICATE` is its SQL
  spelling (a test pins them in agreement). The service, the REST route and
  `crm_update_deal`/`crm_create_deal` stay permissive by design — this is interface
  honesty, not a data-integrity boundary; closes route to `crm_mark_deal_won`/`_lost`,
  which *can* record a lost reason.
  **A human can write that reason since #128**, through `POST /api/crm/deals/:id/mark-lost`
  → `service.mark_deal_lost`. Before it, `lost_reason` rendered on the deal sheet but the
  assistant was its only writer — `_DEAL_USER_WRITABLE` excludes the column, so a keyless
  install could read a lost reason and never type one. The route delegates to the lifecycle
  verb rather than widening that set, which is what preserves the invariant the exclusion
  exists for: a reason still arrives only WITH the close, and can never be pasted onto a deal
  that isn't lost. `mark_deal_lost` gained an `author_id` the route fills from
  `get_current_user` — the generated "Deal lost —" chatter note is a human's typed prose, so
  leaving it NULL would file a rep's own work as Unattributed (the assistant tool still omits
  it, which stays correct for Phase A). The endpoint is chosen by the ACTION, not the text:
  `crm/dealStageWrite.stageWriteRequest` (shared by `PipelinePage` and `CrmDashboardPage`, so
  they cannot drift) routes to the verb whenever the Mark Lost dialog was used *even with an
  empty reason*, because `PUT /deals/:id {stage:'lost'}` does not zero `probability` and
  writes no note — a drag or bulk move still takes the plain PUT. `components/LostReasonModal`
  reuses #57's `composerKeyAction`, inheriting its IME (`isComposing` off the NATIVE event)
  and AltGr guards instead of the blueprint's weaker inline chord, and renders through a
  **portal**: the surface it opens over sits BELOW the assistant launcher — `DealDetailSheet`'s
  root set `zIndex: 39`, and since #75 its replacement is `DetailModal` in `underLauncher` mode
  (`z-50 dock:z-[39]`) — and either one establishes a stacking context no descendant can escape. It also holds a
  synchronous submit-once latch, because `_write_deal_update` returns True for a no-op, so a
  repeat submit would append a second note. The REST boundary **rejects** a reason past
  `MAX_LOST_REASON` (422) where the service truncates — deliberately unlike `BulkDealMove`,
  which declines a Pydantic cap because *its* service refuses with a renderable sentence;
  silently dropping the tail of typed prose is data loss, not a refusal worth preserving.
  **Owner is displayed since #128** too, via `components/OwnerName` on the deal sheet and the
  contact/company detail pages. `useUsers().nameFor()` already resolved NULL to "Unassigned"
  but only the pipeline facet chip consumed it, so an owner was editable and filterable yet
  never *shown* — worse than the blueprint's blank-row bug. Those rows render
  **unconditionally**, unlike every hide-when-blank neighbour: NULL owner is a real state
  (#60) and a hidden row is what makes it unreadable as one, so a `{owner_id && …}` wrapper
  reintroduces the bug. On `CompanyDetailPage` it is its own line rather than a `subline`
  term, since that string is built by dropping blank fields. Chatter now also attaches to
  **companies** (zero-migration: `entity_type` is
  free TEXT), cleaned in `delete_company`.
  **Chatter notes take attachments** (#57, `backend/crm/attachment_service.py` +
  `backend/core/thumbnails.py` + `frontend/src/crm/{chatterAttachments,chatterComposer,postNote,useChatterPost,useAuthedBlobUrl}.ts`
  + `components/{NoteComposer,NoteAttachments,AttachmentLightbox}.tsx`), keyless — nothing
  here keys off `ai_ready`. **The bytes live in Postgres (`bytea`), and that answers the
  issue's gate question rather than dodging it:** the Railway container filesystem IS
  ephemeral EXCEPT the volume `railway.json` requires at `/app/backend/data`
  (`requiredMountPath`; a deploy without it does not start — it is why the branding logo
  and the encryption-key fallback survive), so files-on-disk WOULD have worked on both
  targets. Postgres wins anyway on one store / one transaction / one `pg_dump`: the
  README's documented rollback is restoring a dump, which contains no files, and with the
  bytes in the row every `TRUNCATE` and cascade takes them atomically instead of leaking
  bytes at each site. The honest cost is stated in the migration header — attachments grow
  the database and the dump, `core/postgres.py` has no streaming primitive, and a hard
  delete does not immediately shrink TOAST files. Bounded by a **10 MB per attachment**
  (the `assistant/uploads` precedent, deliberately tighter than the blueprint's 20 MB
  because there is no streaming read) and **10 per note**; NOT bounded at install level,
  which is accepted for a self-hosted CRM whose members can already delete every record —
  a quota is the named upgrade path. Two honest limits on those caps: peak memory is
  per-request × threadpool concurrency (~40 threads on the one worker), not 10 MB — held
  down in practice by thumbnails-only lists, explicit-open originals, the 304 path and a
  client that aborts abandoned downloads, with a weighted admission gate as the upgrade
  path; and an oversized **multipart** body is spooled by Starlette BEFORE any route code
  runs, so no per-route cap can stop it. That is why `main.MAX_REQUEST_BYTES` exists — a
  64 MB middleware backstop (Content-Length only) that runs before the body is consumed.
  It is a disk backstop, not a feature limit, so it must stay above the largest legitimate
  request (an assistant upload: 5 × 10 MB); a test pins that ordering, and another pins the
  spool-before-dependencies behaviour that makes middleware the only workable layer.
  **That backstop is not an admission limit, and #127 split the two.** A disk backstop
  sized for the app's largest route is 6.4× what a chatter attachment may be — and 32-64×
  what the logo and CSV-import routes accept — so everything between each route's real cap
  and 64 MB was admitted, spooled and parsed before that route's bounded read refused it:
  the cheap outer gate none of these uploads had. `main._ROUTE_REQUEST_LIMIT_SPECS` (the
  hand-edited table; `_ROUTE_REQUEST_LIMITS` is its compiled derivative) is therefore
  a first-match-wins path-template → ceiling table consulted by the SAME middleware
  (`_request_limit_for`), sizing **each** upload route at its own feature limit plus
  `MULTIPART_ENVELOPE_BYTES` and leaving every other path on the global ceiling. It lives
  in the existing middleware rather than a `Depends` guard or a second middleware for the
  reason the paragraph above already establishes — middleware is the only layer that runs
  before the body is consumed.
  **The table is keyed by the route's path TEMPLATE and compiled with Starlette's own
  `compile_path`, and hand-writing those patterns instead is a bypass, not a style choice.**
  A hand-written `\d+` for `{note_id}` reads as obviously correct and is wrong: `note_id:
  int` is FastAPI **validation**, not routing, so the router compiles that parameter to
  `[^/]+` and `/api/crm/chatter/note/abc/attachments` reaches the multipart parser, spools,
  and only then returns 422 — under a `\d+` gate it drew the 64 MB backstop the whole table
  exists to avoid. An admission pattern must cover everything the ROUTER accepts, not
  everything the handler will go on to accept; deriving it from the template is what makes
  that unrepresentable rather than merely fixed once. (An earlier revision of this work
  shipped the `\d+` version and a test that asserted the bypass was correct behavior.) The
  **assistant** upload route is deliberately absent: its legitimate 5 × 10 MB already sits
  close to the 64 MB backstop, so a row would only restate it. #127 also gave
  `branding/router.upload_logo` the repo-wide `read(cap + 1)` idiom — it was the one upload
  route still doing an unbounded `await file.read()`, buffering the whole part before the
  size check could refuse it.
  Three properties are pinned, and the first two are pinned that way because the obvious
  test does **not** fail against the bug: the headline test asserts the **parser never ran**
  rather than merely a 413 (the routes have always 413'd) and sizes its body from the
  FEATURE cap, never from `_request_limit_for` — sizing it off the function under test made
  it pass with the table emptied, since the request then simply hit the global ceiling
  instead; the mounted-route guard asserts the table is **non-empty** before looping, since
  a `for` over an empty table passes while checking nothing; and
  `test_every_upload_route_is_bounded_below_the_backstop` enumerates every file-taking
  route from the app itself, so **a new upload route that forgets its row fails CI** rather
  than silently admitting 64 MB. That last one is only as good as its detector, so the
  detector reads FastAPI's dependency graph via `get_flat_dependant` +
  `isinstance(field_info, params.File)` and carries its own synthetic self-test: the
  obvious version — a string match for `UploadFile` on `route.dependant.body_params` —
  silently misses `data: bytes = File(...)` (annotated `bytes`) and any file arriving
  through a `Depends(...)` sub-dependency (`body_params` is not flattened), which are both
  ordinary FastAPI and would have been waved through green. A fourth test pins each row's
  exact `feature cap + envelope` arithmetic, because a wrong VALUE (a row at 63 MB)
  satisfies every structural guard while reopening nearly the whole window. Two of these
  are pinned at the MIDDLEWARE rather than at the helper, which is not a stylistic
  preference: a root_path test that calls `_request_limit_for(get_route_path(...))` itself
  passes no matter what the middleware feeds in — reverting the fix left the file green.
  Unchanged and deliberate: a **chunked** body declaring no
  Content-Length still slips both ceilings and is caught only by the route's
  `read(cap + 1)` — bounded in memory, still spooled — because counting bytes as they
  stream stays "real machinery for a case no browser produces". A *lying* Content-Length is
  not a third hole: h11 delivers exactly the declared byte count to the app, so the
  transport enforces the number the middleware trusted. `crm_chatter_attachments` is the second CRM table
  with a **real FK** (`crm_chatter ON DELETE CASCADE`), which is the whole lifecycle
  design: `delete_contact`/`delete_company` need NO new code, and the FK means the table
  MUST ride BOTH `_truncate_all` statements (Postgres refuses to truncate a referenced
  table alone — the `deal_stage_events` rule). It is deliberately **excluded** from
  `is_crm_empty`/`_crm_empty_in_txn`: the cascade makes "attachments while `crm_chatter` is
  empty" unrepresentable. Three rules are non-negotiable and each has a test: (1) the
  stored MIME comes from **magic bytes** and the client's declared type is *not even a
  parameter* — four image types plus PDF keep a real type, everything else (SVG included)
  is stored and served as inert `application/octet-stream`, because these bytes come back
  from the app's own origin where a stored XSS reaches the session token (the branding-logo
  lesson, twice); (2) serving is **authenticated** (`GET …/thumb` and `…/file` behind
  `get_current_user`) and the frontend fetches through `apiBlob()` into object URLs — a
  bare `<img src>` cannot work at all here, since auth is a Bearer token with no cookie
  fallback — with `nosniff`, an attachment `Content-Disposition` (RFC 5987), `ETag`,
  `Vary: Authorization` and `Cache-Control: private, no-cache`, never `immutable`, because
  `RESTART IDENTITY` reuses attachment ids and a fresh immutable response is never
  revalidated; (3) list views fetch **only** the ≤28 KB server thumbnail — the original
  loads on an explicit open. `filename` is normalized once in the service and stored NOT
  NULL (a nameless upload used to crash the header encoder; a path-bearing one is a header
  problem). `create_attachment` runs cheap-preflight → thumbnail (OUTSIDE any transaction —
  Pillow never runs holding a row lock) → ONE transaction that re-checks everything under
  `SELECT … FOR UPDATE` on the parent note, with **idempotency checked before the cap** so a
  lost response on the tenth attachment stays retryable; `delete_attachment` takes the same
  parent-then-child lock. A NULL thumbnail is a legal terminal state (a non-image, or an
  image the decompression-bomb ceilings refused) and renders as a **download-only** chip —
  never the lightbox, or the browser would perform exactly the decode the server declined.
  `core/thumbnails.py` is ported from the blueprint's *gallery* lineage, not its chatter:
  cake_os chatter has no server-side thumbnails at all, so #57 is a composite of three
  upstream features rather than a port of one. Attachment metadata rides
  `get_chatter`, so `crm_get_chatter` inherits it with no new endpoint — one more
  user-typed-text field in front of a background turn, on #22's terms (the ceiling is still
  one `notify_user`). No agent upload tool: the model has no bytes. **Phase 2** adds the two composing reads:
  `crm_get_deal_health` (one deal — #18's `score_deal()` plus days-in-stage,
  days-since-touch, open/overdue tasks and missing links, reduced to a `flags` list;
  it composes and never recomputes the scoring model) and
  `crm_get_pipeline_analytics` (time-in-stage, per-stage conversion and velocity read
  from `deal_stage_events` — precisely the half #20 had to drop for want of a
  stage-change trail, so the two analytics tools are complementary, not overlapping).
  Because that log only began at Phase 1, every response carries `history_since` /
  `history_covers_window` and `SALES_GUIDE` tells the assistant to state the real span
  rather than present a partial funnel as the whole picture.
  The pipeline board's bulk UI (#55) holds a `bulkPending` lock from the click until the
  reconcile refetch settles — while held, `moveDealStage` returns early and drag is
  disabled, so a single-deal rollback can't clobber the server truth the bulk is about to
  fetch. Selection is a `Set<number>` intersected with the currently visible set; **since
  #74 that intersection is computed twice, deliberately, by two owners that cannot
  disagree**: `CollectionView` derives the bar's COUNT from selected ∩ the current view's
  items (and renders the bar only when that set is non-empty), while `applyBulkMove`
  recomputes `applicableBulkIds(bulkSelected, state.visibleItems)` at CLICK time, because
  the selection can change between the render that drew the bar and the click that applies
  it. They agree because both read `visibleItems` — which requires the pipeline config to
  declare **no `getVoided`** (that absence is what keeps `kanbanItems === visibleItems`)
  and hidden-stage deals to be absent from `items` entirely, not merely from the columns.
  Adding a `getVoided`, or hiding a stage by omitting its column while leaving its deals in
  `items`, silently splits the two; `pipelineCollection.test.ts` pins the former.
  The rejected/unconfirmed/skips wording lives in the pure
  `crm/bulkOutcome.ts` (`ApiError` was added to `core/api/client.ts` to carry the status
  that split needs). The ~45 `crm_*` agent tools + executors are
  collected UNCONDITIONALLY via `crm.tools.get_crm_tools()` — each def carries a
  `"writes"` flag (the single source of truth for the assistant's confirmation gate),
  consumed by `assistant.registry.ToolRegistry` (landed #4). The four read-only
  intelligence tools (`crm_get_stale_deals`, `crm_get_contact_staleness`,
  `crm_find_duplicates`, `crm_scan_gaps`, all in `crm/analytics_service.py`) are pure
  SQL — keyless — and because `writes:False` derives the background allowlist they are
  heartbeat-callable for free.
  **Every tool that returns a deal record returns its `url` too** (#145) — a link that
  opens that deal, attached by `crm.links.with_deal_url()`. `crm/links.py` is the ONE
  server-side definition of the shape, `/crm/pipeline?deal={id}`: the real board route
  plus a query param, deliberately not a `/crm/deals/{id}` route, because `PipelinePage`
  already parses a `?stage=` sibling and a second form would mean two shapes against one
  parser. `deal_url` is absolute only when the install's public address was actually
  configured (`FRONTEND_URL` or a Railway domain — recorded as
  `settings.frontend_url_is_default`, tracked the same way `jwt_secret_is_auto` is);
  otherwise it stays relative, because asserting the `localhost:5173` dev default in a
  message sent to someone's phone is worse than a path their browser resolves.
  **The rule is stated rather than a chosen subset, and membership is decided by tracing
  what a tool's SERVICE returns — never by its name.** Far more tools qualify than the
  four the issue names: alongside the deal reads and all seven write confirmations,
  `crm_get_contact` and `crm_get_company` embed their rollup's deal rows, `crm_dashboard`
  returns five under `top_deals`, `crm_analytics` returns a `stale_deals` list beside its
  scalars, and `crm_find_duplicates`/`crm_scan_gaps` name deals one level deeper. The
  blueprint shipped that same miss three times, each time reasoning from a tool's headline
  purpose — and so did this port: `crm_get_company` was found by the final reviewer, and
  the converse guard was green because its own list of deal-returning services had been
  hand-written and did not name `get_company_detail`. That list is now derived: a test
  scans `crm.service` and `crm.analytics_service` for functions whose SQL reads the deals
  table and fails on any that is classified in neither direction, so the next one has to be
  looked at.
  `with_deal_url` is inert on anything without an integer `id` (`type(...) is int`, since
  `bool` is an int subclass and would emit `?deal=True`), so an error dict or `None`
  passes through — a link built from a missing id is worse than no link. The matching
  `CRM_DEAL_URL_GUIDANCE` is applied by ONE pass over `CRM_DEAL_URL_TOOLS` **at import**,
  never inside `get_crm_tools()` — that is called per turn and returns the shared list, so
  appending there would grow every description without bound. `tests/test_crm_deal_links.py`
  makes both halves structural: it derives the attaching set from the executors' own
  source and fails when the registry or a description disagrees, fails when an executor
  reaches a deal-returning service without either attaching a link or recording a waiver,
  and READS the TypeScript source to pin `dealDeepLink` against `DEAL_PATH_TEMPLATE`
  (two suites each asserting their own hardcoded copy of the string would prove nothing —
  editing the template and its expectation together is one self-consistent commit that
  leaves both green and the producers divergent). Never re-guard this with a hand-written
  tool list: a hand-list can only re-assert the mistake it was written beside.
  On the page, `?deal=` is resolved **during render** (`crm/dealDeepLink.ts` holds the pure
  rules; this repo's react-hooks ruleset makes a synchronous setState inside an effect a
  build error, so the `?stage=` render-compare pattern is the sanctioned one). Unlike
  `?stage=`, the parameter is **kept**, and the resolution keys off `location.key` — every
  navigation is its own event, including one to the URL already showing. That is what makes
  following the same link twice work (the id alone cannot tell a second click from no click)
  and what lets a link whose refresh FAILED be retried. Consuming it was tried and cost more
  than it bought: the rewrite is itself a navigation, so it re-armed the link it had just
  resolved and fired a second board refresh, and it raced the `?stage=` consumer for the
  same params object. Keeping it also means reload reopens the deal and the address bar is a
  real copy source — worth knowing for #75, which specifies stripping on the blueprint's
  authority, where losing the deal on refresh is called out as a caveat. Board membership is asked of
  the WHOLE payload, never of `filteredDeals`: a session facet says nothing about whether a
  deal exists. A miss on a board that PREDATES the link earns one silent refresh before any
  notice, because the assistant hands out links to deals it just created while the drawer
  sits over an already-loaded board; if that refresh fails the page says NOTHING rather than
  telling someone their live deal was deleted; if that one refresh fails the link is
  RETIRED rather than left armed, or an unrelated load minutes later would pop a sheet open
  with no gesture toward it. Staleness is measured in **load generations**
  (`boardLoads`), never in `data`'s object identity — every optimistic update on that page
  replaces `data` without asking the server anything, and reading that as "the board caught
  up" produced exactly the false accusation the refresh exists to prevent (caught in
  review). The notice takes itself back if the named deal later appears, since it tells the
  user to turn on the Archived facet and that has to be allowed to work; and a newer link
  supersedes the sheet an older one opened, keyed on which deal a LINK opened so a card the
  user clicked themselves is left alone.
  **The drawer is the primary surface, and it needed a fix outside this feature to work at
  all:** `assistant/MarkdownContent` rendered every link `target="_blank"`, and the session
  token lives in `sessionStorage`, which is per-tab and which a `noopener` tab does not
  inherit — so an in-app deal link opened a tab with no session, bounced through `/login`
  and landed on the dashboard with the id discarded. Same-origin links now navigate in the
  same tab through a react-router `Link`; everything else still opens in a new tab with
  `noopener noreferrer`. Internal-ness is decided by resolving the href with `URL` and
  comparing origins, never by a `startsWith('/')` test — `//evil.com/x` starts with a slash
  too, and an assistant message can carry a prompt-injected href.
  **One deployment note:** with neither `FRONTEND_URL` nor a Railway domain set, `deal_url`
  emits a relative path, which is correct in-app but not clickable in Telegram or a push
  notification. That is the least-wrong output (an absolute `http://localhost:5173/...` is
  wrong for every reader who is not at that machine), and the fix is to set `FRONTEND_URL`
  on any install whose assistant messages leave the app. **AI touch counts +
  field provenance** (#16) are the two zero-keys-degrading AI reads: an in-process
  daemon worker (`crm/touch_count_service.py`, event-driven off note/activity writes,
  light tier via `get_ai_provider(agent_model_tier="light")`, prompt-injection-hardened,
  never-fabricate) stores an estimated touch count in `deals.ai_touch_*` for a pipeline
  nudge pill; and `crm/provenance_service.py` (`crm_field_provenance`) records which
  standard fields the assistant wrote (recorded inside the assistant's write-tool
  executors — human router edits don't), badged until confirmed or overwritten. Both are
  invisible with zero keys (no count is computed, no provenance is written).
  **Since #56 the touch count is explainable, and that changed what the number means:**
  the model now judges EVERY numbered evidence line and `ai_touch_count` is the **derived
  sum** of the lines it marked as touches, so the pill and the drill-down cannot
  contradict each other (one note describing three calls now counts once — the price of a
  number you can audit). The per-line verdicts live in `deal_ai_touch_evidence`, **one
  JSONB snapshot row per deal** (not a normalized table: verdicts are replaced wholesale,
  never queried across deals, and the synthetic `[deal notes field]` line has no source
  row; not a column on `deals` either, or a multi-KB blob would ride every pipeline card).
  It is FK-less per the audit-table convention and therefore MUST stay in both
  `_truncate_all` strings, but is deliberately **excluded** from `is_crm_empty` /
  `_crm_empty_in_txn` / the seed guards (derived, not entity data — same side as
  `deal_stage_events`/`proactive_nudges`) and owns no sequence (PK is `deal_id`).
  `service._store_touch_count` writes count and snapshot in ONE transaction with the #16
  stale-write guard unchanged (ordering key on the event path, CAS on `force_write`), and
  the snapshot write is **gated on the UPDATE's rowcount**, so a recompute that loses the
  guard leaves no explanation of a number it never stored. Items store **no line text** —
  the reader renders the live row, because a note edit rewrites `message` without moving
  either guard key. A reply whose verdicts fail validation writes the **count alone and
  deletes the stale snapshot** (an explanation of a different inference must not survive
  beside a new number); incomplete coverage is a rejection, never a partial merge. The
  classified window shrank to **35 chatter + 15 activities** because the reply now scales
  with it and `stream_turn` exposes no `max_tokens` knob — the smallest fixed provider
  ceiling is 4096 output tokens (Ollama, openai_compat default), and a test pins the
  arithmetic so raising the window has to confront the ceiling. Read-only via
  `GET /api/crm/deals/:id/touch-count/evidence` → `touch_count_service.get_touch_evidence`,
  which never re-runs AI and reports `verdict_state` **current/stale/superseded/none**. It
  earns "current" only against FIVE checks, because four kinds of drift move none of the
  stale-write guard keys and so are invisible to a sum comparison alone: the visible touches
  vs. the number on the pill (catches a contact deletion that destroyed shared
  `activity_log` rows, or an archived note), a row **edited** since it was judged (an edit
  to a non-touch row moves nothing else), a live row the snapshot never **covered**
  (archiving a judged row out of a full window pulls an older one in, leaving the row count
  and newest timestamp untouched), a judged row that has **vanished** from the live set
  (clearing a `deal_notes` field judged not-a-touch moves *nothing* — that entry is in
  neither the watermark nor the evidence count — and on a closed deal a deleted row escapes
  the evidence-count check too, since that one is open-deals-only), and an item whose digest
  is unusable so it cannot be **verified** at all. Comparing the live and stored key sets in
  BOTH directions is what makes this a closed question rather than a list of drift routes to
  keep extending — four separate review findings landed on this reconciliation before it was
  symmetric. Edit detection compares a stored digest of the line as judged
  (`_line_hash`) against the live line — deliberately not a timestamp, since `activity_log`
  has no `updated_at`, `deals.notes` changes without one, and an `updated_at`-vs-`computed_at`
  comparison misses an edit made while the model was running. The bounded list also reports
  `truncated`, derived from a probe row fetched past each window so a deal holding exactly a
  full window is not mislabelled. Counts written before #56 report `none`; `?scope=all` is
  the documented repair, and nothing is backfilled at deploy time. Zero keys still degrades
  cleanly: no count means no pill and no drill-down, while stage moves (read from
  `deal_stage_events`) carry a **deterministic** never-counted verdict needing no provider
  at all. Field edits are deliberately absent **as an evidence source** — CakeCRM has no
  per-edit timeline (#15 dropped chatter's audit columns and `crm_field_provenance` is
  current-state, not history), so synthesizing one would fabricate history. `/backfill/status` also reports verdict health (ok/fallback/
  failed), because a model that silently stops emitting the schema would keep updating the
  badge while every detail view went empty. Contact
  import is keyless for CSV/vCard; the AI smart-import path (`get_ai_provider()`)
  degrades to a warning when no provider is configured and its UI affordance keys off
  `ai_ready`. First-run offers to load fictional sample data (prompt tracked on the
  `crm_meta` singleton, not a per-integration flag). The **CRM-first shell** (#9) leads
  nav with Dashboard/Pipeline/Contacts/Tasks, surfaces the assistant as a persistent
  launcher (never the home page), and shows a **dismissible** "add an AI key" nudge —
  never a gate, gated on `!credentials_present`, dismissal tracked on
  `crm_meta.ai_key_prompt_dismissed`. Branding (company name / logo) is edited at
  `/crm/settings`, consuming the existing `/api/branding`.
  **The Settings page is a shell, not a list** (#103): four sections — Personal →
  Assistant → Workspace → Integrations, member-visible first so a member's nav is a
  *prefix* of an admin's and the post-login `isAdmin` flip only appends tabs, never
  inserts one before the section on screen — rendered by `SettingsPage.tsx` as an
  underline tab strip of `<Link>`s in `<nav aria-label="Settings sections">`
  (navigation ⇒ underline tabs, the `ViewSwitcher` rule; deliberately **not** an ARIA
  tablist, because these tabs navigate and a `role="tab"` would promise arrow-key
  roving this does not implement) over the active section's cards **only**. Every card
  wraps itself in `components/SettingsCard.tsx` (`<section aria-labelledby>` + a real
  `<h2>` + optional description/badge), which owns padding; the page owns column width
  and inter-card spacing, so a card carries no `marginTop`/`maxWidth`/padding of its
  own — cards **replace** their old outer `<div>` rather than nesting inside the shell,
  and `settingsSections.test.ts` fails the build if a settings card still imports
  `cardStyle`. The section is deep-linkable as `?section=<id>` and is a pure function of
  URL + role recomputed every render (nothing memoises `isAdmin`, in either direction).
  That is load-bearing for the **Gmail OAuth callback**, which lands on `?gmail=…` with
  no `section`: `wantedSection()` maps a **non-empty** `gmail` to Integrations so
  `GmailCard` mounts and its effect can toast and strip the params, and that same effect
  writes `section=integrations` back as it strips — without which removing `gmail` would
  drop the view to the default section. Two details keep that from becoming a trap, since
  `gmail` outranks `section`: the non-empty test is exactly `GmailCard`'s own
  `if (!result) return`, so the page cannot select a section the card then declines to
  clean up; and the nav **deletes** `CALLBACK_PARAMS` from its links (it preserves every
  other param), because a one-shot callback param riding along would pin the view to
  Integrations and make every tab inert — permanently for a MEMBER, who never mounts
  `GmailCard` and so never strips it. All of it is pinned by `SettingsPage.test.tsx`.
  Only the active section mounts, so Telegram's 4 s link-poll runs only while
  Integrations is on screen; the cost is that switching sections remounts (unsaved
  in-card drafts are lost — acceptable, since the tabs are links and switching is a
  navigation). Branding's form lives in `components/BrandingCard.tsx`.
  The **theme itself is fixed**
  (#54) — one polished CakeCRM look in light and dark, defined as `--color-ck-*` tokens
  in `index.css` with a `.dark` override block; there is **no user-configurable accent**
  (`accent_color` was removed from `/api/branding`, and a stale key on disk is stripped
  on read). The neutral tokens are *semantic* (`bg` = page, `card` = surface, `ink` =
  primary text), so overriding them under `.dark` re-themes the whole app — login,
  setup, CRM, assistant, settings — with no per-component `dark:` variants; that block
  compiles **unlayered**, so it wins over Tailwind's `@layer theme`. Both the Tailwind
  `ck-*` utilities and the inline `var(--color-ck-*)` styles in `shared/styles.ts` +
  `crm/styles.ts` resolve through those tokens, and those modules carry **no literal
  hex/rgba fallbacks** — a fallback would silently pin a light colour into a dark
  surface. Tints are derived with `color-mix()` off a token (`shared/styles.tint()`),
  never hand-written rgba — the one exception being the three per-theme chrome tokens
  (`hover`/`scrim`/`shadow`), declared literally in *each* block because they tint
  **ink**, not the accent: a single dark tint would vanish on a dark surface.
  **Every hue is TWO tokens, split by ROLE — `--color-ck-<hue>` is the FILL (background,
  border, dot, bar) and `--color-ck-<hue>-text` is the GLYPH (text or icon)** — and since
  #119 that rule covers the whole family (`green`/`amber`/`red`/`ai`, the six `stage-*`,
  and `accent`), not just the brand red. #54 lightened the hues under `.dark` and never
  wrote the mirror rule for light, so in light mode all eleven failed AA 4.5:1 as text,
  worst `ScorePill` warm at **2.45:1** on a stage-washed deal card.
  **The split is arithmetic, not taste, and that is why #68's "retune the token, don't
  migrate call sites" answer was unavailable here:** a chip's background IS
  `tint(<the same token>, 12)`, so darkening one token to fix its text darkens the wash
  under it — clawing back most of the gain *and* pushing the neutral ramp under the floor
  #68 tuned it to (`ink-dim` on a stage-washed deal card has 0.22 to spare). The wash must
  hold still while the glyph moves. Consequently the constants are explicit —
  `SAGE_FILL`/`SAGE_TEXT`, `GOLD_*`, `CORAL_*`, `AI_*`, and `STAGE_COLORS[x]` carries
  `{ text, fill, bg }` — with the old ambiguous bare names **removed** so the compiler,
  not a grep, finds every consumer; that ambiguity is exactly what shipped the eleven
  failures. `ACCENT` keeps its unsuffixed fill name (it predates the rule, is
  overwhelmingly a fill, and ~50 sites already route text through `ACCENT_TEXT` — which
  is why fixing light-mode accent cost zero call-site edits).
  Values are **derived**: the smallest OKLCH lightness step from the fill (hue and chroma
  held) that clears 4.5:1 on every surface the app really paints, targeted ~4.55.
  `--color-ck-on-status` (white light / near-black dark) is the foreground for a hue used
  as a **solid action fill** — `accent-ink` is white in both themes, right on the brand red
  (4.66:1) but 2.49:1 on the green `.dark` lightens for text.
  **`opacity` on a container that holds a chip is now a bug, not a style choice**: it fades
  text and backdrop together, and a hue tuned to just over 4.5:1 cannot survive any fade —
  #83's archived deal card at `opacity: 0.55` measured **2.08:1** on its `ScorePill`, and no
  value below 1.0 fixes it. De-emphasise with `ink-dim` and an explicit chip instead.
  `core/theme/hueContrast.test.ts` is the guard (sibling to #68's `inkContrast.test.ts`,
  separate because they measure different families against different surface models): it
  resolves `var()` chains — most dark `-text` tokens are passthroughs to their fill — reads
  `accent-soft`'s per-theme mix percentage rather than assuming it, pins the base↔text
  pairing **one-to-one in both directions**, and carries a detector self-test that injects
  the real pre-#119 regression. Two things it needs from you: it composites the wash from
  the **fill** while measuring the **text** token (mixing from the text token models a chip
  that darkens with its own label — the coupling the split removed, and the actual bug #119
  found in `MemoryPage`), and unlike its sibling it deliberately does **not** model a chip
  inside an ink-hovered row, because that headroom is free for the ink ramp and here would
  force a visibly larger colour change to clear a pairing nothing renders. **The neutral ink ramp is bound
  by the same rule and is COMPRESSED because of it** (#68): all four of
  `ink`/`ink-mute`/`ink-soft`/`ink-dim` are body text — `ink-dim` alone paints every
  `labelStyle` label, every `sectionHeading()` and most empty states — so every step must
  clear 4.5:1, and a 4-step neutral ramp cannot do that and keep its old spread. Will's gate
  chose to **re-space the token values, not migrate the ~117 call sites**, so `ink-dim` sits
  just inside the lightest passing value (`#595959`, edge `#5c5c5c` / `#adaba5`, edge
  `#acaaa4`) with `ink-soft`/`ink-mute` above it at even CIE L* steps. The three
  secondaries stay as distinct from each other as
  they were (~6-7 L*; the old LIGHT ramp's own mute→soft step was already only 6) — what
  shrank is the primary→secondary gap, 27 L* → 9 light and 31 → 10 dark. The binding surface
  is **never a raw token**: `tint()` chips and row hovers composite an ink wash over
  bg/card/raised, and a chip inside a hovered row stacks two, so the floor is a stacked wash —
  4.72:1 light, 4.56:1 dark; the hover wash beneath is 5% in light and 6% in dark, which the
  guard reads out of `--color-ck-hover` rather than assuming. **That floor is deliberately
  tighter than what renders, and the test distinguishes the two** — dark's floor sits on a
  chip-inside-an-ink-hovered-row over `card` that has *no producer today* (the shared
  collection layer hovers by swapping to the opaque `bg` token, not an ink tint) and is kept as
  headroom, so dark's worst RENDERED pairing is 5.09:1. Keep that distinction if you touch
  these numbers: two review rounds went to prose that called a modelled bound a real pixel.
  That compression is also why `.dark .hljs-comment` moved to `ink-dim`: the new `ink-mute`
  sits 10 L* from `ink` and would have rendered code comments at nearly the weight of the
  code around them. `core/theme/inkContrast.test.ts` parses the **shipped** `index.css`
  (never a copy of the palette — a duplicated table drifts silently, which is the failure it
  exists to stop) and fails CI on any of the 43 surfaces × 4 tokens × 2 themes falling under
  4.5:1 — so **adding a new `tint()` background under ink text means adding it to that
  surface list.** Fonts are **self-hosted**
  via `@fontsource` (Montserrat for
  headings + buttons, Open Sans for body) — no Google Fonts CDN request, so an offline
  deploy renders correctly; `index.css` also carries a `.dark .hljs*` block because
  the assistant's `highlight.js` stylesheet is a fixed light theme. The light/dark
  choice persists in `localStorage`
  (`cakecrm_theme`) and is applied by a pre-React anti-flash script in `index.html`
  whose key is a contract with `core/theme/useTheme.ts` — both also keep the
  `theme-color` meta in sync so mobile browser chrome follows the app. So the CRM stays fully
  usable — and fully themed — with zero AI keys. The launcher opens a **context-aware slide-over drawer** (#14): the
  open deal/contact/company is published through a shared record context
  (`frontend/src/crm/RecordContext.tsx`, set by the detail pages + `DealDetailBody`, #75)
  and injected **per-turn** into the assistant's **volatile** system prompt as a
  server-built sentence from a validated `{record_type, record_id}`
  (`assistant/router.ChatContext` → `identity.build_context_note`) — never persisted,
  never client free text — with record-aware quick actions rendered in the drawer.
  **Lead scoring** (#18) is the third zero-keys, **pure-algorithmic (no AI)** CRM read: a
  0-100 `lead_score` on deals and contacts, recomputed inline at write-event chokepoints
  (serialized per-entity by a `pg_advisory_xact_lock`, never bumping `updated_at`) plus a
  **bounded** daily heartbeat refresh (T1 `_maybe_refresh_scores`, ≤`_REFRESH_BATCH` stalest
  rows per tick, self-resuming). It uses **dedicated columns**, never `deals.probability`
  (a live user/assistant-editable, provenance-tracked field) — and `lead_score` is never
  user/tool/assistant-writable. Deals sort by score client-side (within kanban column);
  contacts keep a server-sorted `lead_score` column (`DESC NULLS LAST`) for the REST/tool
  path, but since #77 the Contacts page sorts client-side over its assembled corpus.
- **The three list pages run on the #73 collection layer** (#77 — Contacts, Companies,
  Tasks; the pipeline board keeps #21's own bar). The layer filters an **in-memory** array
  and has no server-search hook, so a facet over a server-paginated slice would silently
  lie about what matched — the pages therefore sweep their whole corpus with
  `usePageAssembly` first, exactly as the blueprint does and as #21's then-unpaginated
  board already did. **Since #59 the board sweeps too** (`crm/pipelineAssembly.ts`), on
  this same wire format, which is what settled the fork issue #59 was told not to
  re-litigate: pagination is transport, facets stay client-side over the complete corpus,
  and there is no second filtering model. **The sweep is a KEYSET walk, never OFFSET**: `sort=id` is the only total,
  immutable, append-only order the endpoints offer (`created_at` is DESC on both, so a
  mid-sweep insert would displace the window), and `after_id` is honored ONLY with it —
  any other pairing raises rather than paginating wrong. `hasMore` comes from asking for
  one row more than a page holds, never from `total`: `list_contacts` runs its COUNT and
  its page SELECT in **two separate transactions**, so an insert between them would make a
  `total`-based test report "done" and truncate the corpus. The cursor is the *window*,
  not a filter — it deliberately does NOT reach the COUNT, exactly like OFFSET, so `total`
  stays the size of the whole matching set. Continuation pages skip the COUNT altogether
  (the sweep never reads it, and re-counting on every page would put a scan of the whole
  filtered set on the heaviest read path in the app); the skip is keyed on the CURSOR and
  not on `sort=id`, because the sweep's first page is indistinguishable from ordinary
  `?sort=id&offset=N` pagination, whose caller does need the total. Residual and stated rather than argued away: a
  row whose SERIAL id was allocated before the cursor passed it but which commits after is
  missed until the next sweep; the answer is each page's **Refresh** control, which is also
  what replaces the incidental reloading that server-side filtering used to give for free.
  Writes **patch from the server's own response body** through `crm/usePatchableAssembly.ts`
  — entries are **merge patches** (a response lacking a derived column must not blank it)
  plus a `remove(id)` tombstone (CakeCRM hard-deletes where the blueprint archives), and
  `retry()` **clears the overlay before re-sweeping** or a pre-retry patch merges back over
  the fresh rows. Two backend reads were made list-shaped to make that honest: `get_task`
  gained the contact/deal joins (every task write returns it), and `get_contact_detail`
  carries `last_contact_at`. A failed write is triaged on the status `ApiError` already
  carries (#55): a definite 4xx wrote nothing, anything else may have committed and been
  lost, so it re-sweeps — and a **404 is the one 4xx that proves the CORPUS wrong even
  though it proves the write never happened** (`rowIsGone`), because another seat, the
  assistant or Telegram deleted the row behind a swept list's back; that row is dropped
  rather than left to fail on every click. EVERY mutation path reports an uncertain
  outcome, not only the obvious ones — the detail pages host the EDIT forms, and an
  activity row or a note is one of the two signals behind `last_contact_at`. Because a
  swept corpus otherwise never reloads on its own (any keystroke used to round-trip and
  pick up other writers incidentally), returning to a backgrounded tab re-sweeps once the
  corpus is older than `CORPUS_MAX_AGE_MS`, alongside an explicit Refresh control. A write
  the assistant makes while the list is open is still invisible until one of those fires —
  the accepted ceiling, stated rather than hidden.
  The one write no patch can express is completing a **repeating**
  task, which spawns its next occurrence server-side (#70) — that path re-sweeps, decided
  from the SERVER's copy of `repeat`, never the pre-write one.
  **Last contact** has no column behind it: it is derived per contact from the same two
  signals `analytics_service.get_contact_staleness` reads (activity_log + un-archived,
  non-housekeeping chatter) under the same alias, via an index-driven `LATERAL` — NOT
  `get_pipeline`'s grouped subquery, which aggregates the whole table once per query and
  can afford to only because it runs once for a whole-corpus read. That rule is now
  written into `get_pipeline` itself: #59 gave it a LATERAL twin
  (`_PIPELINE_LAST_ACTIVITY_LATERAL`) used by the keyset page, while the unbounded board
  and the tool's window keep the grouped form. Edit one twin, edit the other — an
  integration test pins that they return the same `last_activity_at`. `NotesThread` gained an
  optional `onChanged` so a note added on the detail page reaches the list's column.
  Both surfaces now exclude `provenance_service.confirm`'s housekeeping notes via
  `scoring_service.HOUSEKEEPING_NOTE_LIKE` (public since #77) — the assistant confirming an
  AI-populated field was silently resetting a contact's staleness clock.
  **Contacts and Companies keep ROUTED detail pages**, which is a deliberate refusal of the
  issue's "detail in the `CollectionDetail` shell" — though only one of its two reasons still
  holds. The Z-ORDER objection does NOT: `DetailModal` was `z-50` against a `z-40` launcher, but
  #75 gave it an opt-in `underLauncher` (`z-50 dock:z-[39]`) that `CollectionDetail` passes
  unconditionally, so a modal detail now ducks under the launcher rather than covering the very
  records that publish assistant context (#14). What survives is the SHAPE: the shell is
  `max-w-2xl` where these are full-width working surfaces, and four other surfaces deep-link to
  `/crm/contacts/:id`, which a modal cannot be. Instead **the route is the selection**: `contacts/:id?` is ONE route
  rendering the list page, which renders the detail when the segment is present. What
  matters is that the element TYPE never changes, so the assembly stays mounted and
  open → back does not re-sweep (pinned by `ContactsPage.test.tsx`, verified to fail
  against the pre-#77 two-component shape). The cost is the shell's ‹ › record navigation.
  **Tasks DOES use `CollectionDetail`** — it has no route to preserve and its detail was
  already a modal over the launcher. Its "Open / Done / All" control is a **custom** facet,
  because only `CustomFacetDef` carries a `defaultValue` and it must default to an ACTIVE
  state to reproduce the old Pending-by-default page; a layer *toggle* would have been
  wrong twice (the hook never filters rows on toggles, and two states cannot express the
  Done-only tab that existed). Its due buckets use the LOCAL day via `ymd`, fixing a UTC
  drift that made an evening "due tomorrow" read as "due today" — and the day itself is
  `useLocalDay`, **state that advances on a timer aimed at local midnight**, fed into the
  configs so the dependency is real. Reading the clock inside a predicate is necessary but
  NOT sufficient: a predicate only runs when React re-renders, and time passing is not a
  render, so a tab left open overnight would keep yesterday's boundaries and stop flagging
  anything overdue. Its `now` is derived FROM the day string (at local noon, clear of both
  DST edges) so the two cannot disagree, and it re-arms on a monotonic tick rather than the
  day value, because `setState(sameValue)` is a React bail-out that would strand a clock
  stepped backwards. Tasks' default sort is a **composite** `open_due` key, since the layer
  sorts by one value per field and "All" would otherwise interleave done and open tasks the
  way the old tab bar never did. `OwnerScopeToggle` is
  **deleted** — an Owner facet with an Unassigned bucket replaces it and can select any
  owner, hiding itself on a single-seat install the same way.
- **Reports is a top-level surface with one report: the company rollup** (#144,
  `backend/crm/report_service.py` + `frontend/src/crm/{ReportsPage.tsx,companyRollup.ts,
  components/{CompanyRollupReport,CompanyTimeline}.tsx}`). Pick one company and get
  everything the CRM knows about it on one scroll — header, exact chips, every deal and
  every contact expandable in place, then a merged notes-and-activity feed. Keyless and
  read-only; nothing on the page writes.
  **The timeline is this repo's first two-source merge, and its ORDER BY has three terms for
  a reason worth stating once.** Notes live in `crm_chatter` and events in `activity_log` —
  two tables with INDEPENDENT `SERIAL` sequences — so note #7 and activity #7 both exist and
  `ORDER BY created_at DESC, id DESC` over the `UNION ALL` is **not** a total order: under
  LIMIT/OFFSET that tie puts one row on two pages and drops another. #58's scanner cannot
  catch it, because it judges the FINAL term's NAME and `id` is in its allowlist — the same
  blind spot its own docstring records for a join that leaves a parent `id` non-unique. So
  the reader projects a `source` discriminator and orders
  `created_at DESC, source DESC, id DESC`: `(source, id)` is unique by construction, and
  `id` stays last so the static sweep still passes on merit rather than by exemption.
  `test_timeline_order_is_total_across_both_sources` pins the term list exactly, and fails
  deterministically if `source` is dropped. **Any future UNION reader owes the same
  treatment** — the scanner will wave it through.
  Paging is LIMIT/OFFSET and its contract is narrow **on purpose**: deterministic while the
  matching set is unchanged, not immune to concurrent writes (a row archived behind the
  cursor shifts the rest up and is skipped; the client's `(source, id)` dedupe hides
  duplicates but cannot recover a skip). Keyset paging on the same triple is the stated
  upgrade path.
  **Archived is opt-in, and that makes `report_service` the THIRD sanctioned hole in the
  `LIVE_PREDICATE` sweep**, in the same shape as `search_deals(include_archived=)` and #83's
  `get_pipeline(include_archived=)`. `include_archived` widens exactly two things — archived
  deals and archived NOTES — and deliberately does not govern contacts: `contacts.status` is
  not a sweep, so contacts are always returned and rendered marked, matching
  `get_company_detail`'s documented asymmetry. `summary.contact_count` is narrower still —
  `status = 'active'` only, so BOTH `inactive` and `archived` are out — because the chip it
  feeds says "Active contacts" and that word has to be true; the section below lists every
  contact and states its own total. Two questions, two honest numbers.
  **Activity attribution is mutually exclusive, and the near-miss is worth recording**: the
  contact bucket tests an absolute `deal_id IS NULL`, NOT membership of the deals being
  displayed. Those look equivalent and are not — an archived deal (or one past the child
  cap) is absent from the displayed set, so its activities silently reappeared under the
  contact with archived history switched OFF. The first draft shipped that; the Codex plan
  review caught it. Because the rule is absolute, a deal wins GLOBALLY rather than only among
  the deals on screen: an activity naming this company's contact and ANOTHER company's deal
  belongs to that deal and appears once, on the OTHER company's rollup and timeline. That is
  better than the blueprint, whose per-company predicates dropped such a row from both — here
  every activity has exactly one home. (The code documented the blueprint's behaviour while
  implementing this one; the Codex verify turn caught the contradiction.)
  **The open-value chip declines to lie about currency.** `deals.currency` is in
  `_DEAL_USER_WRITABLE`, so USD-only is a convention here and NOT an enforced invariant, and a
  bare `SUM(value)` across currencies is simply a false number. `summary.open_deal_currency`
  is the single currency every open deal agrees on, or NULL when they disagree, and the chip
  renders "Mixed currencies" instead of a total in that case; per-deal values render in their
  own currency through `Intl`, falling back to the raw code because the column is free text.
  The rest of the app still sums and prefixes `$` (`get_company_detail` documents that as a
  single-currency sum), which is consistency rather than correctness — this report is the
  first surface to decline it.
  **The headline chips are their own aggregate over the full tables**, never a reduction of
  the capped child lists — reducing the lists lets a cap change a headline number, and with
  archived deals competing for the same window, enabling MORE history could make the
  open-deal count go DOWN. **Custom fields and open tasks ride the payload**, read once per
  entity type (`field_service.list_field_definitions` + `get_field_values_batch`) rather than
  once per row: that is what satisfies the issue's load-bearing "every field, including the
  unset ones" — a values-only read cannot express an unset field — and what keeps one
  "Expand all" from becoming ~150 requests. Caps are 200 children / 25 activities and tasks
  per record, each with a `+1` probe and an explicit truncation flag, because a child now
  carries its own activities, fields and tasks, so the cap bounds a payload rather than a row
  count.
  Three components derive their state from ONE object tagged with the request it belongs to,
  rather than resetting several `useState`s at the top of an effect. That is not only what
  the stricter `react-hooks` ruleset demands (`set-state-in-effect`, fixed rather than
  suppressed): reset-then-fetch is two steps, so a response from the previous archive filter
  could land between them. Row expansion is local to the report and the report is mounted
  `key={companyId}`, so switching account collapses the previous one's rows while toggling
  the filter does not. **`RecordCombobox`'s `create` became optional** here — a report must
  never create a company — gated on the single `canCreate` derivation every other create path
  already reads.
  A **Reports nav entry lives in TWO places**: `CrmLayout.NAV_ITEMS` (rendered twice from one
  list) and `shared/MobileMenuDrawer`'s own separate `items` list. Adding a nav destination
  means editing both.
- **The dashboard leads with a Today panel** (#130, `backend/crm/today_service.py` +
  `GET /api/crm/dashboard/today` + `frontend/src/crm/{todayPanel.ts,components/TodayPanel.tsx}`):
  ONE ranked list of what needs the viewer today, above the stat row, capped at 5 with a
  "+N more today" expander. Pure SQL and pure Python — identical with zero AI providers.
  The ladder is `1 starred · 2 RESERVED · 3 overdue · 4 reminders due today · 5 due today`;
  **rank 2 is emitted by nothing** and is held for hot+stale deals (#125), so that
  follow-up lands as an insertion rather than a renumbering of every rank below it.
  **One clock, and that is the mechanism, not a convention:** `get_today()` reads
  `gtd_common.today_local_str()` — the identical call `gtd_service.today_view()` makes —
  and the reminder window is derived FROM that captured day via the new
  `core.localtime.local_day_bounds()`, never a second clock read, so a request crossing
  local midnight cannot bound tasks to one day and reminders to the next. Those bounds are
  computed in Python rather than with SQL's `AT TIME ZONE` so `zoneinfo` stays the single
  timezone authority (Postgres ships its own tz database, and two copies of one rule drift).
  Building this exposed a real split it would otherwise have sat beside, so **#130 also
  moved three UTC-day decisions onto the configured day** — `get_dashboard_stats`
  (the "Overdue tasks" stat inches below the panel), `analytics_service.get_deal_health`
  (whose comment cited the dashboard as precedent) and `proactive.collect_digest` (which
  told a 6pm Central reader that tomorrow's tasks were due today). One defect, three
  sibling sites, one sweep; Weekly Touches deliberately stays UTC. There were no existing
  assertions to update — none of the three pinned the day at all, which is why the split
  survived — so `test_crm_today.py` adds deterministic ones and keeps all three together,
  because "is this task overdue" must not depend on which report asked.
  **Owner scope is a panel-local Mine/Everyone control**, hidden on a single-seat install:
  the issue named `OwnerScopeToggle`, but #77 deleted it and its replacement is a
  list-page facet, wrong shape for a compact card. Mine sends `owner_id=<me>`, Everyone
  omits the param (the `list_tasks` idiom — no flag, no magic value), and the tasks
  predicate is deliberately WIDER than `list_tasks`' strict `owner_id = %s`:
  `(owner_id = %s OR owner_id IS NULL)`, because unassigned work must appear in "my" view
  — someone has to catch it — badged with `useUsers`' existing `UNASSIGNED_LABEL` (#128's
  label convention).
  **Reminders are scope-invariant** and appear in every scope: they have no owner column
  and are install-wide by design (Phase B), which is the strongest form of that same rule.
  They wear no Unassigned badge, since that label invites an action that cannot exist for
  a reminder. Counts stay honest **structurally** rather than by a shared filter: the
  endpoint returns the FULL ranked list uncapped (`today_view()`'s precedent) and the
  client derives both the five visible rows and the "+N" from that ONE array, so there is
  no rows-vs-COUNT seam to disagree across. The ceiling is stated in the module docstring;
  the upgrade path is a probe-row `truncated` flag (#56's idiom), not pagination.
  The ladder is a **pure function** (`build_today_items`) with no I/O and no clock, so it
  is unit-tested with zero mocks while SQL keeps only membership. On the client, the panel
  re-sorts nothing — it renders `items` in server order — and even due labels compare
  against the payload's own `date`. It reloads on the **server's** next midnight, carried
  as an absolute `next_refresh_at`: `useLocalDay` fires at the BROWSER's midnight, which
  on a default install (`TIMEZONE` unset ⇒ server on UTC) is hours away, so keying on it
  would strand a tab on yesterday's list — the very failure that hook exists to prevent,
  merely relocated. That timer re-arms on a monotonic tick for #77's bail-out reason.
  Task rows navigate to `/crm/tasks` because **no task-detail URL exists** (the route is
  mode-routed); the row's complete-checkbox is how you act on one, and it calls the normal
  `PUT /api/crm/tasks/{id}/complete` so `_apply_task_update_cur`'s invariants (repeat-spawn,
  the GTD CHECK) hold for free. Checkbox and row-open are **sibling** controls, not a
  button nested in a `role="button"` row: nesting puts a control inside a control for AT,
  and Space on the checkbox would bubble a keydown and navigate away, which an `onClick`
  `stopPropagation` cannot prevent. Badges are text-only in existing tokens — deliberately
  no `tint()` background, which would owe an entry in `inkContrast.test.ts`'s surface
  registry (#68).
- **Tasks have two modes over ONE store** (#70), and **GTD is the default** (#102).
  `crm_meta.task_mode` is `normal` or `gtd`; GTD is a presentation + tool surface over the *same* `tasks` rows, never a
  second table — which is what keeps the dashboard counts, contact/deal rollups,
  `crm_get_stale_deals`' open-follow-up check, the heartbeat nudge and the CRM reset
  aware of GTD todos, and makes switching modes a **no-op** (nothing migrates,
  instantly reversible). `tasks` gained `status` (7 GTD values), `star`, `context`,
  `tags` (JSONB — deliberately unlike `contacts.tags` TEXT, because the facet filters
  with `jsonb_exists`), `repeat` (+ `weekdays`/`every:N`), `auto_star_on_due`,
  `project_id` → new `task_projects`, `completed_at`, `source`; plus the one
  invariant that makes one store safe — a DB CHECK `completed = CASE WHEN status =
  'done' THEN 1 ELSE 0 END`. **Every task write funnels through
  `service._apply_task_update_cur`** (`SELECT status … FOR UPDATE` → write both
  columns together → `_spawn_next_task_occurrence_cur` on a real done-transition), so
  `complete_task`/`update_task` are thin adapters and completing a repeating task from
  the plain normal-mode checkbox still spawns its next occurrence. The spawn reads the
  POST-update row (clearing `repeat` while completing must not spawn) and takes ONE
  clock read shared with the auto-star comparison. The migration backfills BEFORE
  adding the CHECK, and `seed_data.py` DERIVES `status`/`completed_at` from
  `completed` — hand-writing either would break first-run seeding. GTD's `dropped`
  status is a soft delete that is neither done nor open, so `NOT_DROPPED_TASK(_T)` is
  swept across the seven open-task query sites exactly like `LIVE_TASK_PREDICATE`;
  the is-the-CRM-empty counts deliberately do NOT filter it. `task_projects` is
  FK-referenced by `tasks`, so it MUST stay in both TRUNCATE variants. Tool surfaces
  SWAP by mode: `crm.gtd_tools.get_gtd_tools()` returns `([], {})` in normal mode (the
  `get_gmail_tools` precedent) and `get_crm_tools()` hides its five task tools in GTD
  mode — advertising both would give the model two vocabularies for one store — while
  executors stay reachable in both so a call proposed just before a flip still
  resolves. `crm_update_task`/`crm_delete_task` close the long-standing parity gap in
  BOTH modes. `identity.GTD_GUIDE` appends to the static prompt only in GTD mode (a
  rare, deliberate cache invalidation, same class as editing the personality — and since
  #102 that is the steady state for nearly every install rather than a flip-flop), and the
  heartbeat prompt names `todo_list` instead of `crm_list_tasks`. Telegram gains a
  deterministic `capture …` intercept that runs BEFORE the model — zero AI cost, works
  with no provider configured.
  **Triage & edit-sheet parity** (#150) fixes four defects daily phone use exposed. A
  native `<input type="date">` ignores `placeholder`, so the empty due-date box carries an
  opaque **"Add due date" overlay**, cleared by a value or by focus — and by `onChange`
  too, because the write that follows a pick sets `busy`, React does not dispatch to a
  disabled target, and `onBlur` would therefore never run (the flag is invisible while a
  date is set and bites the moment it is cleared). **The date commits on BLUR, not on change**, and
  through its own write rather than `patch`'s. A date input reports a COMPLETE value the
  moment every segment parses, so it emits one on nearly every keystroke — `0002-12-24` after
  the year's first digit on an empty box, `2026-01-01` after the month's on a populated one —
  and `patch` sets `busy`, which DISABLES the input, so that first write ate every remaining
  keystroke and the truncated date was what reached the server, silently. Blur-committing
  means the field is never disabled while it still has focus, so what is written is what is on
  screen. The truncation predates this port and was found by the evidence run against the real
  app, which A/B'd it against `main`; the cue is what invites people to type in the box, so it
  is fixed alongside it. `useSerialCommit` is shared by the notes box and the date for the same
  reason both need it — two writes to one column, in flight together, land in whichever order
  the server picks — and a resolving write flushes both, so filing carries them. The trade-off
  commit-on-blur brings, measured rather than assumed: an entry that is never blurred is lost
  on a hard reload. Every path that ends the interaction inside the app — filing, Edit, Delete,
  promoting another card, Tab, clicking anywhere — blurs and commits, so only closing the tab
  with focus still in the box loses it. The same trade-off the notes box already makes. `pendingDue` mirrors the existing
  `pendingTitle` so the CONTROLLED field does not revert to the prop for the length of the
  refetch, and `current` — what the Edit sheet is handed — carries title, notes and date
  from the card's view, never the lagging prop. **The card renders its own view of the
  record, not the `todo` prop**, which lags: between a write being sent and the parent's
  refetch landing, the prop still describes the record as it was. That view plus the three
  optimistic overrides live in ONE `useReducer` (`CardState`), mirrored into a ref that
  `apply()` advances with the same pure reducer before dispatching. The row IS the notes
  baseline — dirty is `notesDraft !== row.notes` — because the only thing that moves the row
  is adopting a newer one, and a newer row is by definition what the server holds. Both halves are
  load-bearing. The reducer is what makes every decision read the state as it is NOW: these
  handlers run from asynchronous callbacks, and a callback closes over the render that
  created it, so a title save resolving after the user started typing notes would compare
  against the empty draft it captured, call the box clean, and **overwrite what was typed**.
  The synchronous ref is what lets a continuation read the card before React commits — the
  sheet payload is built from it when the sheet actually opens, never captured at click time,
  because opening waits on the notes flush and that flush can answer with fields someone else
  changed. `adopt()` takes whichever row is NEWER, ordered on `updated_at` via `isNewer` — a
  VERSION comparison, and it has to be: a write's response is newer than the prop the parent
  still holds, so comparing content would read that lagging prop as an outside change and
  rewind the notes box the instant a save succeeded, the next blur writing the pre-save text
  back over it. `isNewer` breaks a millisecond tie on the **fractional
  seconds**, because `Date.parse` truncates there while every task write stamps `updated_at`
  from `datetime.now(timezone.utc).isoformat()` — microseconds — and equal means reject.
  Reading the fraction rather than the whole string is what keeps it independent of how the
  zone is spelled: `Z` sorts after `+`, so a lexical compare calls the same instant written
  two ways a newer version and adopts the card's own echo. **An override is released when its OWN write
  settles**, success or failure — never because an adopted row disagrees with it. A row is not
  evidence about a write still in flight, and the disagreement rule cannot tell "someone
  changed this elsewhere" from "this row was committed before my write was": an earlier write
  of the card's own, answering first, carries exactly that disagreement, so the rule flashed
  the field back to the value the override exists to hide and handed the Edit sheet the old
  one, whose full-row save then reverted the change. Releasing on settle also closes the
  original hole — a successful write pinning its own value for the life of the card, which
  `pendingTitle` had before this port. There is deliberately **no** "the write was
  acknowledged, so trust the text over the version" rule for notes: `_now()` is stamped under
  the row's own `FOR UPDATE` lock, so `updated_at` is monotonic per row and a held row newer
  than our response was committed AFTER our write. Either it already carries our text, making
  such a rule a no-op, or a later write replaced it — and there, marking the box clean would
  strand a paragraph the server does not have, silently. Leaving it dirty re-sends it, the
  same last-write-wins rule the unsaved-draft case follows. And `star`/`project_id`,
  written straight through and never rendered optimistically, stay current for the sheet only
  because every write path adopts the response it already gets back — `createAndAssign`'s own
  `project_id` write included; without that the sheet opens on pre-write values once `busy`
  clears and its full-row save reverts them. The known limit of ordering on `updated_at` is
  that JOINed columns (`project_name`, `deal_title`, `contact_name`) never bump it, so a
  rename made elsewhere reaches a mounted card only on the next reload — cosmetic, and the
  alternative is versioning rows this card does not own. Step 2 gains an inline **Notes** textarea
  committed on blur through `flushNotes`: single-flight with ONE trailing run (two writes
  to the same column, in flight together, land in whichever order the server picks), and
  deliberately NOT taking the card-wide `busy` — the click that files the item is what
  blurs the box, so a shared flag would swallow that very click. Any *resolving* write
  flushes it first, so the note is on the row before the item leaves the inbox, but is
  **never gated on the result**: filing is this card's one exit, and a note the server keeps
  rejecting would otherwise trap the item in the inbox forever. The textarea replaced the
  read-only preview under the title rather than joining it. Step headings move from
  `text-xs`/`text-muted` to `text-sm`/`text-charcoal` (`ck-ink`, the primary body ink
  `core/theme/inkContrast.test.ts` already pins at AA on `ck-card` in both themes). The
  edit sheet's Context field becomes a `<select>` over the known contexts plus a
  "+ New context…" hatch, matching the triage card: mobile browsers do not reliably render
  a `<datalist>` on a POPULATED text input, so the old picker was invisible until the field
  was cleared. Option values are **indices**, so a context literally named `__new__` stays
  selectable, and the SELECTED context always gets an option — the shared meta loads async
  and a refresh can drop a value, and a select matching no option while `save()` submits the
  hidden string is worse than an extra option. The one place this stays **simpler than
  the blueprint** is the commit primitive: the blueprint routes notes through a shared
  `useAutoSave` (its `shared/autosave`, reused across several surfaces), where this card has
  a local `useSerialCommit` — single-flight with one trailing run, queued on the **tail** of
  the chain so a third caller cannot wake alongside the second and fire a duplicate. Porting a
  shared cross-app primitive for two fields on one card was not worth it. The `updated_at` ordering was: an
  earlier cut of this port adopted only from the prop and compared content, and that produced
  the save-rewind, the pinned override and the stale-star defects the paragraph above
  describes.
  **#102 made GTD the default and the fail-safe.** Four readers resolve the mode —
  `service.get_task_mode()` plus thin `_task_mode()` wrappers in `assistant.identity`,
  `heartbeat.service` and `telegram.service` — and all four degrade to `gtd`, because a
  row we cannot read says nothing about what the user chose, so the honest guess is the
  experience a new install gets. A test asserts the four agree, so they cannot drift.
  **The migration's UPDATE is the whole mechanism, not a policy add-on layered on a
  default change — do not "simplify" it away.** `crm_meta`'s singleton row is inserted by
  the `crm_core` migration long before `task_mode` exists, so `ADD COLUMN … DEFAULT` was
  consumed once at ADD COLUMN time and nothing ever inserts `crm_meta` again (every writer
  UPDATEs it; both TRUNCATE sweeps exclude it). Flipping only the column default would
  therefore change nothing on any install, fresh ones included — which is also why the
  issue's "fresh-installs-only, no backfill" option was unreachable without mutating an
  already-applied migration. `SET DEFAULT 'gtd'` is kept anyway so the schema does not
  contradict the product default for whoever next adds an inserter, and the integration
  test pins BOTH halves separately. Rows already at `gtd` are untouched; a user who
  deliberately chose `normal` in the four days since #70 is flipped once and re-toggles
  (Will's accepted trade on #102) — one click, since switching migrates nothing.
  The mode now has ONE owner in the UI: `CrmLayout` holds it and publishes both
  `TaskModeContext` and `TaskModeSetterContext`, so `TaskModeCard` writes through the
  setter instead of keeping a second copy. Before #102 the card's local state left the
  layout's context stale, so switching mode in Settings did not take effect on
  `/crm/tasks` until a full page reload — which would have broken the very opt-out that
  makes flipping every existing install acceptable.
  Neither no-login surface consults `task_mode` (they gate on `todo_capture_token` /
  `todo_web_enabled`), so this flip does not widen them.
- **The two no-login todo surfaces are asymmetric, and only ONE of them is opt-in** (#70,
  ported from chatty — the heading used to say both were, which the body below has always
  contradicted). Neither consults `task_mode`, so #102's default flip leaves both exactly
  as they were. `/capture[/{token}]` is **write-only** (creates one inbox row, returns only
  its id — no read endpoint exists on it) and is reachable while no token is set;
  `/todo[/{token}]` serves the **whole todo app read+write** and is **off** until
  `todo_web_enabled`, which mints a token in the same action rather than publishing
  the list at a guessable address. Both mount ONLY `gtd_router.build_router` — the
  token reaches todos and nothing else — and both carry `core/ratelimit.IPRateLimiter`
  (a separate strict budget burned only by WRONG tokens), 404-never-401,
  `hmac.compare_digest` on **bytes** (a non-ASCII probe must be a 404, not a 500),
  `no-store` + `noindex` on every response *including* the unbuilt-frontend 503, and a
  body-size check BEFORE JSON parsing. All four routers mount before the SPA catch-all
  in `main.py` or the catch-all swallows them. Tokens are clamped to `[A-Za-z0-9_-]`
  and rejected if they equal a page slug (`RESERVED_TODO_WEB_SLUGS`) — `todos` is
  reserved because `/api/todo-web/todos/…` would otherwise shadow the API mount.
  Documented in SECURITY.md.
- **API keys are entered in-app, encrypted at rest** (Fernet; key from env →
  OS keychain → file fallback) — never as env vars.
- **Backend tests** live in `backend/tests/` (config in `backend/pytest.ini`,
  `asyncio_mode = auto`). The default `pytest` run is **hermetic** — pg helpers and
  provider SDKs are mocked, encryption runs against a per-test key — so the CI gate
  needs no database. Tests that need a real PostgreSQL are marked
  `@pytest.mark.integration` and deselected by default (`addopts = -m "not
  integration"`); run them with `pytest -m integration` and a reachable
  `TEST_ADMIN_DSN`. No `skip`/`xfail`/`# noqa`/`eslint-disable` — fix root causes.
  A repo-wide **guard** test — one that sweeps the tree and asserts a property
  (`test_route_authz`, `test_gmail_guard`, `test_prompt_genericization`,
  `test_query_determinism`) — must itself be falsifiable, because a sweep that quietly
  stops matching is a permanent green, and a permanent green is worse than no guard
  since it reads as coverage. So a new one owes two things beyond the property itself:
  a self-test pinning its detector on synthetic input (both a case it must flag and a
  case it must not), and an assertion that it still reached the real code — per module
  or per registered item, never one repo-wide total, which the largest package satisfies
  on its own. `test_query_determinism` and `test_prompt_genericization` carry both
  (`test_the_guard_actually_catches_a_leak` is the latter's detector self-test);
  `test_gmail_guard`'s source sweep currently has neither and is worth hardening the
  next time it is touched.
- **Frontend tests** are **vitest** (`npm test` → `vitest run`), landed with #73. Config
  is a STANDALONE `frontend/vitest.config.ts` — vitest reads it *instead of*
  `vite.config.ts`, so the production build config stays untouched and tests skip the
  react/tailwind plugin pipeline. Tests are **co-located** with the code
  (`src/**/*.{test,spec}.{ts,tsx}`), not in a separate tree. The default environment is
  `node`; a DOM test opts in with a per-file `// @vitest-environment jsdom` docblock —
  add them that way, never by flipping the default. Components are rendered with plain
  `react-dom/client` `createRoot` + React's `act`; there is deliberately **no
  `@testing-library`** dependency (`vitest` + `jsdom` are the only test devDeps). Three
  fail-closed guards are load-bearing and must not be deleted as "defaults":
  `passWithNoTests: false`, `allowOnly: false`, and `expect.requireAssertions: true`
  (the last is NOT a vitest default) — a runner that reports "0 tests / exit 0" is a
  permanent silent green, which is worse than no runner because it looks like coverage.
  `env: { TZ: 'America/Chicago' }` pins the timezone so local and CI agree — deliberately
  **not** UTC: `crm/pipelineFilters.ts` derives `ymd` from local-date getters because
  `toISOString()` drifts a day in US evening time, and under a UTC runner that distinction
  disappears, so a test pinning it could never fail.
- **The frontend boot path is code-split, and the split is structural** (#149, port of
  cake_os #2066). `main.tsx` renders **`Root.tsx`**, not `App`: `Root` is the one dispatch
  point between the no-login `/todo` surface and the CRM, and **both branches are `lazy()`**,
  so a `/todo` visitor downloads the todo app and never a CRM chunk. Measured: the entry
  chunk went 1,001 kB → 187 kB raw (291 → 60 kB gzip) and the PWA's cold load 1,044 → 346 kB
  raw (300 → 104 kB gzip). `/capture` is untouched — it is backend-rendered HTML
  (`crm/todo_capture.py`) that never loads React, unlike upstream, so the issue's premise is
  false for that half and there is no capture chunk. **Rules: adding a route means adding a
  module-scope `lazy()` const in `App.tsx`, never a static page import** — the bundler follows
  a static import into the shell chunk every CRM visitor downloads, the login page included.
  The ten GTD pages come through ONE module, `crm/gtd/pages.ts`, imported statically by
  `PublicTodoApp` and dynamically by `App`, so both mounts share a single GTD chunk instead of
  handing the phone ten. `TasksModeRouter` keeps `TasksPage` lazy inside it, because
  `TasksPage` drags the #73 collection layer and `@dnd-kit`, which Contacts/Companies/Tasks
  **and the Dashboard** all reach through `shared/collection → KanbanView` — not only
  `PipelinePage`, which is what a one-page reading of the graph would suggest. And
  `AssistantLauncher` lazy-loads `AssistantPanelBody` by its **leaf path** (never the
  `assistant` barrel) the moment `aiReady` is true: the drawer still mounts once and stays
  mounted, so chat state survives open/close; the ~347 kB chunk (react-markdown +
  highlight.js) simply arrives before the first open, and is never requested at all on a
  keyless install.
  **Three route-level Suspense boundaries**, each for its own reason — plus the drawer-local
  fourth described above: `Root` (cold boot of either surface), `App` above `<Routes>` with
  `ConfirmHost`/`ToastViewport` deliberately OUTSIDE it (a toast in flight or an open confirm
  dialog must not be replaced by a spinner), and **`CrmLayout` around `<Outlet />`** — the one
  upstream did not need, because our layout is a nested route and the task-mode flip
  (`null → 'gtd'`) is a plain `setState` in a `.then`, **not** a router transition, so without
  it the whole shell would swap to a spinner while a task chunk loads. That last boundary has
  a second invariant beyond "the Outlet is inside it", and it is the one worth stating: the
  nav, sign-out and launcher must stay OUTSIDE. Hoisting the boundary to wrap the whole layout
  body keeps the Outlet nested and satisfies every positional check while turning each route
  chunk load into a full-shell spinner, which is the failure it exists to prevent — so it is
  pinned twice, positionally in `bootSplit.test.ts` and behaviourally in `CrmLayout.test.tsx`. React Router 7 wraps navigations in `startTransition`, and
  React will not re-show an already-revealed fallback during one — so an in-app click to an
  unvisited route keeps the old screen up and shows **nothing** while the chunk downloads (the
  NavLink active state does not move either, since it reads the deferred location). The
  fallbacks are seen on a COLD LOAD, not on navigation. That silent wait is accepted rather
  than unnoticed: it is the price of not flashing a spinner on every first visit to a page, and
  the real answer is a navigation progress indicator, which is a design change rather than a
  rider on a bundling one. (`useTransitions={false}` on `BrowserRouter` is a real prop and does
  make the fallbacks render on navigation — it trades that flicker back in.)
  **A route chunk that REJECTS is a different matter, and it is contained.** Suspense catches a
  PENDING import and never a rejected one, so after a deploy — which replaces `dist` wholesale,
  404ing all 14 route chunks at once — a rejection would walk past `CrmLayout`'s Suspense, past
  `App`'s, past the toast and confirm hosts, and take the whole shell down. Hence a
  **`ChunkErrorBoundary scope="route"`** around that Outlet, and a **`scope="panel"`** around the
  assistant drawer. The scopes answer two questions, and both matter: how much of the screen the
  failure owns, and whether the user is BLOCKED by it. A route failure blocks (they asked for
  that page) so the one-shot reload still applies, drawn small so the nav survives; the drawer
  does not block — it loads in the background with the drawer shut — so `panel` is the one scope
  that never auto-reloads, because reloading to recover a panel nobody opened would destroy the
  half-typed form the containment exists to protect.
  `core/components/ChunkErrorBoundary` wraps `Root`'s Suspense and is the app's **first
  general error boundary**: it catches every render error below it (a themed card with a
  Reload button beats the blank `#root` this app produced until now) but **auto-reloads only
  on a chunk-load error**, at most once per tab session
  (`sessionStorage['cakecrm_chunk_reload']`, written then read BACK — blocked storage means
  "show the button", never "assume this is the first try"), because every deploy replaces
  `frontend/dist` wholesale in the image and a tab open across one asks for hashes that no
  longer exist. It additionally **declines to auto-reload while `navigator.onLine` is false**:
  the browser words an offline failure identically, and reloading there discards a page the
  user can still read for the browser's own offline screen. `BootFallback` is the one loading
  state (`ck-*` token classes, `role="status"`, a `border-ck-accent-text` spinner per #54);
  `ProtectedRoute` renders it too, so an auth check and a chunk load look like one app.
  **Two guards enforce all of the above, and they answer different questions.**
  `src/bootSplit.test.ts` reads SOURCE — via `import.meta.glob(…, { query: '?raw' })`, NOT
  `node:fs`, which fails `tsc -b` under `types: ["vite/client"]` — parses imports with the
  TypeScript AST, pins per-file eager allowlists (**leaf modules only**; an allowlisted barrel
  is a hole this guard cannot see through, and `assistant/index.ts` plus
  `shared/{dnd,search,collection,listview}/index.ts` are the live examples), walks the real
  **transitive** graph from `PublicTodoApp`, and reports the two edges a static walk would
  miss: a dynamic `import()` or an `import.meta.glob` inside that graph. `src/bootSplitBuild.test.ts`
  builds the app in memory and reads the **emitted chunk graph**, because two real regressions
  leave every source assertion green — an allowlisted eager leaf (`LoginPage`) growing a heavy
  import, and a Vite/Rolldown upgrade merging the branches, since the whole topology is
  automatic (`vite.config.ts` declares no `manualChunks` on purpose). It asserts on SOURCE
  MODULES rather than chunk filenames: an earlier revision matched chunks by name and a
  content hash happened to contain the letters `dnd`. If the todo surface ever adopts the
  collection layer (the `ProjectsPage` follow-up), that PR removes the deny entry and records
  the measured size delta — a deliberate act, which is the point.

## Don't Do This

- Never add an email-send tool or widen Gmail scopes/capabilities beyond read +
  create-draft (see above).
- Never create runtime SQLite stores or ad-hoc schema — Postgres migrations own
  the schema. When a check-then-write spans reads and updates, do it in one
  transaction with `SELECT ... FOR UPDATE` (see `core/auth_2fa.py`).
- Never page a full-corpus sweep on a mutable order. `sort=id` is the assembly key on
  every list endpoint, `after_id` is refused with any other sort, and `hasMore` comes from
  an extra row rather than a `total` computed in a separate transaction (#77). A new list
  endpoint needs all three before a page assembles it. **#59 put the pipeline board on the
  same three rules**, and added the fourth that a *board* needs: a swept corpus is
  delivered in the server's PRESENTATION order, not the cursor order it arrived in —
  `PipelinePage` sorts columns by `lead_score` with a STABLE sort and relies on recency
  surviving beneath equal scores, which is the common case because `lead_score` is NULL
  until something recomputes it. Handing back `id ASC` would silently flip most columns to
  oldest-first, so `sweepPipelineDeals` re-sorts before it resolves.
- Never write `tasks.completed` or `tasks.status` outside
  `service._apply_task_update_cur` — a DB CHECK binds them, so any other writer is a
  constraint violation waiting to happen (#70). Adding a task READER means adding
  `NOT_DROPPED_TASK` to it too, unless it is deliberately counting every row.
- **Never cap a reader whose `ORDER BY` isn't a TOTAL order** (#58). A `LIMIT`/`OFFSET`
  over a non-unique sort key has no defined result — Postgres may break the tie
  differently on each execution, so a row shows up on two pages or on none. End every
  such `ORDER BY` on a unique term: `id` (matching the preceding key's direction, so
  the tiebreak reads the way the sort does), or a column that is already UNIQUE
  (`assistant_context_files.filename`, `assistant_messages.seq` within one
  conversation), or — for a grouped reader — the rest of the GROUP BY key
  (`find_duplicate_deals` orders by `title` **and** `contact_id`, because the group is
  the pair). Ties are the normal case, not an edge: `created_at`/`updated_at` default
  to `now()`, which is **transaction-start** time, so every row written in one
  transaction is byte-identical — a CSV import, `seed_data`, `merge_deals`' note
  copies. Uncapped readers carry the term too, so adding a `LIMIT` later can't quietly
  reintroduce the bug — which is why #59, server-side pipeline pagination, was
  `Blocked by: #58`, and #59 has since cashed that promise in: `get_pipeline`'s keyset
  page is a real capped reader now, and its **default** read stays uncapped and stays in
  `test_hardened_uncapped_readers_keep_their_tiebreaker`.
  Enforced by `backend/tests/test_query_determinism.py`, which AST-scans every non-test
  backend module (it reads f-strings and implicitly-concatenated literals). An ORDER BY
  assembled at RUNTIME is reported as `unknown`, never waved through: the exact set is
  pinned in `UNDECIDABLE_SITES`, keyed by enclosing function, and each entry owes a
  behavioral test on the SQL that reader really emits — so a reader cannot opt out of
  the guard by moving its ordering into a variable. Expect to edit that registry when a
  reader starts or stops interpolating its ORDER BY (#59 and #77 both touch such
  readers); the failure message says which way it moved and what to do.
- Never add a static page import to `App.tsx`, `Root.tsx` or `main.tsx`, and never import
  `AssistantPanelBody` or `TasksPage` statically from the shell — each puts that whole graph
  back in front of every visitor, and `src/bootSplit.test.ts` + `src/bootSplitBuild.test.ts`
  fail CI on all of them (#149). Add a module-scope `lazy()` const instead, never one inside
  a component body (that mints a new component type per render and remounts the subtree).
- Never add a route to `crm/gtd_router.build_router` that should stay private: that
  factory is mounted TWICE, and its second mount is the no-login public web app.
  Authenticated-only routes belong on the module-level `router` instead.
- Never commit TN Cheesecake internals: no real prospect/customer data, no TNC
  staff/product names, no internal hostnames or secrets. Ported prompts (Casey's)
  must be genericized. This repo goes public at launch and history is forever.
  Enforced by `backend/tests/test_prompt_genericization.py` (#22, widened repo-wide in
  #90), which scans **two** surfaces against **two** denylists, split by what a token
  IS rather than by which file holds it. `_FORBIDDEN` (= `_COMPANY + _VERTICAL +
  _BLUEPRINT`) covers the **model-facing payload** — the assembled system prompt,
  every tool name/description/schema, the heartbeat prompt, the UI starter chips. The
  narrower `_REPO_FORBIDDEN` (= `_COMPANY + _VERTICAL`) covers **every committed text
  file**, enumerated by `git ls-files`, so the scope is a file CLASS: a new `docs/`,
  `scripts/` or `.github/` file is guarded the moment it is *staged* — there is no
  directory list to remember to update, which is the whole point (the gap #90 closed
  let a hardcoded upstream org URL and six real upstream directory names reach CI
  green). `_BLUEPRINT` (`cake_os`, `casey`, `cake_crm_`) is the deliberate asymmetry: banned from
  the payload — a shipped product must not name what it was ported from — but
  legitimate in committed prose, since the Source Map and every port comment cite the
  blueprint by name. Deliberate exemptions live in `_REPO_ALLOW` as path → **{pattern:
  exact expected count}** + a written reason, and the count is the whole point: a
  file-keyed exemption would repeat the mistake the gitleaks bullet under "CI &
  Contributing" already records — it "exempts every finding in that file, including a
  real one" — and CLAUDE.md is the most-edited file in the repo, so an unbounded
  exemption *here* would be the widest hole of all. Entries exist ONLY for text that
  must talk *about* the denylist: this rule, a coach lesson quoting a token the guard
  was missing, a sibling guard's own literals. A count that stops matching reality
  fails CI **in both directions** — a stale or inflated allowance is caught as surely
  as a new occurrence — so turning the guard down takes a visible edit to that list
  rather than a bumped number. Scrub the file instead whenever scrubbing is possible.
  **No file is exempt, the guard included** — it holds a counted allowance for its own
  denylist literals like everything else, so the one file with the most licence to
  carry these strings is not also the one place nobody is watching. Surfaces beyond
  plain file *content* are covered because they leak just as permanently: every
  committed **filename** (for a compressed container like a .xlsx, whose bytes no
  decoder can read, that is the only surface there is), **invisible characters** (a
  name pasted out of Word or a PDF can carry a soft hyphen or zero-width space inside
  it and match nothing while reading perfectly — stripped from paths as well as
  bodies), and text in encodings a naive reader drops. On that last: a NUL-byte "is
  this binary?" probe silently skips **UTF-16**, exactly the shape a spreadsheet or
  CSV export of real customer names arrives in, while BOM-less UTF-16 of ASCII content
  is byte-wise *valid UTF-8* and so decodes "successfully" into NUL-interleaved mush
  that matches nothing. The decoder therefore never gives up, and covers the whole
  family in **one** move instead of guessing an encoding: it takes the BOM'd reading
  (UTF-32 tested before UTF-16, which share a two-byte prefix) or falls back UTF-8 →
  latin-1, and for anything NUL-bearing it *additionally* scans the bytes with the
  NULs removed. That one extra reading catches every fixed-width encoding of ASCII at
  once — UTF-16 and UTF-32, either byte order, BOM or none — plus a plain ASCII name
  sitting inside an otherwise-binary blob. Guessing instead meant a NUL-density
  heuristic, and that had a hole: most real binaries are NUL-dense too. Boundaries are
  `(?<![0-9a-z])`, **not
  `\b`** — `\b` counts `_` as a word character, so a token went invisible the moment an
  underscore followed it (`cake_os\b` misses `cake_os_prompt`; the company abbreviation
  vanished the same way inside `<abbrev>_internal`), which are precisely the shapes
  these names take in identifiers, filenames and env vars (six such bypasses were
  measured, and this very bullet tripped the guard by naming one). The scan
  reads the working tree, **not history**: tokens committed before a scrub stay in the
  log. `test_sync_intake.py` consumes `_REPO_FORBIDDEN` **by name** for the same
  reason: it used to hand-copy the blueprint regex in a `pattern != …` exclusion, so
  widening that pattern turned the exclusion into a no-op and failed that test — loudly
  and fail-closed, but on a file nothing was wrong with. Naming the class instead means
  both of #88's scans inherit every future widening automatically. It deliberately
  narrows what they scan (blueprint tokens are legitimate outside the payload) and
  keeps the rendered-issue-body scan, which is coverage no file scan can provide.
- Never import git history from cake_os or chatty — code arrives as clean snapshots
  in ordinary commits.
- Never merge a pull request — with exactly ONE exception, the **operator ship lane**:
  the `/auto-issues-ship-loop(-team)` skills may squash-merge `ready-to-ship` PRs and
  deploy-verify the Railway demo instance, under the grant registered **operator-side**
  in `~/.claude/ship-repos.json`. That grant is deliberately NOT in this repo — repo
  files are PR-writable and grant nothing; this bullet *describes* the lane, the
  registry *grants* it (see `docs/AUTO_ISSUES.md` → ship lane). Outside that lane, Will
  merges all PRs manually. Push feature branches and open PRs to `main`; never push
  directly to `main` after the initial seeding phase.
- Don't add per-agent/per-integration enable flags for core features — the CRM and
  its tools are always on; only AI features key off provider configuration.
- Don't hand-build what the blueprints already have — check `~/ai/chatty` and
  `~/ai/cake_os` first; port and adapt, don't reinvent.

## Issue Loop Conventions (mirrors CAKE OS)

- Work starts from a GitHub issue. Branch `feature/issue-N-<slug>` from `main`, PR
  back to `main`. The issue loop itself never merges; merges happen only by Will's
  click or the sanctioned ship lane (see "Don't Do This").
- Issue eligibility for the automated loop: open, unassigned, no `no-auto` label, and
  human-approved via the `greenlit` label (default-deny — an un-`greenlit` issue never
  enters the loop). Self-assign (or label `no-auto`) before working an issue manually.
- Respect "Blocked by: #N" lines in issue bodies — don't start an issue whose
  blockers aren't merged.
- See `docs/AUTO_ISSUES.md` for the operator guide (label vocabulary, terminal
  outcomes, bring-up sequence, and repo prerequisites like branch protection).
  `scripts/seed-labels.sh` idempotently creates/normalizes the loop's labels.
- **`sync-intake` issues are ordinary issues.** The cake_os sync bot (#23,
  `docs/SYNC.md`) files them when upstream CRM code changes, un-`greenlit` like
  everything else — default-deny holds, and the bot never marks its own work
  eligible. There is **no special case anywhere in the loop**: a greenlit intake
  runs the normal pipeline, and the port worker reads cake_os from the local clone
  at `~/ai/cake_os`, never from the issue body. Ports add a `SYNC_LEDGER.md` row.
- **Porting from cake_os: both sides are Postgres.** cake_os's CRM is PostgreSQL
  (no `apps/crm/db.py`; `core.postgres` helpers; `%s`), so the SQLite items in
  `docs/solutions/database-issues/cakecrm-sqlite-to-postgres-crm-port.md` apply only
  to the chatty-origin port, not to cake_os ports. What does bite every time:
  tenancy stripping (`user_email`/`owner_email` threads ~25% of upstream CRM
  functions and has nowhere to land here), the module-topology collapse (~20 cake_os
  service modules → CakeCRM's single `crm/service.py`, so a same-named file is a
  *candidate*, never a destination), the `apps.todo_gtd`/`apps.dimm` call sites, and
  the PII scrub. Full playbook in `docs/SYNC.md`.
- Keep this CLAUDE.md updated in the same PR as any change to architecture,
  conventions, or the rules above.

## Brand mark

- **The brand mark is a flat vector slice, `frontend/public/logo-mark.svg`** (a 100×100
  viewBox wedge seen three-quarter-on: card/raised faces, the two gold tokens as the top
  layer, maroon + accent-dark red as the crust — chosen so nothing sinks into the dark
  theme's ground — and a hairline maroon `vector-effect: non-scaling-stroke` on the top face
  (`stroke-width` 0.5 — one CSS px is TWO device px on retina), since a cream top on the
  cream page otherwise has no edge). Byte-identical copies serve as `frontend/public/favicon.svg`
  and `docs/brand/logo-mark.svg`; the explainer site (#163/#164) carries two more under
  `website/`. It replaced the 🍰 emoji at every site (`CrmLayout` nav fallback, `LoginPage`,
  `SetupPage`, the README heading). It descends from the sponsoring bakery's logo slice —
  an earlier cut used a pixel crop of that logo, unusable above ~64 px — and is deliberately
  NOT named after the dessert: that word is on the genericization denylist, filenames
  included. Edit the SVG, and every copy must be re-copied (they are not built), and the
  fixed-size PNG exports in `docs/brand/` (16–1024 px + light/dark lockups, rendered in
  Chromium, transparent, unpadded; `docs/brand/README.md` says how) regenerated with it.
  `frontend/public/icon-{192,512}.png` are the todo PWA's **maskable** icons the manifest in
  `crm/todo_pwa.py` has referenced since #70 without the files existing: the mark at 70% on
  the card colour, so a masked launcher's safe zone never clips it. The mark always sits
  LEFT of the "CakeCRM" wordmark, never above it, and never on a badge.

## CI & Contributing

- **CI** (`.github/workflows/ci.yml`) runs on every PR to `main` and on `push` to
  `main`, in three jobs: **backend** (`ruff check .` → import check → `python -m
  pytest -q`, from `backend/`), **frontend** (`npm ci` → `npm run build` → `npm run
  lint` → `npm test`), and **secret-scan** (gitleaks). The scan runs `gitleaks git .` over FULL history, so
  a false positive stays found forever once committed and cannot be fixed by editing
  the tip. `.gitleaksignore` records verified-false findings by exact
  `commit:file:rule:line` fingerprint, with the reasoning written down. Use that file,
  NOT a `.gitleaks.toml` allowlist keyed on commit+path — the latter exempts every
  finding in that file in that commit, including a real one. Validate any new entry
  with a **randomly generated** token, never a canonical doc example
  (`AKIAIOSFODNN7EXAMPLE` and friends are allowlisted by default and prove nothing).
  The alternative is a force-push, which this repo does not do. Backend lint config is `backend/ruff.toml`
  (select `F,E,W,I`; `E501` ignored); dev/CI tooling is pinned in
  `backend/requirements-dev.txt`; tests live in `backend/tests/`. The import check
  imports the app with no `DATABASE_URL` (the Postgres pool inits in the lifespan
  handler), so CI needs no database.
- **AI Code Review** (`.github/workflows/pr-review.yml`) is an **optional, advisory**
  check on PRs to `main`: it posts a review comment (marker `<!-- ai-code-review-bot -->`)
  when an `ANTHROPIC_API_KEY` Actions secret is set (model overridable via the
  `AI_REVIEW_MODEL` repo variable), and is a **green no-op** otherwise (forks, keyless
  repos). It must **not** be configured as a required check. It uses only the GitHub API
  (no checkout) and `on: pull_request` (never `pull_request_target`).
- **Contributing** — `CONTRIBUTING.md` covers dev setup and the checks. Contributions
  are under the **DCO** (`Signed-off-by`, via `git commit -s`), not a CLA, licensed
  AGPL-3.0 (inbound = outbound). DCO enforcement is the DCO GitHub App (installed once,
  manually, on the repo). Issue/PR templates live in `.github/`.

## Source Map (where ported things come from)

| CakeCRM area | Source |
|---|---|
| Product shell (run.py, auth, 2FA, encryption, config, Railway) | `chatty/backend/` + `chatty/run.py` |
| Accounts, roles, per-user 2FA, record ownership + per-rep analytics — **landed #60 (Phase A)** as `backend/users/` (`service`/`router`/`bootstrap`) + reworked `core/{auth,auth_2fa,config}.py` + `20260821100126_multi_user.sql` + `frontend/src/crm/{useUsers.ts,components/{TeamSettings,OwnerSelect,OwnerScopeToggle}.tsx}` (`OwnerScopeToggle` retired in #77 for a multi-select Owner facet). Phase B (assistant memory/chat partitioning, per-user Telegram, owner-routed notifications, owner-aware agent tools) is a separate plan. Corrects the issue's premise: cake_os uses `owner_email` TEXT with no FK, so this is an FK design, not a carry | New capability (no blueprint — `cake_os/backend/apps/crm/analytics_service.get_rep_performance` for the per-rep shape only) |
| DB-backed login credential + in-app password change (`auth_credential` singleton, `POST /api/auth/change-password`, `AUTH_PASSWORD_RESET` recovery lever) — **landed #78** as `backend/core/auth.py` + `frontend/src/crm/components/ChangePasswordCard.tsx` | New capability (no blueprint — back-port candidate to CAKE OS) |
| Postgres pool + migration runner | `cake_os/backend/core/postgres.py` |
| AI providers + pricing + setup wizard | `chatty/backend/core/providers/`, `chatty/frontend/src/setup/` |
| CRM core (schema, router, tools, smart import) — **landed #3** as `backend/crm/` + `frontend/src/crm/` + `frontend/src/shared/` | `chatty/backend/integrations/crm_lite/`, `chatty/frontend/src/crm/` |
| Assistant engine — **chat loop, tool registry, confirmations, uploads landed #4** as `backend/assistant/` + `frontend/src/assistant/`; **memory (facts + FTS) + dreaming (pure-algorithmic usage scoring + fact soft-archival) landed #5** as `backend/memory/` + `backend/dreaming/` (dreaming's archival unit is the fact row, not context files — CakeCRM has no file store; driven by #6's reminder tick) | `chatty/backend/core/agents/` |
| Context files + Memory UI (`assistant_context_files` with GENERATED `kind`/`is_protected`; soul unfenced in static, knowledge nonce-fenced in volatile; 7 keyless tools; always-confirm on protected files; `/api/context-files` + `/api/memory`; `MemoryPage`) — **landed #72 Phase 1+2** as `backend/context_files/` + `backend/memory/router.py` + `frontend/src/crm/MemoryPage.tsx`. Chatty's `_load-order.json`, GCS sync, `atomic_write`, meetings/transcripts and `relevance_prefetch` do not translate and were not ported; its flat namespace became `topics/`+`daily/` prefixes to fit one table; its regex `sanitize_memory_content` was dropped in favour of this repo's nonce fencing (forge-proof where a blocklist is not). Fencing `MEMORY.md` is deliberately STRICTER than chatty, which loads it raw, because ours becomes extractor-fed in Phase 4 | `chatty/backend/core/agents/context_manager.py` + `tools/context_tools.py` + `ai_service._knowledge_management_instructions()` |
| Conversation compaction (`backend/assistant/compaction.py` + `assembly._apply_compaction` + four `assistant_conversations` columns + `history.{get_compaction_state,set_compaction,mark_untrusted_seen,is_conversation_tainted}` + `delimiters.wrap_conversation_summary`) — **landed #72 Phase 3**. Four deliberate departures from the blueprint, each because CakeCRM differs: the summarizer goes through the `AIProvider` ABC on the light tier (chatty hardcodes a Haiku id and calls the SDK directly, which this repo forbids), so the gist is bounded by CHARACTERS — `stream_turn` exposes no `max_tokens` knob; the gist is nonce-fenced rather than regex-scrubbed, and the fence is minted ONCE at write time because our provider caches the conversation PREFIX; a row whose tool work is unfinished is never gisted (chatty needs no such guard — it never persists a call and its result separately); and the transcript truncates at ROW granularity rather than slicing the rendered character stream, which can sever a fence. Row granularity also deletes a whole class of chatty's care: one row carries an iteration's calls AND its results, so a boundary can never SPLIT a `tool_use` from its `tool_result`. NOT ported: chatty's `sanitize_memory_content` (dropped in Phase 1 for nonce fencing) and its `_fetch_anthropic_key` fallback (no provider-specific key path here). Fixed in passing: `claude-opus-4-8`, `get_ai_provider`'s DEFAULT Anthropic model, had no `MODEL_CONTEXT_WINDOWS` entry, so the composer's context meter was hidden on a default install | `chatty/backend/core/agents/compaction/service.py` + `context_assembly._apply_compaction` |
| Assistant brand + identity-panel role gate (`identity.NAME` fixed as "Baker": no `name` column read, no `name` write path, prompt interpolation from the constant, and `NAME_NOTE` between soul and `SALES_GUIDE` so free identity text cannot rename it either; one-shot `UPDATE assistant_identity SET name='Baker'` migration with the column kept for rollback safety; `IdentitySettings.tsx` renders the name read-only and gates the personality editor on `useAuth().isAdmin`, members read-only) — **landed #71 (bundling #106)** as `backend/assistant/{identity,router}.py` + `20260826010825_assistant_name_is_a_brand.sql` + `frontend/src/assistant/IdentitySettings.tsx` (+ co-located vitest). Personality stays user-editable; only the name became permanent | New capability (product decision on issue #71 — no blueprint) |
| Heartbeat + background AI turn — **landed #6** as `backend/heartbeat/` (60s APScheduler tick) + `backend/assistant/background.py` (non-SSE `run_background_turn`: auto-approved writes under a server-enforced tool allowlist + `WRITE_BUDGET_BACKGROUND`). The scheduler now runs **four** jobs, split by one rule the code states explicitly: **local SQL rides `reminder_tick`** (#5 dreaming, #18's score refresh), **network- or AI-bound work gets its OWN `add_job`** (`heartbeat_turn`, #17's `gmail_scan`, #22 Phase 3's `proactive`) so a hung request can never delay reminder delivery | `chatty/backend/core/agents/background_runner.py` + `main.py` scheduler wiring |
| Reminders (own table, recurrence math, agent tools + **net-new full CRUD REST/UI**) — **landed #6** as `backend/reminders/` + `frontend/src/crm/RemindersPage.tsx` | `chatty/backend/core/agents/reminders/` |
| Notifications (Web Push VAPID keys persisted in Postgres, `notify_user` tool, bell) + system alerts — **landed #6** as `backend/notifications/` + `backend/alerts/` + `frontend/src/crm/components/{NotificationsBell,NotificationSettings}.tsx` + `frontend/public/sw.js`. Telegram delivery goes out through `telegram.service.notify_linked_user` (the pure-sync channel #7 landed), via `_send_telegram`; WhatsApp not ported. Chatty's user-configurable `scheduled_actions` subsystem (leases/active-hours/triage/dashboards) deliberately deferred | `chatty/backend/core/agents/notifications/` + `alerts/` |
| Telegram — **landed #7** as `backend/telegram/*` + `frontend/src/crm/components/TelegramSettings.tsx`: single-assistant long-polling (one main-loop asyncio task offloads `getUpdates` via `to_thread` and drives `engine.chat` on the SAME loop as the SSE endpoint — provider async clients are loop-bound), Fernet-encrypted bot token on a `telegram_settings` singleton, one linked user via a single-use `link_code` (Telegram deep link), CRM write confirmations as inline-keyboard Approve/Deny buttons (mapped onto `engine.resolve_confirmation` + an empty-messages continuation, batched so it continues only once every write is resolved), and `telegram.service.notify_linked_user(text)->bool` as the pure-sync outbound channel #6 consumes. No webhooks, no group chat (deliberately cut). | `chatty/backend/integrations/telegram/` |
| Gmail (read + draft only: `gmail_connection` singleton, BYO OAuth at `/api/gmail`, tools `gmail_search`/`gmail_read_thread`/`gmail_create_draft`, guard test + SECURITY.md) — **landed #8** as `backend/gmail/` + `frontend/src/crm/components/GmailCard.tsx` | `chatty/backend/integrations/google/` |
| Gmail connection-race hardening (`connection_generation` optimistic lock + CAS on token persist; pending-draft binding through the shared confirm flow; ciphertext CAS on `mark_broken`; atomic clear-and-capture on disconnect/app-replace; capped recovery of attachment-stored text bodies) — **landed #43** across `backend/gmail/*` + `backend/assistant/{engine,history}.py` | Follow-up to #8 (no blueprint — back-port candidate to CAKE OS) |
| Gmail touch-scan heartbeat job (read-only inbox scan → sender→contact match → idempotent `email` touch logging feeding #16; `gmail_scan_state`/`gmail_scanned_messages`/`gmail_unmatched_correspondents` tables; own `gmail_scan` scheduler job; "create contact?" alerts) — **landed #17** as `backend/gmail_scan/` | New capability (no blueprint — back-port candidate to CAKE OS) |
| Owned Gmail HTTP transport (`build(http=…)` over `AuthorizedHttp` wrapping a budget-gating `httplib2.Http` subclass; `_HTTP_TIMEOUT_SECONDS` per-socket-op stall bound + a per-call request budget resolved before setup; `GmailTimeoutError` as a retryable non-reconnect class; `gmail_scan` budget kept strictly inside its join deadline; `httplib2`/`google-auth-httplib2` pinned) — **landed #64** as `backend/gmail/{client,tools}.py` + `backend/gmail_scan/service.py` + `backend/tests/test_gmail_transport.py`. **Corrects the issue's premise**: a silent socket was never unbounded — the SDK's `build_http()` already applied a 60s socket-inactivity timeout — the defect was that there was no *aggregate* bound over a sequential fan-out, plus the fact that the 60s was an implicit default any `socket.setdefaulttimeout()` caller could redefine. Also retires its stated blocker: static discovery makes `build()` network-free, so the whole stack is hermetically testable against the real SDK | Follow-up to #8/#43 (no blueprint — back-port candidate to CAKE OS) |
| Kanban drag-and-drop — **pointer-based drop-target resolution landed #147** as `frontend/src/shared/dnd/collision.ts` (+ `collision.test.ts`, `KanbanBoard.test.tsx`), swapping `KanbanBoard`'s `collisionDetection` off `closestCorners`. `closestCorners` ranks by distance to the DRAGGED rect, and a column's droppable is only as tall as its own cards — so a card grabbed at the bottom of a long column stays nearest its own neighbours for the whole horizontal drag and a short or empty lane refuses the drop until the user also moves UP. The fix is four transforms over the rect map dnd-kit hands the strategy (never the DOM — a `min-h` big enough to matter parks a blank slab under every short column): `pointerWithin` instead of the dragged rect; **widen every column to the board's vertical band**, unioned with the DRAGGED rect so a mid-drag re-measure cannot close the band above the pointer and start a snap-home oscillation; **close the flex `gap-2` between cards UP**, so the gap resolves to card *i+1* (targeting a card inserts BEFORE it, so giving the gap to card *i* lands one slot too high); and **clip CARDS to what the board shows**, on BOTH axes. `closestCorners` remains the fallback whenever `pointerWithin` returns empty, which is now essentially horizontal — the gutter between columns, a failed precondition, or a pointer-less sensor (a future KeyboardSensor). Widening is skipped when two columns share an x-range (a wrapped or non-flex board would otherwise get IDENTICAL rects and hand every drop to whichever registered first), with a 1px tolerance so a subpixel layout does not silently switch the fix off. **Both folds in piece 4 are LIVE here, which is the main divergence from the blueprint:** upstream removed its internally-scrolling columns and calls that clip defensive, while CakeCRM's pipeline board still ships `columnClassName="… overflow-y-auto max-h-[70vh]"`, so the column fold is the real vertical one; the board container is `overflow-x-auto`, so the scroller box is the horizontal fold that bites once there are more stages than fit the width. **The two axes are then treated differently on purpose.** A lane whose own cards have scrolled out of view VERTICALLY keeps its band and stays a drop target — only its CARDS are dropped — because the lane is still on screen beside the long one, and losing it would break exactly the moment you most want to move a card into it. A lane past the HORIZONTAL fold is dropped outright (`clampX`, and it is deleted from the rect map, since the widening only writes the lanes it was given): it is not beside anything, its x-range points at page space where nothing of the board is painted, and left hittable there a drag released in the strip just outside the board's right edge would commit a deal to a stage nobody could see. **This diverges from the blueprint, which clips only cards** — its keep-every-lane rationale covers the vertical case and does not reach this one, and cards were already clipped on both axes, so the x asymmetry for lanes was the inconsistency rather than the fix. For the same reason the `closestCorners` fallback is handed the clipped-card map rather than the raw one: ranking by distance always names *something*, so on the raw rects a pointer in the gutter between two lanes could be answered with a card below a fold, undoing the clip in precisely the cases pointer containment could not cover. A ONE-column board is widened like any other, because the band starts from the dragged rect and so is not that column's own rect. `KanbanBoard` marks its scroll region with a `data-kanban-scroller` **attribute**, deliberately not a CSS class: `collision.ts` finds the box by walking up from a droppable's own node, nothing else reads it, and the failure mode of a rename is silent (unclipped cards hit-testable off-board), so `KanbanBoard.test.tsx` pins the attribute and the `closest()` walk against a real DOM. The prop surface is unchanged. **The port is a composite of two upstream changes, which the issue's single citation hides.** PR #1813 (`f367e7cf8`) is pieces 1-3 plus a column-only, y-only card clip; everything about the BOARD's own box — `clampToBox` on both axes, `boardVisibleBox`, the `data-kanban-scroller` contract and the band being drawn from visible rects — arrived later, in PR #1956 (`e605b49c4`), whose other half bounds the board to the window. `SYNC_LEDGER.md` records both, so a later intake for `e605b49c4` is not read as un-ported and re-ported wholesale. NOT ported, all of it from #1956: `useBoardScroller`, the `items-start`/`overflow-y-auto` board chrome and `DRAG_DROP_GUIDE.md` (the guide's content lives in the module docstring here, per #147's scope), plus #1813's `ProjectBoardPage` flex-class repair (no such board in this repo). One consequence of declining the board-bounding is worth knowing: CakeCRM's scroller has no VERTICAL fold (`max-h` sits on the columns, whose droppable is itself the scrollport), so the board box bites only on x here | `cake_os/frontend/src/shared/dnd/` |
| **Shared collection layer** (the CRM UI's interaction substrate) — **landed #73** as `frontend/src/shared/{search,listview,collection,overlay,hooks}/` with their co-located tests, plus the vitest harness. `search` (SearchInput/SearchFilterBar/SortControl + `match`/`persist`/`sort`), `listview` (ListView/ViewSwitcher + `headerSort`/`sortRows`), `collection` (CollectionView, `facets`, `useCollectionState`, `usePageAssembly`, `visibleOrder`, `views/{Cards,CollectionList,Kanban}`, `detail/CollectionDetail`, `closePolicy`), and `overlay/DetailModal` (pulled in because `CollectionDetail` wraps it). **#73 landed the layer ONLY — no CRM surface was rewired**; Pipeline adopted it in #74, Contacts/Companies/Tasks in #77, deal detail in #75. #74 added TWO knobs to the layer, both optional and both defaulting to pre-#74 behavior. (1) `KanbanViewConfig.dragPolicy?: 'index' \| 'column'`: the layer's `dragLocked = isFiltering \|\| !manualOrder \|\| hasTruncatedColumn` exists because a drop INDEX is unmappable against a partial list, so a board that assigns only a COLUMN and discards `newIndex` (no rank column exists to persist a position into) opts out of the gate entirely and keeps only its own `kanban.dragDisabled` extras. It is consumed in exactly one line of `useCollectionState`, and `CollectionView`'s "· drag paused" note now keys on `dragLocked` too so it cannot lie under `'column'`. The policy is a claim about the app's `onMove`, not a styling choice, and the type cannot yet enforce it — making `newIndex` structurally unavailable under `'column'` is tracked as its own issue. (2) `CollectionViewProps.searchResetNonce?: number`: routes a PAGE-initiated clear to the reset mechanism `SearchInput`/`SearchFilterBar` already own, because `SearchInput` adopts an external value only when it CHANGES — so clearing while `state.query` is already `''` leaves unsettled typed text that re-filters a moment later. It is summed with the bar's internal counter and compared with `!==`, so it **must only ever increase**. The alternative a caller reaches for otherwise is re-keying the whole subtree, which also destroys the facet disclosure panel, `ListView`'s show-all and the board's scroll position. Adaptations from the blueprint: `lucide-react` swapped for the in-repo `shared/icons.tsx` (no new dependency; `IconChevronLeft` added); the blueprint's `corrections` dependency reduced to a local `collection/voidedRowClass.ts` (the `voided` tri-state itself is generic and inert unless a config supplies `getVoided`); `shared/pagination` is NOT reachable from the layer and was not ported; the app-local `detailClosePolicy.ts` became `collection/closePolicy.ts` since CakeCRM has one CRM app; and the ported code was modernized for CakeCRM's stricter `eslint-plugin-react-hooks` v7 ruleset (`configs.recommended`, which the blueprint does not enable) — ref-writes-during-render and setState-in-effect were removed rather than suppressed. Styling: the layer keeps the blueprint's Tailwind utility classes, wired to CakeCRM's theme by **semantic aliases** in `index.css`'s `@theme static` (`cream`→`ck-card`, `sand`→`ck-bg`, `charcoal`→`ck-ink`, `muted`→`ck-ink-mute`, `line`→`ck-line-strong`, `brand`→`ck-accent`, `font-heading`→`font-display`) — declared as `var(...)` so `.dark` re-resolves them and the layer inherits dark mode with no `dark:` variants. Accent-as-TEXT deliberately routes through `text-ck-accent-text` per #54's WCAG rule, never `text-brand`. The `dock:` custom variant is defined in `index.css` for `DetailModal`'s takeover-vs-centred switch. **Row interactivity is keyboard-first since #148** (port of the same upstream fix), and the shape of it is `onRowClick`, now **optional**: present ⇒ the `<tr>` is a tab stop that activates on Enter/Space, absent ⇒ the row is wholly inert (no cursor, no hover, no tab stop, no handler), because a focus stop that does nothing is worse than none. `CollectionListView` therefore forwards `onRowClick` only when the page wires `onSelect` — the old `row => onSelect?.(row.id)` closure is always truthy and would have minted a tab stop per row on a surface with nothing to open. The key handler is guarded on `e.target === e.currentTarget` so a keystroke aimed at a control INSIDE a cell (the selection checkbox, a column's inline link) never also opens the row; the CLICK path is deliberately NOT guarded that way, since a mouse click always targets a cell and the same test would swallow every row click — click suppression stays the cell's own `stopPropagation`. The row keeps `role="row"`: `role="button"` would flatten interactive cell content out of the accessibility tree and orphan every cell's implicit `role="cell"`, so the residual WCAG 4.1.2 gap is **mitigated, not closed**, by ONE `aria-describedby` hint on the table (not a `<caption>`, which per HTML-AAM would *name* every list with the same generic instruction), rendered only when rows are interactive AND non-empty. A reader is still told "row", not "opens this record", and a table description may go unheard by a Tab-only user landing straight on a row. **The consumer-side half of the same fix is ADDITIVE and did ship: where a row's action is a ROUTE rather than an overlay, the title cell also carries a real `<Link>` with `stopPropagation`** — Contacts and Companies take it, Tasks (an overlay) does not. The anchor is not decoration: it announces as a link, and it carries Ctrl/Cmd-click, middle-click and right-click into a new tab, which a click handler cannot hand-roll because a middle click fires no `click` event at all. What stays deferred is only the expensive half — moving the tab stop OFF the row and onto that anchor, which is what would fully close 4.1.2 and would rewrite every adopting surface's columns. A third ceiling comes with row-level focus and is named in the code: every rendered row is a tab stop, so a list goes from a handful to up to `renderCap` (300) — and to twice that on a route-shaped surface, where the row and its title anchor are both stops — with the "show all" control behind them; the deferred half above is what retires it, since moving the stop onto the anchor halves the count and a roving tabindex then collapses the table to one. Both key and click paths bail on a modifier (`meta`/`ctrl`/`alt`/`shift`) before doing anything, since a row can honour neither open-in-new-tab nor Shift+Space page-up — a divergence from the blueprint, which guards neither. `CardsView` gained the same interactivity-follows-`onSelect` gate as the list view, so the two views tell one story. Focus is an **`outline`, not a `ring`**: Tailwind's `ring-*` compiles to `box-shadow`, which WebKit does not paint on `display: table-row`, so the house `focus-visible:ring-…` idiom renders nothing on a `<tr>` in Safari and every iOS browser; `outline-brand` at full strength is 4.65:1 light / 3.15:1 dark against the card, over WCAG 1.4.11's 3:1, where `ring-brand/40` composites to 1.93:1. This is the one accent-as-non-fill use that does NOT route through `--color-ck-accent-text` — 1.4.11 asks 3:1 of a graphical indicator, not 1.4.3's 4.5:1 of text. Consequently `voidedRowClass.ts` gained `voidedTableRowClass` (`[&>td]:opacity-60`): `opacity` composites an element's ENTIRE painting including its focus outline, so the row-level form would dim the indicator below 3:1 on exactly the rows the non-destructive standard insists stay openable. It is a SECOND constant rather than a change to `VOIDED_ROW_CLASS`, which `CardsView` applies to a div where a `[&>td]` child selector would match nothing and drop the dimming altogether. | `cake_os/frontend/src/shared/{search,listview,collection,overlay}/` |
| Theme + dark mode (fixed `--color-ck-*` palette, `.dark` semantic-token override, self-hosted Montserrat/Open Sans, `useTheme` + `ThemeToggle`, accent-picker removal) — **landed #54** as `frontend/src/index.css` + `core/theme/useTheme.ts` + `crm/components/ThemeToggle.tsx` | `cake_os/frontend/src/index.css` + `core/theme/useTheme.ts` (read from `origin/master`) |
| Hue FILL/TEXT split for WCAG AA (11 `--color-ck-*-text` tokens + `--color-ck-on-status`; `SAGE_FILL`/`SAGE_TEXT` &c. replacing the ambiguous bare names; `STAGE_COLORS` → `{text, fill, bg}`; `core/theme/hueContrast.test.ts`) — **landed #119** across `frontend/src/index.css` + `shared/styles.ts` + `crm/{constants.ts,components/badges.tsx}` + ~25 call sites. **Corrects two premises in the issue**: the migration is ~40 text sites, not ~10 (the issue lists only the badge/stage sites it measured — `color: CORAL`/`GOLD`/`SAGE` also paint form errors, toasts, the Gmail connected flag and the dashboard idle-days label, plus the Tailwind `text-ck-*` classes in `login/` and `setup/`); and **dark was not clean** — the issue's sweep covered same-hue pairings only, so it missed `red` at 3.97:1 on a foreign stage row and `stage-lost` at 4.17:1 on its own wash. Three adjacent defects of the same class were found by measurement and fixed here rather than filed: `MemoryPage` mixed its washes from `ACCENT_TEXT` (a foreground token, so retuning text moved a background), `TriageCard`'s `hover:text-brand-dark` is 2.18:1 on the dark card, and the three solid status buttons put white on a dark-mode fill at 2.49:1. NOT changed: fills, in either theme — the brand red and every stage/status hue are byte-identical as backgrounds, borders, dots and bars | New capability (no blueprint — follows #54's own `accent-text` precedent; back-port candidate to CAKE OS) |
| Companies (first-class entity: `companies` table, `company_id` FKs, rollup detail page, text→FK backfill migration) — **landed #13** | `cake_os/backend/apps/crm/company_service.py` |
| Company link coherence (shared batched `resolve_or_create_company_ids()` resolve-or-auto-create on every ingestion path; contact list/search LEFT JOIN + `company_name`; second one-shot backfill) — **landed #35** | New capability (gate decision on issue #35; shared with the #61 importer) |
| Inline quick-create for a deal's Contact and Company (`frontend/src/crm/components/RecordCombobox.tsx` — a generic server-searching combobox with a `Create "<name>"…` row, keyboard nav and `role="combobox"`/`listbox` a11y — wired into `DealForm`, plus `POST /api/crm/companies/resolve` exposing the #35 primitive over REST) — **landed #123**. Retires the capped-200 `<select>` pattern in the deal form and the two hand-written out-of-page append guards with it. **Corrects three of the issue's own pointers** (the search param is `q` not `search`; `DealCreate` takes no free-text company; `POST /companies` does NOT use the resolver and 400s on a case/whitespace duplicate) — see the CRM bullet for the ownership split and why the resolve endpoint had to exist. Built reusably for **#126**, which landed on it unchanged | New capability (no blueprint — cake_os's entity forms use plain capped `<select>`s too; back-port candidate to CAKE OS) |
| Contact form company field — the freetext↔link merge (`ContactForm`'s free-text `Company` input + its capped `Linked Company` `<select>` become ONE `RecordCombobox`, consumed unchanged; `ContactForm.test.tsx` added) — **landed #126**. The blueprint's `QuickAddModal` defers its company create to submit; CakeCRM creates on row press because that is the shipped component's contract and the issue mandates one component for both surfaces — bounded by `/resolve` being get-or-create. Two contract consequences are accepted and pinned by tests: an unlinked contact needs the form's own inline **Remove** action (the widget's × is gated on a non-null value), and a name typed without choosing `Create "…"` is discarded on Save, as it already is in `DealForm`. The load-bearing rule is that an UNTOUCHED company field omits both `company` and `company_id` from the `PUT`, so `exclude_unset` leaves a pre-#35 contact's unmatched free text intact | `cake_os/frontend/src/apps/crm/components/QuickAddModal.tsx` + `SearchableSelect.tsx` (#2016/#2045, #2049/#2069) |
| Chatter/notes (`crm_chatter`) — **landed #15** as `backend/crm/chatter_service.py` + `frontend/src/crm/components/NotesThread.tsx` | `cake_os/backend/apps/crm/chatter_service.py` |
| Chatter note attachments + readable composer (`crm_chatter_attachments` bytea + FK CASCADE; `crm/attachment_service.py`; `core/thumbnails.py`; 4 auth-guarded routes; `apiBlob` + `useAuthedBlobUrl`; `NoteComposer`/`NoteAttachments`/`AttachmentLightbox`) — **landed #57**. **Corrects two premises in the issue.** (1) "Reuse the existing assistant uploads storage (`backend/assistant/uploads.py`)" cannot be complied with literally — that module is a TEXT EXTRACTOR that discards the bytes ("there is no attachments table and no file cache", its own docstring), so there was no first store to reuse and #57 creates CakeCRM's first one; the instruction's intent (exactly ONE place uploaded bytes live) is honored, and what IS reused from it is the constant/`UploadError` idiom, the lazy-import discipline for heavy libs, and the repo-wide `read(cap + 1)` bounded read. (2) The three cited upstream issues are **three lineages, not one**: cake_os **#1526** (`53b0f3627`) is the attachments + composer work, and it landed as a NEW platform app `backend/apps/chatter/`, not in `apps/crm/chatter_service.py`; **#1215** and **#1331** are the CRM image **gallery** (auth-guarded fetch, server-side thumbnails), so **cake_os chatter has no server-side thumbnails at all** — its `width_px`/`height_px` are client-supplied and `docs/MEDIA_STORAGE.md` lists thumbnails as deferred. Since Will's gate made thumbnails non-negotiable, the pipeline is ported from the gallery lineage instead, adapted base64→bytes and with the unused `crop_square` mode dropped (CSS `object-fit` crops). Auth-guarded serving is an **adaptation, not a port**: cake_os mints GCS V4 signed URLs, CakeCRM has no object store, so it serves from an authenticated endpoint and the client builds object URLs — which is the pre-#1215 pattern cake_os replaced, and the only one Bearer-token-only auth permits. NOT ported: GCS/object storage, the `Surface`/`SURFACES` four-app registry and the `chatter_messages` rail, `core/chatter.can_view` (CakeCRM has one surface and no per-object ACLs), `core/upload_admission.py`, `core/audit.py` void-with-reason (this repo hard-deletes and has no audit chain), uploader-only write gates (they would contradict #60's any-member model), client-side downscale, the batch `?note_ids=` endpoint + `useNoteAttachments` (metadata embeds into `get_chatter` instead), and width/height/duration columns | `cake_os/backend/apps/chatter/{service,router}.py` + `frontend/src/shared/chatter/*` (flow); `cake_os/backend/core/thumbnails.py` + `apps/crm/image_service.py` (thumbnails) |
| Custom fields (EAV `crm_field_definitions`/`crm_field_values`, Settings editor, entity-form + detail-page value inputs, 6 `crm_*_fields` tools) — **landed #19** as `backend/crm/field_service.py` + `frontend/src/crm/components/{CustomFieldSettings,CustomFieldsSection,CustomFieldInputs}.tsx` | `cake_os/backend/apps/crm/field_service.py` |
| Touch counts + field provenance (`deals.ai_touch_*` cols + in-process recompute worker; `crm_field_provenance` + `AiBadge`/`ProvenanceBadge`/`TouchCountPill`) — **landed #16** as `backend/crm/touch_count_service.py` + `provenance_service.py`. **Per-event verdict detail view landed #56**: the `deal_ai_touch_evidence` JSONB snapshot (FK-less, one row per deal, written in the count's own transaction and rowcount-gated), `touch_count_service.get_touch_evidence` + `GET /api/crm/deals/:id/touch-count/evidence`, and `frontend/src/crm/{touchEvidence.ts,components/AiTouchDetail.tsx}` — at which point `ai_touch_count` became the **derived sum of per-line verdicts** so the pill and its explanation cannot disagree (see the CRM bullet for the window shrink and the `verdict_state` reconciliation) | `cake_os/backend/apps/crm/touch_count_service.py`, `provenance_service.py` (the detail view + its evidence table are ported from the blueprint CRM's touch-count evidence feature) |
| Lead scoring (pure-algorithmic `lead_score` 0-100 on deals+contacts; event-triggered inline recompute serialized by a per-entity advisory lock + a bounded daily heartbeat refresh + backfill endpoint/tools `crm_get_lead_score`/`crm_recompute_lead_scores`; sortable contact list + `ScorePill`) — **landed #18** as `backend/crm/scoring_service.py`. Since the #22 merge the write-event chokepoint for deal-column writes is `service._write_deal_update` (one hook covers the #22 lifecycle verbs too), with `archive_deal`/`merge_deals` hooked separately; archived deals are excluded from the contact deal-linkage aggregate | `cake_os/backend/apps/crm/scoring_service.py` |
| Scoring, analytics — analytics **landed #20** as `service.get_analytics()`/`summarize_analytics()` + `GET /api/crm/analytics` + `crm_analytics` tool + enriched `CrmDashboardPage` (win/loss, activity volume, read-time deal aging from existing timestamps — no migration; stage-duration metrics dropped, no stage-change audit trail; scoring landed separately in #18 above) | `cake_os/backend/apps/crm/*_service.py` |
| Dashboard parity (stat row + Weekly Touches) — **landed #76** as `service.get_weekly_touches()` + `GET /api/crm/dashboard/weekly-touches` + `frontend/src/crm/components/WeeklyTouchesCard.tsx`, plus `total_companies` on `get_dashboard_stats()` and a four-tile stat row on `CrmDashboardPage`. Ported for CONTENT parity, **additively** — the blueprint component is written against Tailwind classes (`bg-cream`/`text-charcoal`/`font-heading`) that #54 removed, and a literal replacement would have deleted #20's analytics sections. The blueprint's PER-REP grouping collapsed to per-DEAL here (no owner columns, single-user then), with the envelope keeping `window`/`total_touches`/`total_open_deals` and `deals` standing where it had `reps` — **and #146 is the re-grouping that anticipated**, so `reps` is what ships today (see that row). **Two separate signals, deliberately:** window MEMBERSHIP is `LAST_TOUCH_SQL` — the same keyless GREATEST(edit, newest activity, newest live note) expression `analytics_service.get_stale_deals` uses, so the card and the "Needs a touch" panel on the same page can never disagree about what a touch is — while the per-deal NUMBER is #16's `ai_touch_count`, which is what supplies the zero-keys gate (no provider ⇒ every count NULL ⇒ `computed_deals == 0` ⇒ the card renders `null`; it owns its own wrapper padding, so hiding leaves no gap). Membership is emphatically NOT `deals.ai_touch_count_at`: that column is #16's stale-write-guard watermark (it only advances when a provider answered and the CAS accepted, and falls back to the deal's `created_at`), so keying a window off it made every provider timeout silently drop a deal from an accountability number — and left numerator and denominator with different coverage on a half-backfilled install. Because membership is keyless, both sides of the ratio are coverage-independent. The touch-count colour ramp moved to `crm/constants.ts` and is shared with `TouchCountPill` (one number, one colour, app-wide). Window math mirrors the blueprint but on UTC calendar days — no CT convention here, so the inclusive end-day bound is a plain +1 day, guarded against the `datetime.max` OverflowError that is not a `ValueError`; the filter is labelled UTC rather than converting per viewer. It **stays** UTC after #130 moved this module's "today" decisions onto the configured timezone (that bullet has the reasoning): a labelled absolute window the caller names explicitly is a different question from "is this task overdue right now". Drill-down landed with #56: each row opens the deal sheet via `onOpenDeal`, whose evidence section explains that deal's number event by event. | `cake_os/frontend/src/apps/crm/components/DashboardTab.tsx` + `WeeklyTouchesCard.tsx` + `backend/apps/crm/dashboard_service.py` |
| Weekly Touches per rep + the per-rep drill-down page — **landed #146** as `service.get_weekly_touches` (one `GROUP BY d.owner_id` aggregate + one `ROW_NUMBER() OVER (PARTITION BY d.owner_id)` rows query, both shared with `get_weekly_touch_detail`), the pure `_shape_touch_reps`, the shared-snapshot reader `_touch_snapshot_reads`, `GET /api/crm/dashboard/weekly-touches/detail?owner=<id|unassigned>`, and `frontend/src/crm/{weeklyTouches.ts,WeeklyTouchesDetailPage.tsx,components/TouchDealRow.tsx}` at `/crm/touches/:owner`. Membership, the per-deal number and the zero-keys gate are all unchanged from #76. **The rep universe is every owner of an open deal INCLUDING the NULL bucket** — named "Unassigned" (the OWNERSHIP word; `_shape_per_rep`'s "Unattributed" is about authorship) and sorted last — so a rep who touched nothing still gets a row, which is the point of a weekly accountability pull, and the totals are the bucket sums **by construction** rather than a separately-computed number that could drift. The cap is PER REP (a global `LIMIT` would leave rep rows showing a count with no rows beneath them), and the window filter sits inside the ranked subquery so the cap ranks only deals that count. The **single-rep case renders flat** — the pre-#146 card — but still shows its owner, and its Details link on the same `touches > 0` gate every other rep row uses: dropping the header entirely would break #128's unassigned-renders rule and, with no `NAV_ITEMS` entry, leave the new page reachable only by typed URL on a single-seat install. The drill-down **RE-RESOLVES the window** (it takes the same `start`/`end` calendar days the card takes, and nothing at all on the rolling default) rather than inheriting the card's exact instants, and that is a correction the final review forced after an earlier revision did forward them. Freezing the bounds looks like it guarantees the page lists what the clicked number counted; it cannot, because membership under `LAST_TOUCH_SQL` is "this deal's CURRENT most recent touch falls in the window" — a statement about now, not a historical fact. A touch made after the card rendered therefore moves that deal PAST a frozen upper bound and deletes it from the page, **including a touch the user makes from the page itself**: log a call and the deal you just worked vanishes from the list of deals you touched. Reproduced on Postgres and now pinned by two integration tests. Re-resolving asks the card's question at open time, so the page is always internally consistent and always current; the cost is that a dashboard left open for an hour links to a page an hour fresher than its own number, which is the honest form of the same disagreement. `owner` is a REQUIRED param spelling NULL as a literal, deliberately unlike `/dashboard/today`'s absent-means-everyone `owner_id`, which cannot address the unowned bucket; it is range-checked to a 32-bit id, since a larger one reaches Postgres as an out-of-range comparison and 500s. **Both surfaces read on ONE snapshot**, via `_touch_snapshot_reads` — `get_connection` + `SET TRANSACTION ISOLATION LEVEL REPEATABLE READ` + `row_to_dict`, the sanctioned own-your-cursor path. Each puts a count and the rows behind it on one screen, so two snapshots are a way to render a contradiction: "6 of 5 open deals touched", a rep row with more deals under it than its own number admits, or every row dropped because the bucket vanished between the reads. That makes `_shape_touch_reps`' orphan branch unreachable in production; it stays because the shaper is a pure function over whatever rows it is handed. The drill-down is also BOUNDED (`WEEKLY_TOUCHES_DETAIL_MAX`, probed one past) and reports `truncated`, because a page promising the full list must not quietly serve a prefix. NOT ported: the blueprint's owner-authored-chatter touch definition and its most-recent-note column (CakeCRM's per-event explanation is #56's evidence view inside the deal sheet), its Central-time window logic, its `?label=` free-text forward (the server owns the label), and its EXCLUSION of unowned deals. A "mine" scope was considered and deferred: Weekly Touches is a comparison view, and if it is ever wanted it is a client-side filter of `reps` by the current user, never a backend param whose absent/NULL semantics would collide with the Unassigned bucket. | `cake_os/backend/apps/crm/dashboard_service.py` (`get_weekly_touches`/`get_touch_detail`/`_resolve_detail_window`) + `frontend/src/apps/crm/components/{WeeklyTouchesCard,WeeklyTouchesDetailPage}.tsx` @ `8e4d202f9` (cake_os #602) |
| Dashboard Today panel (one ranked Top-5 of what needs me today: `backend/crm/today_service.py` with the pure `build_today_items` ladder, `core.localtime.local_day_bounds`, `reminders.service.list_pending_between`, `GET /api/crm/dashboard/today`, `frontend/src/crm/{todayPanel.ts,components/TodayPanel.tsx}`; plus the three-site UTC→configured-day sweep) — **landed #130**. See the CRM bullet for the ladder, the one-clock rule, the reserved rank 2 and the reminders-are-scope-invariant decision. **Corrects three premises in the issue**, each verified against the tree rather than the issue text: (1) it says to widen scope via "the existing `OwnerScopeToggle` (#60)" — #77 **deleted** that component, and its replacement is a multi-select list-page facet, so the panel carries its own two-button control instead; (2) it specifies "task → task" click-through — there is **no task-detail URL** anywhere (`/crm/tasks` is mode-routed and both modes keep detail in component state), so task rows go to `/crm/tasks` and the row checkbox covers acting on the specific task; (3) it treats the owner filter as reaching every row — `reminders` has **no owner column at all** and is install-wide by design, so reminders are scope-invariant and count coherence is structural (one array, sliced) rather than a shared WHERE builder. The issue's "Owner filter rides the shared WHERE builders" is honored in spirit — one filter, applied once — but there is no builder to ride: the shared builders are `_contact_search_where`/`_company_search_where`, and neither tasks nor reminders has one | New capability (no blueprint — back-port candidate to CAKE OS) |
| Assistant tool set + sales behaviors — **Phase 1 landed #22**: 9 new tools (`crm_search_deals`, `crm_mark_deal_won`/`_lost`, `crm_archive_deal`, `crm_merge_deals`, `crm_get_stale_deals`, `crm_get_contact_staleness`, `crm_find_duplicates`, `crm_scan_gaps`) in `backend/crm/analytics_service.py` + `service.py`, parity closes (embedded `custom_fields`, tool-side `limit_per_stage`, `limit` on find/search, company chatter), the genericized static `identity.SALES_GUIDE` prompt block + sales `QuickActions`. **Phases 2 + 3 landed together** once #17/#18/#20 all merged (the three-PR split was dependency ordering, and every dependency cleared at once): **Phase 2** = `crm_get_deal_health` + `crm_get_pipeline_analytics` in `analytics_service.py` (see the CRM bullet above); **Phase 3** = `backend/proactive/` — a daily pipeline digest and stale-deal / untouched-contact nudges on their own `proactive` scheduler job. Both are **keyless-first**: the digest is deterministic SQL and the nudges read Phase 1's pure-SQL detectors, with an optional single `run_background_turn` (read tools + `notify_user`, digest numbers in the USER message) adding at most one extra notification when a provider exists. Every send **claims before it delivers** — the digest via a one-statement rowcount UPDATE on `heartbeat_state` (so two ticks can't both push), each nudge via a conditional upsert on `proactive_nudges` — because a crash that loses one notification beats one that re-sends every tick. `proactive_nudges` is polymorphic and FK-less, so it MUST stay in the `_truncate_all` sweep. NOT ported: `get_rep_performance` (no owner columns), `enrich_field` (no web tools), lead-import tools (own issue) | `cake_os/backend/apps/crm/tools/` + the blueprint sales agent's config |
| Todo-GTD task mode (one store: widened `tasks` + `task_projects`; `crm/gtd_{common,service,router,tools}.py`, `crm/todo_{capture,web,pwa,tokens}.py`, `core/{ratelimit,localtime}.py`; `frontend/src/crm/gtd/*` + `components/TaskModeCard.tsx`) — **landed #70**. **Source note, because the issue says otherwise:** the `<!-- auto-answer -->` directed "port from chatty, not cake_os" on the premise that cake_os was behind. It is not — cake_os's `todo_gtd/common.py` header states it IS chatty's todo ported to Postgres, extended with `weekdays`/`every:N` repeats, a Today view, quick-add and `auto_star_on_due`. Chatty's is SQLite behind a process-wide write lock. So each half came from whichever tree is genuinely ahead, and the answer's file-level instructions were followed exactly where it gave them: **public capture + web app + rate limiter + PWA manifest from chatty** (`capture.py`/`web.py`/`ratelimit.py`/`pwa.py`, named explicitly in the answer), **GTD core from cake_os** (already Postgres, already on `pg_fetchall`/`row_to_dict`, and the only tree with the three features the issue's own scope list demands). NOT ported: cake_os's owner-scoped GTD *views* — since #60 a task carries `owner_id` and every task write path threads it (including the repeat-spawn, so a recurring task keeps its assignee), but the GTD lists are deliberately unscoped: GTD is one person's working surface, and the no-login capture/web surfaces have no user identity to scope by. The Projects/CRM card-link connector (`tasks` already carries contact_id/deal_id — `RecordChip` is the native replacement), `todo_get_capture_link`/`todo_get_web_link` (links are secrets; they live in Settings, not in a chat transcript), cake_os's `ConcurrencyGate` (chatty's limiter is what the answer named), `always_confirm` (no engine support — all six mutating tools carry `writes:true` instead), and the copy buttons (not in the issue's scope). `ProjectsPage` renders a plain card grid rather than `shared/collection`. #77 adopted the layer on Contacts/Companies/Tasks but deliberately NOT here: the issue scopes exactly those three, this is a GTD surface over GTD's own API, and it was being changed concurrently — so its adoption is a follow-up, not part of #77. | `chatty/backend/core/todo/{capture,web,pwa,ratelimit}.py` + `chatty/frontend/src/todo/publicMode.ts`; `cake_os/backend/apps/todo_gtd/*` + `cake_os/frontend/src/apps/todo-gtd/*` |
| Todo GTD triage & edit-sheet parity (due-date cue overlay + `pendingDue`, inline Notes textarea with a single-flight blur commit, `<select>` Context picker, `text-sm`/`text-charcoal` step headings) — **landed #150** as `frontend/src/crm/gtd/components/{TriageCard,TodoEditSheet}.tsx` + their two co-located vitest files. NOT ported: the blueprint's `shared/autosave` `useAutoSave` primitive — CakeCRM has no such primitive and one textarea does not pay for one, so the local `flushNotes` (single-flight, one trailing run, queued on the chain's tail) stands in for it. Its `updated_at`-ordered `adopt()` IS ported, and deliberately: an earlier cut of this port adopted only from the prop and compared content, and review found the save-rewind, the pinned override and the stale-star defects that ordering exists to prevent. The upstream Settings-modal scroll-lock fix (cake_os #2554) does not apply: CakeCRM shows the capture/web links on the Settings page's Task mode card, not in a modal | `cake_os/frontend/src/apps/todo-gtd/components/{TriageCard,TodoEditSheet}.tsx` (PR #2424 `e3c06bba`, PR #2539 `b936ac29`) |
| Frontend boot split (`Root.tsx` dispatch, lazy route table + one shared `crm/gtd/pages.ts`, lazy assistant drawer, `core/components/{BootFallback,ChunkErrorBoundary}.tsx`, and two guards — `bootSplit.test.ts` over the source graph and `bootSplitBuild.test.ts` over the emitted chunk graph — plus `ChunkErrorBoundary.test.tsx` and `PublicTodoApp.test.tsx`) — **landed #149**. Entry chunk 1,001 → 187 kB raw; `/todo/{token}` cold load 1,044 → 346 kB raw (300 → 104 kB gzip). **Corrects the issue's premise for `/capture`**: it is backend-rendered HTML here, not a React route, so there is no capture chunk and nothing to do for that surface. Adaptations, each because CakeCRM differs: the guard reads source via `import.meta.glob ?raw` (upstream's `node:fs` fails `tsc -b` under `types: ["vite/client"]`) and adds a transitive-closure sweep plus a dynamic-import and `import.meta.glob` sweep that upstream has no analogue for; a SECOND guard asserts the built chunk graph, since Rolldown's automatic topology is doing all the work and no source assertion can see it change; the ten GTD pages share ONE chunk via `pages.ts` (upstream ships ten, which would hand the phone ten requests); a third Suspense inside `CrmLayout` (nested-route layout + the non-transition task-mode flip); `LoginPage` stays eager (first screen of every signed-out visit, three small modules); the assistant drawer goes lazy on `aiReady` (upstream has no always-mounted heavy drawer); `document.title` assertions dropped (never set here); one boundary in `Root` rather than two; and the boundary declines to auto-reload while `navigator.onLine` is false. Upstream's nested-`<Router>` crash (its #2057) was never live here — the old early return ran before `<BrowserRouter>` — so `Root` makes a non-bug structural rather than fixing one. Fixed in passing: `.dark .hljs-emphasis`/`.hljs-strong` set only a font face while github.css sets an explicit near-black `color` on both, so markdown emphasis inside a dark-mode code block rendered near-black (pre-existing; the split moving github.css into the lazy chunk is what made the cascade worth re-reading) | `cake_os` PR #2150 (`1c10ccfec`): `frontend/src/{Root,main,App}.tsx`, `core/components/{BootFallback,ChunkErrorBoundary}.tsx` (+tests), `bootSplit.test.ts`, `apps/todo-gtd/PublicTodoApp.test.tsx` |
| Bulk deal operations (per-stage Select All + card multi-select, inline bulk bar, atomic set-based backend, `crm_bulk_move_deals` tool) — **landed #55** as `service.bulk_move_deals` + `_classify_deal_update` + `POST /api/crm/deals/bulk-move` + `PipelinePage` selection UI + pure `crm/bulk{Selection,Outcome}.ts` (+ `ApiError` in `core/api/client.ts`). NOT ported, each because the column does not exist here: the `status` dual-write and its multiple-assignment fix (won/lost ARE stages in CakeCRM), the ~60-line `display_order` request-order replay (deals carry no rank column — columns sort by `lead_score`), and the `owner_email` branch (single-user; #60 owns ownership). Also cut: the chatter translation layer (`log_events_bulk`, `lost_reason_note`/`lost_reason_cleared` kinds) because CakeCRM's stage audit IS `deal_stage_events` and a single-deal move writes no chatter either — so bulk writing none is parity, not a gap; client-side chunking (`BULK_CHUNK_SIZE` + the multi-chunk fold) since one request under a 200-cap covers an unpaginated single-user board, though the rejected-vs-unconfirmed distinction it protects survives in the collapsed `bulkOutcome.ts`; `reconcileBulkResult` (the blueprint's own PipelineTab never uses it — it serves the list surfaces, which patch rows in place, where the board always reconciles by refetching); the `BulkUpdateModal` (an inline bar is enough for one action); and bulk mark-won/mark-lost (the issue scopes bulk to stage-move; `crm_mark_deal_lost` stays the reason-capturing close). Two deliberate divergences FROM the blueprint: its bulk fetch takes no row locks, ours takes `ORDER BY id FOR UPDATE`; and its rejected path cannot revert, ours reverts to each deal's server-confirmed stage. | `cake_os/backend/apps/crm/deal_service.bulk_update_deals` + `frontend/src/apps/crm/{bulkSelection,bulkUpdateOutcome}.ts` + the PipelineTab selection/BulkBar |
| Per-deal deep links (`crm/links.py` — one server-side link shape; `url` on every deal-returning agent tool via `with_deal_url`; `CRM_DEAL_URL_GUIDANCE` applied by one import-time pass over `CRM_DEAL_URL_TOOLS`; a `SALES_GUIDE` line; `?deal=` resolution + dead-link notice in `PipelinePage`) — **landed #145** as `backend/crm/links.py` + `frontend/src/crm/dealDeepLink.ts` + `backend/tests/test_crm_deal_links.py`. Adapted, not copied: upstream's CRM is one tabbed page so its shape is `/crm?tab=pipeline&deal={id}`, while CakeCRM's pipeline is a real route (`/crm/pipeline?deal={id}`). NOT ported: `contact_path`/`company_path` (upstream's global-search page builds those server-side; nothing here does, so they would be untested constants with no producer), upstream's `deepLinkBanner.ts` three-condition gate (`loadedOnce`/`reloading`/`boardStale` encode a tab that stays mounted under a hidden CSS class for a whole session — this page is a route that remounts, and `loading`/`!data` already early-return the spinner and `LoadError`), and its always-absolute `deal_url` (this one falls back to a relative path when no public address was configured, rather than asserting a dev-default hostname into a message that leaves the app). the fire-once-then-strip URL rewrite (tried, then removed: the rewrite is itself a navigation, so it re-armed the link it had just resolved and fired a second refresh, and it raced the `?stage=` consumer for the same params object — keying resolution on `location.key` answers the same "clicked twice" problem without touching the URL, and leaves a link a reload can resume) | `cake_os/backend/apps/crm/links.py` + `tools/crm_tools.py` + `tests/test_crm_deal_links.py` (cake_os #1453) |
| Pipeline facet filtering (client-side: `frontend/src/crm/pipelineFilters.ts` pure predicate + `components/PipelineFilterBar.tsx`, spliced into `PipelinePage`'s useMemo seam as `deals`→`filteredDeals`→`grouped`; facets = keyword/stage/value/close-date/last-activity; sessionStorage `crm_pipeline_filters`) — **landed #21**. Every facet is client-side except #83's `archived`, which also carries `?include_archived=true` (see the CRM bullet). Owner facet dropped (single-tenant); `get_pipeline()` gains a derived `last_activity_at` = MAX(deal `activity_log` rows + un-archived deal `crm_chatter` notes) via one UNION-ALL/GROUP BY join (NULL = no activity), plus `company_name`. Drag stays enabled while filtering (board is stage-only, index-safe) — since #74 that is stated to the shared layer as `KanbanViewConfig.dragPolicy: 'column'` rather than re-implemented. | `cake_os/docs/CRM_FILTER_DESIGN.md` + `cake_os/docs/solutions/architecture-patterns/client-side-facet-filtering.md` |
| **Pipeline parity — board + list on the collection layer** (#73's layer adopted by the CRM's primary surface) — **landed #74** as a rewrite of `frontend/src/crm/PipelinePage.tsx` onto `CollectionView`/`useCollectionState`, plus pure `crm/{pipelineBoard,pipelineSort,pipelineCollection,stageCriteria}.ts` and `crm/components/{pipelineListColumns,StageChipBar}.tsx`; `components/PipelineFilterBar.tsx` is deleted and `crm/pipelineFilters.ts` narrowed to the predicate alone (the search text, the active-facet count and the `crm_pipeline_filters` sessionStorage envelope all became the layer's `collection_crm_pipeline_v1`). **The page keeps its brain and changes its skin:** `data.deals` remains the SOLE owner of optimistic board state — the layer caches no items — so #12/#21's op-sequence, per-deal write chain, server-confirmed-stage rollback and generation-guarded deferred refresh are carried over verbatim, and `handleKanbanMove` still patches before resolving under the exemption now written into `CollectionKanbanProps.onMove` (legal only because it can never reject, which is what makes `useKanbanState`'s rollback branch unreachable). What is new: a **List view** with sortable columns whose keys equal `pipelineSort.ts` field values (so header and dropdown cannot order differently), a **sort control** resting on an `arrayOrder` `boardOrder` field — the page pre-sorts `items` stage-major then `lead_score` DESC, so array order IS the shipped order and drag stays legal at rest — **per-stage visibility** (page-owned `hiddenStages` in sessionStorage `crm_pipeline_hidden_stages`, bridged to the bar through `config.toggles` + `controlledToggles`; cake_os persists this to a `stages.hidden` COLUMN, which CakeCRM's string-constant stages have no room for), **`STAGE_CRITERIA`** popovers rendered INLINE rather than absolutely positioned because the board's `overflow-x` scroller clips a popover at any z-index, and a mobile **`StageChipBar`**. Hidden-stage deals are filtered out of `items` BEFORE the layer sees them (the blueprint's arrangement), which is what makes the totals, the list, search and the bulk intersection exclude them without each re-applying the rule. Two consequences that read as bugs unless you know they are deliberate: a **stage move un-hides a hidden destination** (`revealStage`, called from the sheet's Mark Won/Lost and from a non-rejected bulk move) — an explicit "put it THERE" beats a put-away column, the same call the `?stage=` deep link makes, and without it deals moved into a hidden stage vanish with no message at all, since a clean bulk move is deliberately silent; and hiding **every** stage empties `items`, at which point the layer answers with its own empty state rendered BEFORE its toolbar — so the "N stages hidden · **Show all**" control lives in the page header, above `CollectionView`, where no combination of hides can take it away. The owner facet is declared **unconditionally**, even on a single-user install where the old bar hid it, because `useCollectionState` coerces its persisted envelope once against the facets then declared — a facet appearing later has its restored selection silently erased. NOT ported: `movedDeal.ts` (a CakeCRM move writes `stage` only; `_classify_deal_update` owns the probability/lost-reason rules server-side, and a client mirror would be a second copy of rules #96/#99 own), `deepLinkBanner.ts` (#145 shipped the equivalent here FIRST — the `?deal=` resolution, the dead-link notice and its `boardLoads` generations all live in `PipelinePage` — so this rewrite CARRIED that machinery across rather than porting the blueprint's, translating it from a `selectedDeal` object to the layer's `selectedDealId`; the blueprint's own three-condition `loadedOnce`/`reloading`/`boardStale` gate stays unported for the reason #145's row gives. CakeCRM's `?stage=` link is preserved and now also clears a hidden stage and a persisted List view), the card quick-action menu / `QuickLogModal` / `LostReasonModal` / `BulkUpdateModal` (a third stage-move path obeying the `bulkPending` lock, for actions the sheet already offers), `config.detail` (the sheet stays a page-owned modal at ONE marked seam so #75 swaps it in one file — note the LIST view reaches that same seam through `CollectionView`'s `onSelect`/`selectedId`, which #75 replaces along with the render site; `selectedId` is inert until a `config.detail` exists, and is passed now so the swap needs no second edit), and `usePipelineState` (CakeCRM's own reviewed machinery stays). **#83's archived-deal work was folded into this rewrite when the two met**, and it did NOT take the route #83's own carry-forward note sketched: the archived facet is an ordinary `single` `FacetDef` on `pipelineCollection.ts`, not `getVoided` + the layer's voided tri-state. Two reasons, both structural. The layer's `VoidedFilter` rests at `null` meaning SHOW ALL where ours must rest at live-only, and its facet label and options are hard-coded "Voided"/"Hide voided"/"Voided only" — so adopting it meant teaching the shared layer a per-config default AND per-config copy, shared-layer design a conflict resolution has no mandate for. And `getVoided` is a **load-bearing absence** here (see the bulk-intersection note in the CRM bullet); `pipelineCollection.test.ts` pins it. What the layer DID need is the third item on that list, `CollectionKanbanProps.dragDisabled` widened from `boolean` to `shared/dnd`'s existing `DragDisabled<T>` union, unwrapped in `KanbanView` like every other item-shaped slot and collapsed to a literal `true` by `dragLocked` — a predicate is truthy, so OR-ing the two would hand `boardDragDisabled` a function and leave the drag overlay mounted on a board that cannot drag. The two facts the layer cannot express are handled by the PAGE: an inactive facet's predicate never runs, so the live-only resting state is the server's `LIVE_PREDICATE` and `load` prunes archived rows from `data` whenever a narrowing refetch is deferred or fails; and an empty `items` takes the toolbar off screen with it, so the header carries a **Show archived deals / Show live deals** link beside "Show all" — without it, archiving your last open deal puts the recovery view behind a control that is no longer rendered. | `cake_os/frontend/src/apps/crm/components/PipelineTab.tsx` + `StageChipBar.tsx` + `pipelineListColumns.tsx` + `apps/crm/{collectionConfig,pipelineSort,pipelineBoard}.ts` |
| Archived deals reachable from the UI (Archived facet + inert board cards + `POST /api/crm/deals/:id/restore` + the deal sheet's archived banner/Restore; `get_pipeline(include_archived=)`; per-item `shared/dnd` `dragDisabled`) — **landed #83** across `backend/crm/{service,router}.py` + `frontend/src/crm/{pipelineFilters.ts,PipelinePage.tsx,components/{PipelineFilterBar,DealDetailSheet,DealForm}.tsx}` + `frontend/src/shared/dnd/dragDisabled.ts`. **Not a port — this is the first deal-restore capability in either tree**, which corrects the gate decision's "extends the family pattern" framing: cake_os's Status/`archived` facet exists only for Contacts/Companies over a plain `status` enum (Companies restore by editing that select; Contacts have no restore path at all), and its *deals* have neither a facet nor any restore, front or back — `deal_service.archive_deal` there even hard-drops open todos with the comment "un-archiving never resurrects them". Its list endpoints also default to returning archived rows, where CakeCRM's `LIVE_PREDICATE` + explicit `include_archived: bool = False` is the stronger contract. So the in-repo precedents govern: the chatter-note `/archive`+`/unarchive` POST pair for the route shape, `crm_search_deals(include_archived)` for the flag. **#109 (#74) rewrote every one of these surfaces onto the shared collection layer and carried this feature across, and #110 (#75) landed the sheet half on top of it.** The three exported units survived, two of them relocated: `pipelineFilters.isArchivedDeal` stayed put and is still the single archived predicate the BOARD reads directly (money aggregates, bulk payload, select-all ids, per-card drag gate), while the tri-state facet itself moved into `pipelineCollection.ts` — as a plain `single` facet, NOT the `getVoided` route sketched here; see the #74 row above for why, and for the one collection-layer change it did require. The wire contract is unchanged (`GET /api/crm/deals?include_archived=true`, deals array only, `stage_summary` always live-only), and `advanced.archived` is gone as a concept — the value lives in the layer's `collection_crm_pipeline_v1` envelope, so #83's hand-rolled sessionStorage coercion went with it and its tests moved to `pipelineCollection.test.ts`. The sheet half moved with #75: the banner, Restore and the archived gate on Mark Won/Lost now live in `DealDetailBody`, whose `onBoard`/`stageWritable` split had anticipated them — and the banner renders on EVERY host, because the body reads `archived_at` from its own detail fetch rather than through the canonical-host-row merge. That is what makes it work on the dashboard and the touches page, whose rows carry no board state at all | New capability (no blueprint — cake_os has no deals archived facet or restore; back-port candidate to CAKE OS) |
| Settings page shell (four-section IA in `crm/settingsSections.ts`; underline-tab `<nav>` of `<Link>`s with `?section=` deep links; `components/SettingsCard.tsx` heading/description/padding shell adopted by all nine cards; `components/BrandingCard.tsx` extracted out of the page; member/admin partition, nav and Gmail-callback tests) — **landed #103** as `frontend/src/crm/{SettingsPage.tsx,settingsSections.ts,styles.ts}` + `frontend/src/crm/components/{SettingsCard,BrandingCard}.tsx` + shell adoption in the eight existing cards. Behaviour-preserving apart from three deliberate repairs the chain had accumulated: Team / Assistant memory / Task mode rendered bare `cardStyle` and so had **no padding at all**, Telegram hard-coded `padding: 28` (it took no `isMobile` prop), and Task mode's description spread `labelStyle` and rendered its sentence as 10 px tracked uppercase. Normalising onto `settingsDescription` also moves Assistant memory's description `maxWidth` 560 → 460 and Branding's + Change password's description margin 24 → 20, and Task mode's "No-login links" `<h3>` moves from mono-uppercase `sectionHeading()` to sans-semibold `settingsSubheading`. The Task-mode card's TITLE was renamed **"Tasks" → "Task mode"** (beside "Assistant memory" the bare noun read as the tasks page) — `README.md` and `SECURITY.md` navigation paths were updated for that and for the new section level. `CustomFieldSettings`' entity strip stays `filterTab` but gains `role="group"` + `aria-pressed`, so AT hears a filter there and navigation in the strip above it. Review also gated Notifications' install-wide digest toggle behind `isAdmin` (see the multi-user bullet) — a pre-existing leak this PR's own gating claim made untenable | New capability (no blueprint) |
| **List-page parity on the collection layer** (Contacts/Companies/Tasks: keyset corpus sweep + client-side search/facets/sort, derived `last_contact_at`, routed-detail-as-selection, Owner facet) — **landed #77** as `frontend/src/crm/{collectionConfig.ts,listColumns.tsx,assemblyPage.ts,usePatchableAssembly.ts,ContactsPage,CompaniesPage,TasksPage}` + `components/RefreshButton.tsx` + `sort=id`/`after_id` on the three list endpoints. **The issue's premise is wrong about Tasks**: `cake_os/.../components/TasksTab.tsx` does not exist — that CRM has four tabs (Dashboard/Contacts/Companies/Pipeline) and keeps tasks in a separate `todo-gtd` app that never adopted the layer, so the Tasks page is designed here in the layer's idiom rather than ported. NOT ported: `listRow.ts` (its `toListRow` strip exists because the blueprint's detail BODIES gate enrichment on field presence; CakeCRM's detail pages fetch by id unconditionally, and the overlay merges rather than replaces, so a detail-shaped row is harmless), `lastContact.ts` (deal-specific, with a custom-field precedence that has no analogue — `gtd/util.formatAge` renders ours), the bulk bar (no bulk contact endpoint exists here), `CrmContext`/`pendingNavigation` (real routes, not a tab shell), `useFetchOnce`/`fetchCrmTeam` (`useUsers` is the equivalent), and a Cards view (list-only with responsive column hiding, the blueprint's own call). Two deliberate divergences FROM the blueprint: it pages the sweep by OFFSET over `created_at asc`, ours is a keyset walk on `id` (neither CakeCRM endpoint had an ascending immutable order, and both hard-delete); and its detail rides the modal shell, ours stays routed for the z-index/deep-link reasons in the CRM bullet | `cake_os/frontend/src/apps/crm/{components/{ContactsTab,CompaniesTab,crmListColumns}.tsx,collectionConfig.ts,hooks/usePatchableAssembly.ts}` (Tasks: no blueprint) |
| Composer & label parity (human-writable lost reason + visible owner) — **landed #128** as `POST /api/crm/deals/:id/mark-lost` + `mark_deal_lost(author_id=)` + `crm/dealStageWrite.ts` + `components/{LostReasonModal,OwnerName}.tsx` + Owner rows on the deal sheet and the contact/company detail pages. **The issue's premise was stale on all three items, and only one was a port.** (3) *Note composer keys* was **already shipped by #57/#121** — plain Enter inserts a newline, Cmd/Ctrl+Enter posts, the hint reads `Cmd/Ctrl+Enter to post`, and both the IME and AltGr guards were already in `chatterComposer.composerKeyAction`; nothing was changed for it, and the new modal REUSES that helper rather than copying the blueprint's inline chord check (which has neither guard). (1) *Multi-line lost reason* had **no input to widen** — the blueprint turned an existing `<input>` into a `<textarea>` inside its `LostReasonModal`, while here Mark Lost moved the stage silently and `lost_reason` had no human writer at all, so the capture was built rather than ported. (2) *Unassigned owner* had **no owner row to relabel** — the blueprint's Owner row rendered through a hide-when-blank primitive, where CakeCRM displayed the owner nowhere, so the display was added. NOT ported: the blueprint's unbounded reason field (the service truncates at `MAX_LOST_REASON`, so the field caps and the route 422s instead), and its bulk mark-lost reason (#55 scopes bulk to stage-move). **Known gaps, stated rather than implied:** drag-to-Lost, bulk-move and `DealForm`'s stage select still close deals with no reason, and a reason cannot be corrected after the close — this makes the explicit Mark Lost action carry one, it does not make every close carry one | `cake_os/frontend/src/apps/crm/components/PipelineTab.tsx` (`LostReasonModal`) + `DealDetailBody.tsx` (the Owner row) |
| **Server-side pipeline pagination** (opt-in `limit`/`after_id` keyset pages + a `limit_per_stage` SQL window in ONE `get_pipeline`; `crm/pipelineAssembly.ts` sweep; `sort` refusal on the deals route) — **landed #59** as `backend/crm/{service,router,tools}.py` + `frontend/src/crm/{pipelineAssembly.ts,PipelinePage.tsx}`. **The blueprint framing in the issue body is loose in two ways, both verified in source.** (1) cake_os #1213 "set-based reads" is `bulk_update_deals` going set-based — a WRITE-path batch fetch, not a pipeline read — so nothing here was built "on top of" it. (2) cake_os's pagination is **transport chunking invisible to the user**, not a load-more UX: `deal_service.get_pipeline_page` is a flat `ORDER BY d.id ASC LIMIT/OFFSET` with a full-set aggregate per page, and its `fetchPipeline()` walks every page before rendering once over the complete set, keeping facets/sort/Select-All client-side by explicit decision. Its kanban even disables the shared layer's truncated-column mechanism (`columnCap: Number.MAX_SAFE_INTEGER`) because at ~1,300 deals in one stage it hid the board and locked drag — so a load-more/truncated-column board would have been a **divergence** from the blueprint, not a port of it. That settles the issue's "design decision", together with Will's gate (#54 owns the fork) and #77's in-repo precedent. **Three deliberate improvements on the blueprint**, each because a CakeCRM rule supersedes it: keyset not OFFSET (the repo bans OFFSET sweeps — a deletion behind the cursor skips a row); `hasMore` from an over-fetched row, not a cross-transaction total (#77's rule); and the HTTP route and the agent tool **share one service mechanism**, where cake_os's tool Python-slices an unbounded read — which is the issue's literal ask, and stops `crm_get_pipeline` BUILDING every deal row into the process to return 25 per stage (Postgres still ranks the whole partition before the outer `rn` filter — the win is rows transferred and held, not a smaller scan; a lateral-per-stage rewrite is what would shrink the scan, and is its own perf project). `stage_summary` stays a separate always-live-only full-set aggregate, and is `null` on continuation pages (the `_count_or_none` rule, keyed on the CURSOR so the sweep pays once and an ordinary `?limit=` caller still gets the envelope). `deals_truncated` is derived from a per-partition `rn <= limit + 1` probe — never from `stage_summary`, which excludes won/lost while the cap applies to every stage. `limit_per_stage` is deliberately NOT an HTTP param (no consumer; purely additive later). Also fixed in-PR: `list_deals` used `if contact_id:`, so `?contact_id=0` — which the route deliberately treats as a filter — silently returned every deal. NOT done: a LATERAL-everywhere unification, and a `(deal_id, created_at)` composite index (no measured need; both stated as upgrade paths) | `cake_os/backend/apps/crm/deal_service.get_pipeline_page` + `frontend/src/apps/crm/api.fetchPipeline` (consulted, then diverged from on three points above) |
| Reports tab — single-page company rollup (`backend/crm/report_service.py`, two reads under `/api/crm/companies/{id}/{report,timeline}`, `frontend/src/crm/{ReportsPage.tsx,companyRollup.ts,components/{CompanyRollupReport,CompanyTimeline}.tsx}`, a Reports entry in BOTH nav lists) — **landed #144**. See the CRM bullet for the two-source timeline order key and the archived policy. NOT ported: the `crm_chatter_all` view (no view here — a two-source `UNION ALL` with a `source` term in the order key instead), the company-level activity bucket (`activity_log` has no `company_id`, so the key does not exist rather than being an empty list), the two hardcoded custom-field keys (this port embeds EVERY defined field, joined server-side so an unset one still lists), `DealTodos` (the existing tasks reader's predicates ride the payload instead), and the "My accounts only" owner scope (not in the issue's scope; `GET /companies?owner_id=` already exists if it is wanted). DIVERGES on three points, each because the blueprint's version is wrong here: archived deals are **opt-in** rather than always-on (a third hole of the same shape as `search_deals` and #83's `get_pipeline`); the headline chips are their **own server aggregate** rather than a reduction of the capped child lists, because reducing the lists lets the archive toggle move an open-deal count *downwards*; and the child caps are 200/25 rather than 1000/50, because each child now carries its activities, fields and tasks, so the cap bounds a payload rather than a row count | `cake_os/backend/apps/crm/report_service.py` + `frontend/src/apps/crm/{companyRollup.ts,components/{ReportsTab,CompanyRollupReport,CompanyTimeline}.tsx}` @ `74f902684` (cake_os #2336) |
| **Sync bot — receiving half** (`.github/workflows/sync-intake.yml` + `scripts/sync_intake.py` + `SYNC_LEDGER.md` + `docs/SYNC.md`) — **landed #23**. cake_os fires a keyless `workflow_dispatch` carrying merge **metadata only**; CakeCRM validates, classifies the paths, dedupes on a full-SHA marker, and files an **un-`greenlit`** `sync-intake` issue. Translation is NOT done here — an intake issue enters the ordinary `/auto-issues` pipeline, whose worker reads cake_os from the local clone. **Two structural guarantees:** (1) *never a push* — the sender's token holds **Actions: write** only, which cannot push/PR/create-issue (`repository_dispatch` was rejected because its token needs **Contents: write**, i.e. push-capable against an unprotected `main`); (2) *no upstream text* — the payload has no free-text field, and **no cake_os path is rendered either**, because a path is only *prefix*-constrained and the filename after it is free text that could carry a customer name or forge the dedupe marker. The issue instead names **CakeCRM's own counterpart path**, and only when that file already exists here (already-public name); everything else becomes a count. Asserted, not argued: `test_sync_intake.py` feeds sentinel paths and fails CI if one survives rendering. Verdicts (`crm-code`/`shared-dnd-only`/`internal-paths-only`/`docs-only`/`no-watched-files`) are deliberately **factual, not portability judgments** — portability isn't decidable from a path. `shared-dnd-only` is its own verdict because cake_os's `shared/dnd/` has **13 non-CRM consumers** (CRM is 1 of 14), so a dnd touch is weak CRM evidence. Dedupe is the full-SHA marker check **plus a per-SHA `concurrency` group** (`sync-intake-<sha>`) closing the check-then-create race. The distinction is the whole point: a *global* group would drop distinct intakes (only one run may sit pending), while keying on the SHA serializes exactly the duplicate deliveries and drops nothing. The workflow self-provisions its label and declares `permissions: issues: write` explicitly (the repo default is `read`). **The sender half lives in cake_os and is not built yet** — `docs/SYNC.md` §6 is its spec. | New capability (no blueprint — the cake_os half is its own issue there) |
| **Deal detail on the collection shell** (`DealDetailBody` inside `CollectionDetail`, replacing `DealDetailSheet`) — **landed #75** as `frontend/src/crm/components/DealDetailBody.tsx` + the shared `crm/dealDetailConfig.ts` + `crm/dealLinkPickers.ts` + rewired `PipelinePage`/`CrmDashboardPage`/`WeeklyTouchesDetailPage`, with `DealForm` reduced to CREATE-only (editing is inline in the body now). The `DetailHostConfig` lives in its own module because **three** hosts open deals into the same shell, and a second copy of that answer is how a subtitle fixed on one page and a `loadById` route renamed on another drift apart. **This is the first consumer of the #73 layer on a deal surface**, and three small shared-layer changes made the mount honest rather than faked. (1) `CollectionDetail.config` narrowed to `DetailHostConfig<T>` — a `Pick` of the four fields it actually reads — and `state` became **optional**, because a page that renders its OWN views would otherwise have to mint a `storage` key that shadows its real filter store plus a `defaultView` for a `useCollectionState` it never runs. The rule, documented on the prop: the layer derives an order only from `state`; without it `navOrder` decides; with neither, both arrows disable — the same "disabling beats guessing" answer an unlocatable record already gets. The order memo keys on `state?.view`/`visibleItems`/`kanbanItems` **destructured above it**, never on `state`, which `useCollectionState` does not memoize. (2) `DetailModal` gained an opt-in `underLauncher` rendering `z-50 dock:z-[39]`, and **`CollectionDetail` passes it unconditionally**. That resolves the z-order objection #77 cites for keeping its list pages off the layer: the assistant launcher sits at `zIndex: 40`, and it is the one control that must stay reachable over a record detail because it opens the drawer carrying that record's context (#14) — so the centred modal ducks under it while the full-screen takeover stays above (a phone cannot share the corner with a floating button). Drawer 59/scrim 58, `ConfirmHost` 150 and toasts 200 stay above both, which is also what keeps #128's portaled `LostReasonModal` clickable from inside the panel. It is a PROP, not a new default, because `DealDetailSheet`'s deleted comment recorded the opposite invariant for every OTHER overlay — and it is not a per-surface opt-in either, because an opt-in each consumer must remember is exactly the trap #77 fell into. **The launcher earns that place only while it IS a drawer**: on a keyless install the same button reads "Hire your assistant" and NAVIGATES to `/setup`, which unmounts the open panel and any inline edit draft with it, never reaching that panel's close guard — so `AssistantLauncher` drops to `zIndex: 38` and withholds `data-detail-companion` unless `aiReady === true`. Below the panel is what every other overlay-covered control does, and what the separate `z-50` edit modal this replaced did by construction. The `loading` state takes the same value: it opens no drawer either, and a disabled button cannot accept the Tab hand-off. Residual, documented in `DetailModal`: the Tab trap engages only while focus is inside the panel, so after the drawer returns focus to its external button Tab can walk the covered page — not a regression (the sheet had no trap at all). (3) `confirmDiscardOn` is now **async over the app's `confirmDialog`** rather than `window.confirm`, with deliberately **entity-neutral copy** since every collection detail shares it. **One PUT carries stage AND columns**, which is a correction the plan review forced: `_classify_deal_update` overrides `probability` to 100/0 only inside the transaction that CHANGES the stage, so splitting the save across two requests makes "Stage → won, Probability → 50" a race whose loser silently wins. `moveDealStage` therefore generalized to `writeDeal(deal, patch, fromStage?, lostReason?)` — the stage-specific steps (optimistic restage, `dealConfirmedStage`, the "Failed to move deal." toast) are conditional on the patch carrying a stage, while a fields-only write still takes a sequence number and rides the same per-deal chain so it is ORDERED against a concurrent drag rather than racing it. The fourth argument is the ONE write that is not that PUT: `patch.stage === 'lost' && lostReason !== undefined` routes to #128's `stageWriteRequest`, whose narrower body strands no column because the reason dialog is its only caller and it sends the stage alone. **The REVERT is deliberately NOT conditional**: on a failure the LAST writer for that deal owns the reconciliation, whatever it was writing, so it restores `dealConfirmedStage` unconditionally. A superseded stage write returns before reverting (correctly — a newer op owns the board), so gating the revert on `toStage` stranded optimistic paint the moment the newest op was a fields-only save: drag to won, save a field, both fail, board keeps showing a stage the server never stored. `writeDeal` also **rejects** under the `bulkPendingRef` lock instead of returning silently, because dropping a redundant drag is invisible and fine but dropping typed field edits is not — and `updateDealStage` (Mark Won/Lost) therefore AWAITS it and closes the panel only on success, since closing is this page's way of saying the deal is closed and a fire-and-forget close reported one the server never saw. It also `revealStage`s the destination once the write has gone out, Mark Won/Lost being one of the two paths that can move a deal into a column the user has put away. **Five behaviours came ACROSS from `DealDetailSheet` when the sheet was deleted**, each because `main` had added it there after this branch was cut: #83's archived banner + Restore (`POST /deals/:id/restore`, the row handed UP through `onRestored` so a host patches it in place rather than trusting a silent refetch), #83's archived gate on the close-out pair and the Stage field, #128's `LostReasonModal` + the awaited `closeOut` latch, #128's `OwnerName` row, and `pre-wrap` on `notes`/`lost_reason`. Two of those needed a shape the sheet did not have: `archived_at` and `stage` are read from the body's OWN detail fetch when there is one, never through the canonical-host-row merge, because a deal archived AFTER the board loaded carries `archived_at: null` in that row — and because a close whose response was LOST may already have committed, so `closeOut` reconciles on the way out rather than re-offering a Mark Lost that would append a second note. **`DealDetailBody`'s inline editor uses the same `RecordCombobox` pickers `DealForm` does** (#123/#126), through the shared `crm/dealLinkPickers.ts`: capped `<select>`s here would have kept the edit path on the first 200 rows with no way to link anything outside them, which is the hazard #123 removed. `DealForm` keeps them for CREATE and lost its edit mode, its `isEdit` branch and #83's stage lock with it. The `onBoard`/`stageWritable` split is deliberate: the pipeline passes `stageWritable={onBoard && !isArchivedDeal(deal)}`, the dashboard and the touches page pass `stageWritable` always, since neither has a board to be off and hiding Mark Lost on a "Needs a touch" list would break a real flow — an archived deal is still gated there, by the body's own fetch, which is the only thing that knows. Off the board the body owns its READ channel (`fetched`), and on it the two MERGE with canonical winning per key — `top_deals` selects no `company_name`, so discarding the fetch whenever `onBoard` was true left that row blank; `undefined` is treated as absent, since clearing a field is `null`/`''` on the wire. The **ONE** close guard composes both dirty sources (edit form + inline quick-log draft) and is reused as `canLeave` by the body's own exits — Mark Won/Lost and the contact/company links never reach `CollectionDetail.request()`, so without that they were unguarded holes. Copy-link builds `/crm/pipeline?deal=<id>` **from the id** through `crm/dealDeepLink.ts`, never from `window.location`: the panel is opened from a card or a list row far more often than from a link, so the address bar usually names the board. **#145 owns that parameter's lifecycle** and deliberately KEEPS it, so this issue's original "strip it on read" spec is superseded — a reload reopens the deal, and following the same link twice is distinguished by `location.key` instead. `boardNavOrder.ts` is likewise GONE: `CollectionView` derives `kanbanColumnIds` from `kanban.columns`, so ‹ › walks the board column-major with no page-side wiring, and the dashboard passes `navOrder={[]}` — three unrelated queries, so walking any one would page through records the user did not open from. Fixed in passing and kept: `scrolledStage` gated the `?stage=` DELETE as well as the scroll, so a repeat link to the same column left the param stuck forever, and an invalid `?stage=bogus` waited on `data` that would never scroll anywhere. NOT ported, each for a stated reason: `ImageGallery` (no media store), `DealTodos` (a different directory model, and the blueprint file carries a real customer domain), `ScoreBreakdown` (no per-factor endpoint — `ScorePill` stands), `SearchableSelect` (`RecordCombobox` is this repo's equivalent and predates it), and `useCopyToClipboard` (`TaskModeCard`'s writeText+toast pattern was already the convention). One structural rule the review ladder forced, invisible until a deep link exercised it: the body SEEDS `fetched` from an already-detailed prop, because on that same path `loadById` wins the race, the canonical board row (which carries no `activity`) then replaces the prop, and re-reading `deal.activity` there emptied the timeline and re-fetched identical bytes — so the "already detailed" decision is latched once per mount in a ref. (An earlier revision of this work also hoisted the whole panel above `PipelinePage`'s loading branch, when it was a page-owned sibling. That was REVERTED once the seam put the panel inside `CollectionView`: it buys nothing there — an early return and a ternary unmount that subtree identically — and nothing can be selected before the board lands anyway, since `deepLinkVerdict` answers `idle` until a payload has been applied. The seed above is what survives of it, and it is what the off-board hosts still need.) Relatedly, `dealOpSeq` records `{seq, hasStage}` rather than a bare number: "superseded — leave the newer state" was written when only another stage write could supersede one, and a fields-only save expresses no stage intent, so its success silently undoes a failed drag unless the superseded op still reports. The settle loop added two more: `DetailModal` in `underLauncher` mode hands focus to the ONE element carrying `data-detail-companion` (the launcher) at either END of its Tab cycle, because rendering that button above the panel made it reachable by POINTER while the trap still cycled strictly among panel descendants — leaving keyboard-only users unable to reach the assistant at all, which is worse than the untrapped sheet it replaced. Only the OUTBOUND direction is implemented: the trap is a React `onKeyDown` on the panel, so a Tab pressed while focus sits on the companion never reaches it, and getting back is native order — the honest behaviour for a `role="dialog"` that deliberately does not claim `aria-modal`. And the inline editor keeps `DealForm`'s `pickContact` rule (fill the company from the chosen contact ONLY when none is set), whose loss would have quietly kept newly-linked deals out of their company's rollups. **A deal in a HIDDEN stage is accepted as costing one extra GET**: `CollectionView` resolves `selectedId` from the hidden-stage-filtered `items`, so such a deal falls through to `loadById` — but `onBoard` is asked of the whole payload, so a put-away column never makes a live deal read-only. The blueprint's KNOWN GAP is inherited knowingly and restated in the file header: the guard covers the body's own drafts only, so ‹ › nav can still discard a `CustomFieldsSection` or `NotesThread` composer draft — neither exposes a dirty signal, and plumbing one is its own work. One more parity item the review caught: `leaveVia` carries its own one-in-flight ref mirroring `CollectionDetail.request`'s `pendingRef` (on a CLEAN body `canLeave` resolves in a microtask with no dialog, so two fast clicks both cleared it and ran the exit twice). **The re-settle's own review added one rule worth stating, because it is the seam the close guard cannot cover on its own:** the guard runs at CLICK time, and an unmount cannot be vetoed, so a draft started AFTER an exit was requested would be discarded in silence. Two halves answer it. The body refuses new drafts while an exit is in flight — Edit, the quick-log chips and its note all read one `exiting` flag — and **the BODY, not the host, decides the dismissal**, through an `onClose` prop it calls only while it is still MOUNTED. That placement is the whole fix, and it was reached by elimination: a host has to guess whether the panel in front of it is still the one that asked, and every way of guessing has a hole — the deal id repeats on A → B → A, and a selection counter has to be bumped by every path that ends a session (the deep-link resolution, the dead-link notice taking itself back, a remount forced by the board emptying underneath), which is more paths than anyone enumerates. A mounted body IS the open panel, so "close the open panel" and "close mine" cannot differ; a replaced body simply never calls it, and its successor keeps its draft. The hosts therefore RETHROW a refused stage write rather than swallowing it — a resolve is what tells the body the deal is closed — and `onRestored` patches the row unconditionally (the board must stop showing an archived deal either way) while only the dismissal is conditional. Three details of that make it work and each was a bug first: `mountedRef` is re-ARMED in setup rather than merely cleared in cleanup, because <StrictMode>'s setup → cleanup → setup cycle otherwise leaves it false for the life of the mount and every close silently declines to dismiss — in development only, which is where it would be mistaken for a broken write; `closeOut` swallows the host's rejection, since both buttons fire it without awaiting and the report is already made twice over; and the edit form is FROZEN rather than discarded while an exit is in flight (a `<fieldset disabled>`, so a field added later cannot forget it), because the write may still be refused and the panel would then stay — throwing the draft away on a consent given for a leave that did not happen is worse than the loss it prevents. Relatedly `onSaveDeal` resolves with the SERVER's row rather than `void`: `_classify_deal_update` derives `probability` from the stage inside the same transaction, so folding the patch that went out painted "Won · 50%" on an off-board host until the background re-read landed, and for good if it failed. | `cake_os/frontend/src/apps/crm/components/{DealDetailBody,detailPrimitives}.tsx` + `detailClosePolicy.ts` + the `PipelineTab` mount site |
| Todo-GTD copy affordances + long-title wrapping (two `CopyButton`s on the edit sheet reading LIVE form state; pure `crm/gtd/copyText.ts`; `break-words` + `min-w-0` on `InlineTitle`) — **landed #151** as `frontend/src/shared/hooks/useCopyToClipboard.ts` + `frontend/src/crm/gtd/{copyText.ts,copyText.test.ts}` + `crm/gtd/components/{CopyButton.tsx,TodoEditSheet.tsx,TodoEditSheet.copy.test.tsx,InlineTitle.tsx}`. **#70 had already carried every wrap change** in the upstream commit (`TodoRow`, `InboxPage` incl. `data-inbox-queue`, `ReviewPage`, `WaitingPage` — whose notes preview stays `truncate` by upstream's deliberate exception) because it ported those files *after* this commit; the single gap was `InlineTitle`'s static span — and `break-words` alone would not have fixed it: it renders as a bare flex item on the triage card, and `overflow-wrap: break-word` is excluded from min-content sizing, so without `min-w-0` the item's automatic minimum stays the full width of the pasted URL and the wrap never engages. The four sites #70 ported all carry `min-w-0` on their flex items already, which is why this was the one left. The blueprint sidesteps it with `inline-block max-w-full` on a `<button>`; ours is a `<span>` blockified as a flex item, so `min-w-0` is the translation, not a copy. The Copy half was absent entirely. **One deliberate divergence, and it is the reason the hook is shared rather than local:** both blueprints let a failed copy fall through to a `console.warn`, justified by "HTTPS in prod, localhost in dev, both secure contexts". CakeCRM breaks that premise on purpose — `/todo/{token}` (#70) is a no-login surface built to be opened from a phone on the LAN, i.e. plain http, where `navigator.clipboard` is **undefined** — so both buttons would be dead controls on the one deployment they exist for. `copyToClipboard()` therefore falls back to a `document.execCommand('copy')` staging textarea, and the API-presence check is a **synchronous optional chain, not a try/await**: that ordering is what keeps the legacy path inside the click's user gesture, which is the only reason it works. `crm/components/TaskModeCard.tsx` — the repo's one pre-existing clipboard consumer, and the one that copies those very LAN links — was routed through the same helper (it keeps its own toasts, reading the resolved boolean). **The issue's scope line is a paraphrase, and it is right where the blueprint is thin:** it asks for Copy buttons "(title, notes)", but upstream's two buttons are *whole-todo* and *next-action*, and neither blueprint has a notes-only copy at all. This ships **three** — the two ported ones plus the notes button the issue names — because the notes are where an address or a pasted link actually lives, and the whole-todo copy buries them under the action and up to seven metadata lines. It needs no formatter (`notes.trim()`); the two ported renderings stay in `copyText.ts`, mirrored. **Second divergence, same principle:** `todoCopyText` words the repeat rule through `REPEAT_OPTIONS` rather than printing the stored rule, because `every:3` is storage syntax with no options entry (the sheet renders it as "Every N days…" plus a number input) and this module's own contract is that a copy says what the screen says — the same reason its status word comes from `STATUS_META`. Both blueprints print the rule raw. NOT ported: cake_os's `InboxPage.test.tsx` selector repair (that test does not exist here; the `data-inbox-queue` handle it motivated already does), and the "Title" → next-action label rename (ours already read "What's the next action?") | `cake_os/frontend/src/apps/todo-gtd/{copyText.ts,components/CopyButton.tsx}` + `core/hooks/useCopyToClipboard.ts` (commit `e00e05cde`); chatty `frontend/src/todo/copyText.ts` consulted for the mirrored shape |
| Todo-GTD inbox triage step badges (a local `StepHeading` in `TriageCard.tsx`: the step number as a solid `bg-ck-accent` + `text-ck-accent-ink` badge beside the heading, and a `text-ck-accent-text` star in place of the word "(required)" on step 3) — **landed #162**. A from-spec build, not a port: cake_os #2613 is an open, unimplemented spec, so the issue text is the spec of record. **The rule this establishes for any future glyph-for-words swap here:** both the badge and the star are `aria-hidden` and each carries a visually-hidden equivalent INSIDE the `<h3>`, so the heading's accessible name still reads exactly as the words they replaced ("Step 3: Last step — set context (required)"). An `aria-label` on the badge would NOT do that job — a bare `<span>` is `role=generic`, which does not reliably take a name from the author — and naming the `<h3>` itself would hide the heading's own words from any reader that matches on text. The tests assert the ACCESSIBLE name (textContent minus `aria-hidden` subtrees), never raw `textContent`, which would pass on markup that announces a heading as "01"; all three go red against the pre-#162 component. No token or guard-registry edits: `bg-ck-accent`/`text-ck-accent-ink` is the repo's one solid-accent pairing and is already pinned at AA in `core/theme/hueContrast.test.ts`, and the star is text on the card, which `accent-text` already covers. `stepCls` lost its `mb-2` to the flex row that now holds badge and heading together — same 8 px gap, one owner | New capability (no blueprint — cake_os #2613 is an unimplemented spec) |
