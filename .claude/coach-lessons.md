# Coach Lessons — CakeCRM

> Instincts extracted from development sessions in this repo. Evidence: `[✓confirmed ✗violated · last-event]`.
> Scan this index; grep the quoted phrase for the full instinct.

## Index

- [Providers] Together `/v1/models` returns a bare array: `httpx` direct, not SDK `models.list()`
- [Testing] per-provider wire-shape test: mock the HTTP `transport`, not the SDK object
- [Testing] multi-SELECT snapshot reads: `REPEATABLE READ` as first statement
- [Testing] monkeypatched factory wrapping the patched symbol: capture the original first
- [Tooling] sibling PR owns the lint config: `ruff --config <its file>`, scoped, never tree-wide `--fix`
- [Auto-Issues] pipeline skills on this repo: pin `origin/main`, no cake_os PR bots

## Providers

- `[✓1 ✗0 · 2026-07-23]` **When** validating or listing a provider's models via the OpenAI SDK's `client.models.list()` against an OpenAI-compatible endpoint → **do** verify the provider's `/v1/models` actually returns OpenAI's `{"data": [...]}` envelope before relying on the SDK → **because** Together AI returns a **bare top-level array**, and `openai==2.30.0`'s `models.list()` raises on it — which silently turned every valid Together key into a 400 at the connect gate. Fix: list/validate such providers via `httpx` directly, accepting bare-array or enveloped (found by review-super on #2/PR #28).

## Testing

- `[✓1 ✗0 · 2026-07-23]` **When** a test suite mocks a provider **SDK object** (returning envelope-shaped fakes) → **do** add at least one test per provider that mocks the HTTP **transport** with the REAL wire shape (e.g. `httpx.MockTransport` returning Together's bare array) → **because** SDK-object mocks can't catch wire-format mismatches or catalog-content bugs (Google returning TTS/image models that name-based tier inference then picks as defaults) — both were caught on #2 only by running the real pinned SDK against a mocked transport.
- `[✓1 ✗0 · 2026-07-23]` **When** a service does two SELECTs in one transaction and treats them as one consistent snapshot → **do** issue `SET TRANSACTION ISOLATION LEVEL REPEATABLE READ` as the FIRST statement of that transaction → **because** Postgres' default READ COMMITTED takes a new snapshot per statement, so the pair can see different states; the transaction-scoped form resets at commit and is safe on a pooled connection, unlike `SET SESSION`, which leaks across checkouts (CredentialStore, #2).
- `[✓1 ✗0 · 2026-07-23]` **When** a monkeypatched factory wraps the very symbol it patches (e.g. `monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: httpx.AsyncClient(...))`) → **do** capture the original into a local first (`real = httpx.AsyncClient`) and call that → **because** the lambda body resolves the patched name → infinite recursion (#2).

## Tooling

- `[✓1 ✗0 · 2026-07-23]` **When** running `ruff` while a sibling PR owns the repo's lint config (`ruff.toml` not yet merged) → **do** run `ruff check --config <that file>` scoped to YOUR files, never `ruff check . --fix` tree-wide → **because** default ruff selects far more rules than the repo config (`F,E,W,I` here), and a tree-wide `--fix` re-sorts imports in files outside your change, creating spurious diffs that conflict with the sibling PR. Revert any out-of-scope files ruff touches (#2 vs #10/PR #27).

## Auto-Issues

- `[✓1 ✗0 · 2026-07-23]` **(Auto-Issues)** **When** running review-super / `/pr` / settle+evidence steps on this repo (`wwilson1017/CakeCRM`, default branch `main`) → **do** pin the diff base to `origin/main` explicitly (pass it as review-super's arg, `gh pr create --base main`) and swap the skills' hardcoded `tncheesecake/cake_os` + `master` in `gh api`/graphql calls → **because** those skills default to a nonexistent `master` (and a stale local `main` balloons three-dot diffs to the whole product shell), and this repo has none of cake_os's PR bots — verbatim runs review an empty/ballooned diff, poll the wrong repo, and return a false "0 threads → clean" (verified on #10/PR #27). See `docs/solutions/workflow-issues/cakecrm-ci-hygiene-and-cakeos-pipeline-adaptation.md`.
