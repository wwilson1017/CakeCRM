# Frontend boot split

> Topic doc split out of `AGENTS.md` (former Product Rules). `AGENTS.md` keeps the enforceable
> invariants; this file is the full implementation record, moved verbatim. Add new
> implementation notes ("landed #N as …", design reasoning, divergences) HERE, not in
> `AGENTS.md`. Phrases like "the CRM bullet above" or "see the X bullet" refer to the
> rule bullets this file opens with, or to the topic doc that holds that rule; find
> other areas' docs through the Topic docs table in `AGENTS.md`.

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
  handing the phone ten. `TodosModeRouter` keeps `TodosPage` lazy inside it, because
  `TodosPage` drags the #73 collection layer and `@dnd-kit`, which Contacts/Companies/Todos
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
  upstream did not need, because our layout is a nested route and the todo-mode flip
  (`null → 'gtd'`) is a plain `setState` in a `.then`, **not** a router transition, so without
  it the whole shell would swap to a spinner while a todo chunk loads. That last boundary has
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
  content hash happened to contain the letters `dnd`. **#234 adopted the collection layer on
  the todo surface and retired both guards' deny entries for it**, deliberately: Someday, Done
  and Projects render through `CollectionView`, which reaches `@dnd-kit` through `KanbanView`.
  Both guards now pin `shared/collection/CollectionView.tsx` as a POSITIVE sentinel instead, and
  the source guard pins the package set (`@dnd-kit/{core,sortable,utilities}` beside `react` and
  `react-router-dom`), so the cost stays a recorded fact rather than drifting. Measured on the
  built `/todo` cold load (entry chunk + `PublicTodoApp` + every static import, JS only): **320.6
  → 417.0 kB raw, 101.4 → 132.2 kB gzip** (+96.4 kB / +30.8 kB). The entry chunk every visitor
  downloads is unchanged at 188.7 kB. Lazy-loading `KanbanView` inside the layer would win most
  of that back for every non-board surface, and is a shared-layer change for its own issue.
  **The `/todo` budget was re-based on 2026-10-04 (#278)** because that growth was recorded but
  the guard's ceiling was not moved with it: `bootSplitBuild.test.ts` capped the `/todo` cold load
  at 431,000 bytes (~421 kB, 312.3 kB measured at #149 plus ~35% headroom), #249 landed at 417 kB
  under it by chance, #275's phone bottom bar added 2.5 kB, and the two remaining GTD PRs (#276
  weekly review, #277 bring-back date) measured 421.2 and 422.1 kB and failed CI by about one
  kilobyte each. The budget is now 583,000 bytes — the measured 422.1 kB with the same ~35%
  headroom — and the comment beside it names which PRs consumed the old margin. **The rule this
  makes explicit:** a PR that deliberately grows a guarded chunk records the measured delta AND
  moves the budget line in the same change, so the next unrelated PR is never the one that trips
  it. The entry-chunk and CRM-shell budgets were not touched and keep their original margins.
