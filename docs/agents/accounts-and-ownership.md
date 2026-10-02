# Accounts, roles, ownership and access

> Topic doc split out of `AGENTS.md` (former Product Rules). `AGENTS.md` keeps the enforceable
> invariants; this file is the full implementation record, moved verbatim. Add new
> implementation notes ("landed #N as …", design reasoning, divergences) HERE, not in
> `AGENTS.md`. Phrases like "the CRM bullet above" or "see the X bullet" refer to the
> rule bullets this file opens with, or to the topic doc that holds that rule; find
> other areas' docs through the Topic docs table in `AGENTS.md`.

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
  **Ownership is NOT access control.** `owner_id` on contacts/companies/deals/todos
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
  work, so per-rep activity credits a rep for work on a colleague's record. Phase A
  stamped them on the human REST paths only, so assistant writes rolled up as
  "Unattributed" — **#190 closed that for attended turns**: the registry carries the
  caller's seat, so an assistant-logged activity or note credits the person talking,
  and a confirmed write credits the APPROVER. Unattended turns still stamp nobody,
  because there is nobody to stamp; those rows stay "Unattributed", which
  undercounts but never misattributes.
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
  Change password, Pipeline board (#124), Link my Telegram (#193) and Assistant memory. **Todo mode is admin-only
  since #102**:
  `todo_mode` is a `crm_meta` singleton, so one member flipping it changes everyone's
  todo surface, and the card's no-login section can mint an unauthenticated read+write
  link to the whole todo store whose lifetime is **not** tied to the account that
  created it (deactivating that user revokes their JWT via `token_epoch`/`is_active`,
  not the URL). `/api/crm/todo-mode` and both `/api/crm/todo-surfaces` methods are
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
  **Assistant conversations ARE access-controlled** (#191, Phase B/B2) — the one place
  the product does that, and a deliberate NEW position rather than an extension of the
  old one. "Ownership is not access control" is scoped to **CRM records**: a deal is
  team data with a name attached. A conversation is not — it carries drafts,
  half-thoughts and private asks — so `assistant_conversations.user_id` is a real
  permission, **owner-only with NO admin override**. Adding an admin read path later is
  additive; removing one would be a breach, which is why the strict direction ships
  first. Every path that reaches a conversation from a seat is scoped to it: the four
  REST endpoints, `engine.chat`'s resume of a client-supplied `conversation_id`, and
  `/confirm`'s claim — the last two are what a naive pass misses, and without them the
  REST filters are decoration because any seat could resume another's thread by uuid.
  Cross-seat access answers **404, never 403** (no existence oracle), and `/chat`
  answers a foreign uuid with the byte-identical SSE error an unknown one gets.
  Mechanically: `history.py`'s six conversation functions take a **keyword-only,
  REQUIRED** `user_id` (`None` = no filter, for trusted internal callers only — required
  rather than defaulted so a future route cannot silently reopen the hole), and
  `engine.chat`/`resolve_confirmation` take a required keyword-only `user` for the same
  reason. Migration `20260917173429` claims legacy rows for
  `MIN(id) WHERE role='admin' AND is_active` (a no-op on a fresh install, where the
  table is empty), and `users/bootstrap.py` repeats the claim inside the transaction
  that seeds the first admin — the same thing
  it already does for legacy `totp_config`/`trusted_devices` rows. **Both are needed:**
  an install upgrading straight from a pre-multi-user release runs
  `20260821100126_multi_user.sql` and this migration in ONE startup, before any admin
  exists, so the migration's subquery is NULL and never re-runs; without the bootstrap
  half that install's whole chat history would stay unowned, and an unowned conversation
  is invisible to every seat. **`AND is_active` is load-bearing in the migration**
  (and in `users.service.earliest_admin_id()`, which is the same expression and stamps
  the seatless Telegram thread): access here is owner-only with no admin override, so
  claiming history for a deactivated lowest-id admin would hand it to a seat nobody can
  authenticate as. With no active admin the subquery is NULL, the UPDATE is a no-op and
  the rows stay unowned — invisible, which is the fail-safe direction. The bootstrap
  claim needs no such predicate: it stamps the admin row it just INSERTed as active, and
  only ever runs on an install with no users at all. The column is `ON DELETE CASCADE`
  (personal data follows its person, the `totp_config` idiom, not `owner_id`'s SET
  NULL).
  **Telegram is per-seat since #193** (Phase B/B4), which deleted the
  `earliest_admin_id()` stopgap `telegram/store` carried for exactly that issue — each
  link now mints its conversation with its own owner. See the Telegram bullet below.
  **A notification can carry a LINK since #235**: nullable `notifications.link`, set only
  through `deliver_notification(..., link=)`, which keeps it only if it is a same-origin path
  (`/…`, not `//`, no backslash, no whitespace or control character) and otherwise drops the
  link while still sending. It is the bell row's title link, the Web Push click-through `url`
  (previously always `/crm`), and a trailing line on the Telegram text via
  `crm.links.app_url` — absolute only when `FRONTEND_URL`/a Railway domain is set, the same
  known limit `deal_url` documents. `notify_user` does not take one, so an assistant turn
  cannot mint a link. The first sender is the chatter @-mention (`docs/agents/crm-core.md`).
  **Still install-wide after Phase B, deliberately:** memory facts, context files, the
  Gmail CONNECTION, alerts and the AI provider keys. That list is what is left, not a
  to-do: conversations (#191), notifications (#192) and Telegram links (#193) are
  per-seat, and reminders — which used to sit here — no longer exist (#188). Memory
  facts and context files stay shared **permanently** (Phase B Decision 1 — they are
  team knowledge, and per-seat facts would make the assistant amnesiac for every new
  seat), which carries an honest cost that is documented rather than engineered around:
  a fact recorded out of a now-private conversation is still install-visible, so the
  README tells users not to ask the assistant to remember what colleagues must not see.
  Alerts stay install-wide (system-level senders, a member is often the right
  responder), and the keys are single-tenant by design.
  **The Gmail connection is shared but its ACCESS is not, since #194** (Phase B/B5).
  `get_gmail_tools(user=…)` returns `([], {})` unless the connection exists AND the
  caller is an admin seat — or the install has turned on `share_with_all_seats`, a
  boolean on the `gmail_connection` singleton, default off, flipped by the admin-only
  `PUT /api/gmail/sharing` and reset by every path that installs a new connection
  identity (`clear_connection`, `save_app_credentials`, `save_tokens`), so a new grant
  starts private. `user is None` is tested FIRST and needs no DB, so an unattended turn
  is denied even with sharing on and even when the row is unreadable — a second, earlier
  lock in front of `BACKGROUND_EXCLUDED_TOOLS` (#114), neither relying on the other. So
  Will's §15 ruling still holds — every active seat gets the assistant, no temporary
  gate to remember to remove — but a member's assistant no longer carries the admin's
  mailbox. Per-seat Gmail OAuth stays #189, demand-gated. `test_gmail_guard.py` is
  untouched in substance (read + create-draft only, forever, same three tools, same op
  allow-list, same scopes, same draft binding); its one edit is that the exfiltration
  guard now builds its registry as an ADMIN seat, because the seat gate would otherwise
  make that guard's own vacuity precondition false and let it pass without testing
  anything — and it gained an assertion that an unattended registry carries no Gmail
  tools at all.
  **Writing a PROTECTED context file is admin-only on BOTH doors** — the REST write since
  #194 (Decision 1d), the assistant's tool since #213. Both run on the NORMALIZED name
  (`service.normalize_filename` → `service.PROTECTED_FILES`), which is load-bearing:
  gating the raw string would be bypassed by typing `SOUL.MD`.
  *REST:* `context_files/router.put_context_file` 403s a non-admin, in-handler on purpose
  rather than a route-level `require_admin` — the same route serves member-writable
  `topics/` and `daily/` writes, so the gate has to see the filename, and
  `test_route_authz`'s `ADMIN_ONLY` pin is therefore untouched by it (the one entry that
  pin DID gain is the Gmail sharing route, which is a genuine route-level gate).
  *Tool:* `get_context_file_tools(user=…)` wraps `write_context_file` in
  `_admin_only_protected_writes`, which refuses a protected filename for a non-admin
  seat. **This is shape B of the two #213 named, and the distinction matters:** unlike the
  Gmail seat gate the tool is still ADVERTISED to every seat — the defs are identical for
  admin, member and unattended registries — because withholding `write_context_file`
  would also take members' topic and daily writes away unless the tool were split in two.
  Refusing inside the executor keeps one tool and one rule shared with the REST door, at
  the cost that the model is sometimes offered a write it will be refused; the member
  entry in `identity._ROLE_NOTES` (advice only, #200) buys that back by telling Baker not
  to offer the rewrite. The wrapper is applied at collection time, the
  `crm.tools.bind_server_args` shape, so the module-level `CONTEXT_FILE_TOOL_EXECUTORS`
  map stays seat-free. Fails closed on `user=None` (an unattended registry, a second lock
  in front of `assistant.background`'s read-only allowlist), on a row with no role or an
  unrecognised one, on a non-dict user, and on an unparseable filename. Resolving the
  role ONCE per collection is safe because the registry is rebuilt from a freshly loaded
  row for every turn AND every confirmation (`assistant.router.confirm`,
  `telegram.service`), so the seat that APPROVES decides — a write proposed while admin
  and approved after a demotion is refused.
  **Why the tool needed its own gate:** `requires_confirmation` is not a role. It stops
  every protected write and shows the content, in power mode too, but the approver is
  whoever is in the conversation, so before #213 a member could ask for a `soul.md`
  rewrite and approve their own request (proven live in #194's evidence run,
  `written_by='assistant'`). The card is UNCHANGED and still fires for admins — the two
  gates are independent. Reads stay member-open (visibility is the Memory page's whole
  point) and `delete_context_file` needed no wrapper, because `service.delete_file`
  already refuses a protected file for every seat including an admin.
  `MemoryPage` renders those two files read-only for a member so the 403 is never the
  first thing they learn. The dead `MULTI_USER_ENABLED` flag was deleted — grep found
  only its own definition and the docstring advertising it.
