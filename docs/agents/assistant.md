# Assistant, AI providers, memory and the help library

> Topic doc split out of `AGENTS.md` (Product Rules). `AGENTS.md` keeps the enforceable
> invariants; this file is the full implementation record, moved verbatim. Add new
> implementation notes ("landed #N as …", design reasoning, divergences) HERE, not in
> `AGENTS.md`. Phrases like "the CRM bullet above" or "see the X bullet" refer to the
> matching Product Rules bullet — find its topic doc through the index at the top of
> `AGENTS.md`.

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
  **Normal mode auto-approves a routine tier of writes** (#180): a tool def may carry
  `"confirm_tier": ROUTINE` beside `"writes": True` — the constant lives alone in
  `assistant/confirm_tier.py` (an import-free leaf, because `crm/tools.py` declares the
  tier and `assistant/registry.py` validates it, and the registry already imports
  `crm.tools`). The registry adds it to `_INTERNAL_KEYS` (so it never reaches a
  provider), FAILS LOUD at construction on any other value or on a tier declared on a
  read, and exposes `is_routine_write(name)` — pure set membership, so **absence is the
  deny state**: an unknown name, a read and every unclassified write all confirm.
  Twenty tools carry it. Sixteen are `crm_*` (create/update contact, company, deal;
  deal stage; won/lost; log activity; create/update/complete todo; set custom fields),
  and **#186 added the four `todo_*` ones that GTD todo mode — the product default since
  #102 — puts in their place**: `todo_create`, `todo_update`, `todo_create_project`,
  `todo_update_project`. Without those, #180's own motivating example still raised a card
  on nearly every install, because `_TODO_TOOL_NAMES` hides three of the sixteen in GTD
  mode. The other six todo tools stay unclassified on the rule: `todo_bulk_update` is
  bulk, the two deletes remove a record, and the three reads cannot carry a tier at all.
  No single registry holds all twenty — normal mode loads the sixteen, GTD mode
  thirteen plus four — so `test_confirm_tier.py` pins the SOURCE set and each registry
  separately. The classification rule and the never-routine list live in
  `assistant/confirm_tier.py` and `SECURITY.md`. Never add one to a tool that notifies,
  deletes/archives/merges, bulk-writes, or leaves the install. In the engine the routine predicate may appear
  **only** as a narrowing of the normal-mode term of the gate, never as a term of its
  own, so a stronger rule can never be masked by it. Two consequences of "normal no
  longer means confirm everything" are load-bearing: the power→normal demotion for
  untrusted content was replaced by a `context_is_untrusted` boolean that reaches the
  gate directly (demoting INTO a mode that auto-approves would have switched the
  mitigation off silently), and the same-turn Gmail binding was generalized from the
  power arm to every mode. Only the first costs anything: normal-mode turns now pay the
  one indexed `is_conversation_tainted` read that power mode always paid, deliberately.
  The Gmail generalization is free — it widens where an already-set in-memory flag is
  consulted. `assistant.confirm_tier.removes_from_view()` is the argument-level
  carve-out: contacts and companies archive, and todos (in either mode) and GTD projects drop,
  through a `status` argument — including one the def does not advertise, since nothing
  validates tool arguments against the schema at runtime — so that one call keeps its
  card while the tool stays routine. It **moved there from `crm/tools.py` in #186**,
  when its keys stopped belonging to one tool module (`todo_update*` are defined in
  `crm/gtd_tools.py`); that also took `assistant/engine.py` off its only import of CRM
  feature code, so the gate reads as assistant-layer vocabulary end to end. `dropped` is
  the carved-out value on both todo keys and nothing else is: `done` on a todo and
  `completed` on a project are completion (the same call #180 made for a todo), and
  `someday`/`someday_maybe` is filing between working lists that each have their own GTD
  page. Only `dropped` is a soft delete — `todo_delete`'s own description tells the model
  to prefer it — and it is the one argument that can hide either row, because
  `gtd_service._check_fields` RAISES on any field outside `TODO_FIELDS`/`PROJECT_FIELDS`
  (unlike `service.update_todo`, which silently filters), so `deal_id` — the only other
  column that hides a todo — is unreachable through `todo_update`.
  **The tier is bounded by a RESULT-conditional untrusted taint, not just a tool-name
  one** (#204). `/api/capture` is mounted unconditionally and takes text from an
  unauthenticated stranger while no `todo_capture_token` is set (#70), so until #204 that
  text reached the model through `todo_list`/`todo_get` unfenced and untainted — a prose
  "treat as data" note rode the payload and nothing else did — and an injection planted
  there could steer any routine write with no Approve card. That was true of the thirteen
  non-todo `crm_*` routine writes before #186 ever classified a `todo_*` one, so #186
  widened the reachable set rather than opening the path.
  `delimiters.fence_public_rows` walks every tool result for a row carrying
  `source='capture_web'` and nonce-fences its prose as
  `<untrusted_external_content source="public_capture">`;
  `fence_tool_result(tool_name, result) -> (content, tainted)` is now the ONE place both
  execution loops serialize and fence, and `engine._chat_impl` sets
  `turn_has_untrusted_reads` off that flag instead of off
  `name in _UNTRUSTED_SOURCE_TOOLS`. The taint COMPOSES and is never cleared, so a context
  read (fenced-but-untainted by ORIGIN) cannot reset one the row already set. Keying on the
  row rather than the tool covers `crm_list_todos` — the same `todos` rows in the other todo
  mode — and unconfirmed write echoes for free. **Matching a row never ENDS the walk**:
  a nested record carries its own `source` and answers for itself, which matters because
  `crm.service.get_contact_detail` returns `{**contact, "todos": [...]}` and
  `contacts.source` is the free-text LEAD source a user types, so a contact whose source
  reads `capture_web` would otherwise shield every capture row nested under it — on a read
  that is background-callable, where the fence is the only control. **Adding the three todo reads to
  `UNTRUSTED_SOURCE_TOOLS` was the rejected alternative**, for two reasons: that set IS
  `background.BACKGROUND_EXCLUDED_TOOLS` (#114), so it would blind the heartbeat on the
  surface `heartbeat.service._heartbeat_prompt` names in GTD mode, the default install; and
  a whole-payload fence would mark the user's OWN todos as adversarial data the assistant
  must not act on, which in GTD mode is the product.
  **`resolve_confirmation` TAINTS but does NOT fence**, deliberately and not by omission:
  `history.get_tool_result` hands the persisted content straight back to the /confirm caller
  on a duplicate Approve, so fencing there showed the HUMAN nonce markup where the record's
  text belongs. The taint is what carries the property — every later write in that
  conversation confirms regardless.
  Four residuals, stated rather than rediscovered: `PUBLIC_ROW_STRUCTURAL_FIELDS` is
  deny-by-default, so a new free-text column on `todos` is fenced automatically while a new
  ENUM column is fenced until it joins that set — and the three TIMESTAMP names in it are
  load-bearing, not a hedge, because `core.postgres._postprocess_value` makes every
  timestamp an ISO string before a row reaches the walk; `contacts.source`/`companies.source`
  are lead-source free text, so a contact whose source someone typed as literally
  `capture_web` costs one Approve card; an install that actually uses public capture will
  taint the turns that list its inbox, which is the price of the guarantee and is narrower
  than shape B's; and `todo_projects` has no `source` column and `gtd_service.capture`
  cannot create a project, so `todo_list_projects` carries no stranger text and needs none.
  **A fence nested in JSON arrives with its quotes ESCAPED**, which
  `assembly._reclose_untrusted` could not see until #204 widened its three patterns — a
  truncated oversized row otherwise handed the model an open fence, the one thing that
  function exists to prevent. The tokenless capture DEFAULT is unchanged: shrinking the
  on-ramp is a product question, not a fix to the read path.
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
  switch off every CRM discipline with it. `identity.COACHING_GUIDE` (#201) rides the
  same lever for the same reason, as its OWN static block immediately after
  `SALES_GUIDE`: that guide is operating discipline, this is the advisory voice built on
  top of it — read the health/staleness/analytics signals FIRST and ground every claim in
  a tool result, lead with the one or two highest-leverage moves, coach the process and
  ask about what the CRM cannot know, state the real span when
  `history_covers_window` is false, and describe the deal, never judge the person.
  Coaching is deliberately NOT a help-library topic: a topic is content Baker MAY read,
  behaviour is something it MUST keep. The assistant's **second execution mode** (landed #6,
  `backend/assistant/background.py`) is a non-SSE `run_background_turn` for
  autonomous work (the heartbeat + the proactive digest): it reuses the same
  `ToolRegistry`/`build_tool_turn` loop but, having no human to confirm writes,
  runs under a **server-enforced tool allowlist of READ tools + `notify_user` only**
  (no CRM writes at all — enforced at both advertisement and execution) plus a
  `WRITE_BUDGET_BACKGROUND`, with untrusted CRM text kept in the user
  message, never the system prompt. So a prompt injection via CRM content
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
  reads the fence — so injected CRM text could steer it `gmail_search` →
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
  due-guard) and is driven by **#6's 60s `maintenance_tick`** (via
  `heartbeat.service._maybe_run_dreaming`) — #5's interim lifespan task was absorbed
  when #6 landed, exactly as planned.
  Since **#72 Phase 4 the cycle has a SECOND unit**: live `topics/*.md` context files,
  scored by `scorer.score_file` inside `_run_cycle`'s *existing* transaction — same
  advisory lock, same 03:00 slot guard, same `SET LOCAL` timeouts, same audit row, **no
  new job** (file-dreaming is local SQL, so it rides the tick; the observer below is
  AI-bound and does not — that pair IS the rule, not an exception to it). The usage
  signal is `assistant_context_files.read_count` / `last_read_at`, bumped by
  `context_files.service.track_read_for()` from **exactly three places**: the
  `read_context_file`, `read_daily_note` and `search_context_files` tool executors (the
  search records the *surfaced subset* only). It is deliberately **never** bumped by
  `build_knowledge_block`, by the manifests, or by the REST router — that is the
  **load-count trap**: `soul.md`, `MEMORY.md` and both manifests load on every single
  turn, so an unconditional count would be a constant and nothing could ever be archived.
  Same call #5 made for facts (FTS matches count, confidence backfill does not), and a
  test invokes every excluded entry point with non-empty fixtures so it cannot pass
  vacuously. Chatty's five file signals renormalize to four — read recency .35,
  recency-gated read frequency .20, write recency .25 (`updated_at`, the one signal a
  file has and a fact does not), age .20; mention frequency is dropped, it has no
  producer here. **Topic files only** (Decision C): protected files and daily notes are
  never archived, re-asserted in SQL from the GENERATED `kind`/`is_protected` columns
  rather than a Python set. The archive UPDATE deliberately does **not** set
  `updated_at` — it is both the write-recency signal and the Memory editor's
  optimistic-concurrency token, so bumping it would inflate the score and 409 an open
  editor. Archival stays soft: `read_file` still returns archived rows and any write
  un-archives. `dreaming_runs` gains `files_scored`/`files_archived`, exposed by the REST
  endpoint as counts only — archived *filenames* live in `details` and are the same kind
  of leak that already keeps `details` out of the response.
  **The observer** (#72 Phase 4, `backend/memory/observer.py`) is the assistant's **only
  automatic learning path** and the one AI member of `backend/memory/`. It reads the
  `role='user'` rows written since a per-conversation watermark
  (`assistant_conversations.observed_through_seq`), makes **one light-tier call per
  settled conversation** (settled = quiet for 10 minutes counting *every* message, not
  just the user's — a still-streaming assistant reply means nothing has settled yet; and
  eligible on **rows OR characters**, because a row count alone never observes a user who
  types one substantial message and stops), and writes two row shapes: `memory_facts` rows with
  `created_by='observer'`, `source='conversation:<id>'` and confidence **≤ 0.9**, and GTD
  **`inbox`** todos with `source='agent'` and no owner. It is **not an agent turn** — no
  tools, no registry, no iteration, a fixed JSON schema — so the background allowlist and
  the one-`notify_user` ceiling are untouched and it **cannot schedule a notification of
  any kind** (Decision B: the inbox IS the confirmation discipline for a writer with no
  human to ask; a test reads the module's own AST and proves it references no delivery
  channel). Chatty's THREE pipelines (observer, per-fourth-message extractor, 581-line
  commitments store) collapse into this one pass; its `observations` table, commitment
  lifecycle and per-provider HTTP fan-out are not ported. Injection posture: assistant
  rows — and therefore every tool result, Gmail body and context-file read — are excluded
  **by SQL, not by filtering**; a user row carrying an upload fence is skipped whole (fail
  closed); the transcript **and** the already-tracked todo titles are each nonce-fenced
  separately; rows older than 14 days are dropped **per row**, not per segment. Dedupe and
  supersession run off `memory.service.find_live_facts_by_key` (exact, case-folded, in
  SQL — `query_facts` is unescaped-ILIKE substring matching and must never be used for
  this), it **refuses to supersede any fact it did not write**, and the replacement is
  **inserted before** the old row is invalidated so a crash costs a duplicate, never the
  fact. It runs on its own 60s scheduler job, claims one run per 15 minutes with a
  rowcount UPDATE on `heartbeat_state.last_observer_run_at`, respects
  `settings.heartbeat_enabled`, and is a **complete keyless no-op** — with no provider it
  returns before the claim, the query and any log above debug. The migration backfilled
  every existing conversation's watermark to its own `max(seq)`, so history is never
  replayed; that backfill is **once-only by the runner's `_migrations_applied` ledger**,
  not by being idempotent, and re-running it would consume unobserved messages.
  **Context files** (#72 Phase 1+2, `backend/context_files/` + `frontend/src/crm/MemoryPage.tsx`)
  restore chatty's *other* memory unit, which #5 deliberately skipped: `soul.md` (the
  assistant's self-written identity), `MEMORY.md` (its living snapshot), `topics/<name>.md`
  and `daily/YYYY-MM-DD.md`, all rows in `assistant_context_files`. So the #5 note above
  that "dreaming's unit is the fact because CakeCRM has no context-file store" now states
  a *history*, not a constraint — **file-dreaming landed in Phase 4 and is the only writer
  of `archived_at`** (see the dreaming paragraph above); every read already carried the
  live predicate, so that phase was purely additive. `kind` and `is_protected` are **GENERATED columns** derived from
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
  SALES_GUIDE; since #201, `COACHING_GUIDE` sits directly after SALES_GUIDE), so a
  self-rewritten soul can add to who Baker
  is but never override a tool or security contract — or its own name. `DEFAULT_SOUL` is the
  blank-means-default fallback constant (same pattern as `personality`); the migration seeds
  **empty** content so a later boot can never overwrite an edited soul, and the constant is
  scanned by `test_prompt_genericization.py`.
  Security: writes carry `writes:true` (so `background_allowlist()` excludes them — asserted
  explicitly, not left to the derivation), and a write to a **protected** file additionally
  **always confirms, power mode included**, via `context_files.tools.requires_confirmation()`
  consulted from the engine's gate — `writes:true` alone is NOT enough there, because a
  poisoned `soul.md` is a permanent system instruction, not one bad record. That hook
  **fails closed** on a missing/unparseable filename. Since #213 a protected write ALSO
  needs an admin seat (`_admin_only_protected_writes`, §multi-user) — always-confirm is
  not a role, and the approver is whoever is in the conversation; the two gates are
  independent and both still fire. `read_context_file`/`read_daily_note`
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
  **Phase 4 (observer + commitments-as-inbox-todos + file-dreaming) landed**; **Phase 5
  (the optional embeddings re-ranker) was DECLINED** on 2026-09-14 — the default provider
  cannot embed at all, and without pgvector a stored vector could only re-rank what FTS
  already found, so it could never surface what FTS missed. FTS is the search,
  permanently. The Phase 4 PR carries no closing keyword; #72 is closed by hand.
  Assistant memory is install-wide and stays that way — Phase B Decision 1 settled it
  as permanent team knowledge rather than a gap; chat history is per-seat since #191,
  and writing the two PROTECTED files is admin-only on both doors — REST since #194, the
  assistant's `write_context_file` since #213.
  **The product manual is a searchable library of committed markdown** (#143 phase 1,
  `backend/help/`): `content/**/*.md`, one file per topic, loaded lazily and cached,
  warmed once in the lifespan by a `warm()` that CANNOT raise. Three keyless
  `writes:False` tools — `help_search`, `help_read_topic`, `help_list_topics` — ride
  `help.tools.get_help_tools()` unconditionally, and their DEFS are constants: the
  registry is composed on every turn, so a def built from the corpus would put disk I/O
  and a possible exception on that path and make one bad topic file break chat. Files,
  not a Python constant and not a table: the manual is product content versioned WITH the
  code, so it changes atomically with the release that changes the feature it describes,
  while a prompt constant would grow the cached prefix without bound and a database copy
  would need a seed on every wording edit. Search is a pure ranking pass over the one
  cached library (no index, no embeddings), deterministic by construction — ties break on
  slug — with a one-character plural fold and a half-weight prefix match standing in for a
  stemmer. Only `identity.HELP_NOTE` enters the prompt: a few slim lines naming the
  sections and the discipline (search before answering; say so when the library does not
  cover it), placed in the STATIC half after `CONTEXT_FILES_NOTE` and before the
  upload-safety instruction, and pinned by POSITION rather than presence. Help results are
  deliberately NOT fenced and do NOT taint the turn — `UNTRUSTED_SOURCE_TOOLS` encodes
  third-party origin, and this is our own reviewed prose, the one payload class that
  cannot carry hostile user text — which is pinned in both directions so a later
  "fence everything" refactor fails loudly. `backend/tests/test_help_library.py` is the
  guard: every tool-shaped token in the library must name a REGISTERED tool (matched
  tool-shaped, never by prefix — a prefix rule misses every unprefixed tool, the seven
  context-file ones included), no topic may promise to deliver mail, no topic may contain
  an untrusted fence marker (one quoted marker would permanently downgrade every later
  turn of any conversation that read it), and the settings topics' admin flags are read
  out of `settingsSections.ts` itself rather than a second hand-written list. **The
  convention half is stated and not testable: a PR that changes a user-facing flow updates
  its topic.** Phase 3 (a coaching guide + chips) is a follow-up issue; #143 stays open as
  the tracker.
  **The playbooks shelf (#209) is content ON that library, not a feature beside it:**
  `content/playbooks/<book>.md`, one topic per published sales or negotiation book (ten in
  v1), each an own-words summary carrying the same four H2 sections in the same order —
  `Core ideas`, `When Baker reaches for it`, `Applied to the CRM`, `Go read it` — with the
  technique names as `aliases`, so a search for the technique lands on the book and search
  itself needed no change (aliases already score 6 with a phrase bonus, and the author's
  surname is one of them, so "what does Cialdini say" resolves with no `author` field in
  `search._field_tokens`). The per-book search pins in `test_help_library.py` pin that
  END-TO-END ranking rather than one field — alias, title, description and body all feed
  the score, and by mutation most of those queries still resolve with the aliases removed
  — so the alias line is guarded structurally by `_playbook_defects` instead; a pin that
  demanded the hit come from the alias field would fail on an innocent reword.
  Attribution IS the copyright posture — ideas
  summarized and attributed, no reproduced passages, at most one short attributed quote,
  a "go read it" line, published books only (CONTRIBUTING.md) — so it is structural: a
  fifth front-matter key `author:` (optional at the loader, `Topic.author`, title-sized
  ceiling), surfaced by `help.tools._brief` only when set so the product topics' listings
  do not grow an empty field. `test_help_library.py` section 7 guards the shape per topic
  in both directions and parses H2 itself rather than using `Topic.headings`, which strips
  the level and would pass four H3s or a fifth section; its per-book search pins sit
  alongside the product pins, which is also the check that the shelf stole none of them.
  Whether the prose is genuinely ours is the convention half, reviewed in the PR. Reactive
  only: `COACHING_GUIDE` aims `help_search` at the technique, the objection or the buyer's
  behaviour — status-worded queries are won by the product's own deal topics — and has
  Baker name the book and author; `HELP_NOTE` lists `playbooks` among the sections. No
  proactive nudge, no shelf UI, no new tool, no weight change.
  **Phase 2 (#200) is the only phase that edits the engine and the router**, and everything
  it adds is either VOLATILE prompt or a keyless read — the static half and the record
  context are byte-for-byte what they were. Three parts. (1) `ChatRequest` gains a SECOND
  optional field beside `context`: `SettingsPageContext` (`page: Literal["settings"]`,
  `section: Literal["personal","assistant","workspace","integrations"]` — the
  `settingsSections.ts` ids restated as a backend Literal). It is a separate field rather
  than a widening of `ChatContext`, so the record boundary is untouched and a turn may
  carry both; unknowns are 422 on `/chat` and **400 on `/chat/upload`**, which threads it
  *identically* to the way that hand-parsed path already treats `context` — the two seams
  must not disagree about which status a bad enum gets. `identity.build_page_note` builds
  the English server-side from `_SETTINGS_SECTION_HELP` (section id → plain-words gloss +
  manual topic slugs) plus the validated id, never from client text, the same defence in
  depth `build_context_note` draws; the note names the section AND the topics covering it,
  tying "where" to "how", and says the assistant cannot change settings itself. Those slugs
  are pinned against the real library by `test_help_library.py`, and the map's key set is
  pinned against the router's Literal, so a renamed topic or a drifted section fails CI
  rather than producing no note at all. **#193 landed between this branch's review and its
  merge and split Telegram in two** — the bot is install configuration (admin, Integrations)
  while a linked chat is a person's own device (member-visible, Personal) — so the manual
  gained `settings/telegram-link` beside `settings/telegram`, the Personal section names it,
  and `_TOPIC_TO_CARD` covers the new card. #191 and #193 had also left three OTHER topics
  asserting the pre-split world — `settings/notifications`, `settings/team` and
  `assistant/permissions` all still said the Telegram link and the chat history were
  install-wide — #194 reconciled all three ahead of this branch, so it rebases onto that text rather than restating it: contradictory topics are worse
  than a missing one, because which answer Baker gives then depends on what search returned. Two cards with different gating cannot share one
  topic, because a topic carries exactly one `admin` flag; the admin topic's stale
  one-account-per-install claims were corrected in the same pass, per the convention that a
  change to a user-facing flow updates its topic. `build_system_prompt` takes it as a **keyword-only**
  `page=` so every existing caller is unchanged. The frontend derives it from the URL with
  the same `wantedSection`/`resolveSection` helpers `SettingsPage` uses
  (`assistant/pageContext.ts`, consumed by `AssistantLauncher`) — no second publisher, and
  the member fallback comes free — and `useAssistantChat`'s per-message turn snapshot now
  carries record AND page together, so a post-confirmation continuation resumes the turn as
  it started. (2) The seat's **role** is one more sentence from `build_user_note`, which
  already builds the volatile "who am I talking to" line from the same auth row. Its
  vocabulary is derived from `test_route_authz.ADMIN_ONLY` — the list CI already pins in
  both directions — and a test asserts the member note names every install-wide control a
  person might ask about, because a control it fails to name is a control it fails at. The
  trap worth remembering: Notifications is ONE card with two halves, so "notification
  preferences are yours" is a positive ERROR (push on this device is everyone's, the daily
  digest writes install state through a `require_admin` route), and Telegram has the same
  shape since #193 — connecting the bot is admin, linking your own chat is not. It is
  emitted independently of name/email, is absent on an unattended turn, and is deliberately
  NOT threaded into any tool executor — `require_admin` and `bind_owner_filter` stay the
  enforcement, this only stops Baker walking a member through an admin-only flow.
  (3) `crm_get_setup_status` is one keyless read (`crm/setup_status_service.py`, **no REST
  route, so no route-authz change**) over services that already exist: AI readiness and the
  active provider as an enum, Gmail connected/broken, Telegram configured/linked, the todo
  mode, and custom-field definition counts per entity type. Booleans, enums and integers
  only — no free text, no secrets, and deliberately no `active_model`, mailbox address or
  bot username, because a read tool is on the unattended on-ramp and an all-structured
  payload is the ideal case there; `test_crm_setup_status.py` pins the key set and every
  leaf's type so a later field cannot quietly widen it. **Every leaf is nullable and `null`
  means UNKNOWN, never "off"**: each fact is read in its own try/except, so a database
  hiccup cannot have Baker tell someone their Gmail is disconnected and walk them through
  reconnecting it, and the tool description states that contract to the model. **Three
  readers cannot report their own failure and each needed a tell**, which is the part to
  preserve: `gmail.store.get_row`, `providers.credentials.CredentialStore._load` and
  `crm.service.get_todo_mode` all catch every database error and return a plausible value
  ("store reads never raise" is correct for their hot paths — chat must not 500 on a
  hiccup), so NO exception reaches this module and a naive try/except reports an outage as
  a deliberate configuration. Every tell must ride the SAME query as
  the value, never a separate preflight probe — a probe can succeed in the instant before
  the read it was meant to vouch for fails, so it proves nothing (this was built as a probe
  first and corrected). Gmail brings its own (the seeded singleton means an EMPTY row can
  only be a failed read); `CredentialStore` gained a **`load_failed`** flag set in the same
  `except` that returns the empty shape, which every other caller ignores, **and
  `get_ai_provider` gained a keyword-only `store=`** so the readiness answer and the
  provider name come from ONE load — bare, the factory builds a second store whose failure
  that flag cannot see, and the two fields could also straddle a concurrent
  connect/disconnect; and todo mode is
  read through `crm.service.get_crm_meta`, the failure-AWARE reader of that singleton, which
  lets the error propagate — so `setup_status_service` restates the mode-normalization rule
  rather than calling `get_todo_mode`, and a test pins the two against the same stored value
  so the fifth reader of that default cannot drift from the four. The AI one is the worst
  case the nullable payload exists for: without it a dead database invites Baker to walk
  someone through an AI Setup they already completed. It is
  background-callable and that is asserted rather than incidental — the payload is install
  configuration with no record content and nothing about another seat, and a background
  turn's one egress is a `notify_user` over the install's own channels. **Exactly one field
  is per-SEAT rather than per-install** — whether the person Baker is talking to has a
  linked Telegram chat, which #193 moved out of the singleton into `store.get_link(user_id)`
  — and it is the only reason `get_setup_status` takes a `user_id` at all. That id is bound
  server-side in `_identity_executors` and stripped from the model's arguments the way every
  other identity binding is (#190), so a status read can never be turned into a question
  about somebody else's phone; an unattended turn has no seat, so the field is `null` there
  rather than `false`, which would invite the turn to say "go link a chat" about one that
  may already exist. The bot-config half and the seat half are read in SEPARATE
  try/excepts, so a failed per-seat read cannot blank the install-wide fact beside it.
