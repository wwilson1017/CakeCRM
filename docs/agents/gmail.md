# Gmail — read + draft only

> Topic doc split out of `AGENTS.md` (former Product Rules). `AGENTS.md` keeps the enforceable
> invariants; this file is the full implementation record, moved verbatim. Add new
> implementation notes ("landed #N as …", design reasoning, divergences) HERE, not in
> `AGENTS.md`. Phrases like "the CRM bullet above" or "see the X bullet" refer to the
> rule bullets this file opens with, or to the topic doc that holds that rule; find
> other areas' docs through the Topic docs table in `AGENTS.md`.

- **Gmail is read + draft only, forever.** There is no email-send tool anywhere in
  the codebase, and none may be added. Google scopes can't express "draft but not
  send", so the guarantee is enforced at the tool layer: the registry exposes read
  and create-draft tools only. This is a documented trust guarantee (SECURITY.md).
  Landed #8 as `backend/gmail/` (mounted `/api/gmail`) + `frontend/src/crm/components/GmailCard.tsx`:
  BYO Google OAuth app (client_id/secret + tokens Fernet-encrypted in the
  `gmail_connection` singleton), scopes `gmail.readonly` + `gmail.compose` only, the
  three tools `gmail_search`/`gmail_read_thread`/`gmail_create_draft` collected via
  `gmail.tools.get_gmail_tools(user=…)` (defs gated on the connection AND — since #194 —
  on the SEAT: admin seats only, or every seat when the install turns on
  `share_with_all_seats`; a turn with no user gets nothing at all. Executors follow the
  defs either way), `gmail_create_draft` marked `writes:true`. The seat gate decides who
  is OFFERED the tools and never what they can do. Enforcement artifacts: the guard test
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
