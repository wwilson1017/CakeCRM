---
title: Code-splitting a SPA needs an error-containment matrix, not just Suspense
date: 2026-09-04
category: architecture-patterns
module: frontend/src/core/components/ChunkErrorBoundary.tsx
tags: [code-splitting, react-lazy, suspense, error-boundary, deploy-skew, vite, react-router]
problem_type: pattern
---

## Context

Introducing `React.lazy` to an app that had none (CakeCRM #149, PR #155) creates a failure class the
eager bundle did not have: a chunk request that fails at runtime. Every deploy replaces
`frontend/dist` wholesale inside the Docker image — no CDN, no retained old assets — so a tab open
across a deploy holds an `index.html` whose hashed chunk names no longer exist. Verified against the
running server: renaming one emitted chunk makes its URL return a hard 404, and `/assets` is a
`StaticFiles` mount that 404s cleanly rather than falling through to the SPA HTML, so the failure
surfaces as a real import rejection rather than an HTML-parsed-as-JS error.

The instinct is to put a `Suspense` at each split point and stop. That is half the job, and the
missing half is not obvious, because the tests you would naturally write all pass.

## Guidance

**1. `Suspense` catches a PENDING import. It never catches a REJECTED one.**

A rejected `lazy()` promise is re-thrown through the nearest ERROR boundary, walking straight past
every `Suspense` in between. So each split point needs a boundary above it, or the rejection reaches
the root and unmounts the whole tree.

```tsx
// Not enough on its own:
<Suspense fallback={<BootFallback />}><LazyThing /></Suspense>

// The composition that actually contains a failure:
<ChunkErrorBoundary scope="panel">
  <Suspense fallback={<BootFallback variant="panel" />}><LazyThing /></Suspense>
</ChunkErrorBoundary>
```

**2. One boundary policy for the whole app is wrong in both directions.** Two questions decide the
policy at each site, and they are independent:

| | How much of the screen does the failure own? | Is the user BLOCKED by it? | Auto-reload? |
|---|---|---|---|
| `app` (root) | everything | yes | yes, once per tab |
| `route` (around the router `Outlet`) | the content column | yes — they asked for this page | yes, once per tab |
| `panel` (a background-mounted drawer) | the panel | no — it loads with the drawer shut | **never** |

The `panel` row is the one that surprises. A drawer whose chunk is prefetched the moment a feature
flag flips is not something the user is waiting on, so reloading the page to recover it destroys
whatever they were actually typing. Getting this wrong is silent: the panel is off-screen, so the
only symptom is a page that reloads itself for no visible reason.

**3. A contained boundary needs a reset key, or it is a dead end.** React error boundaries never
self-reset, and a `route` boundary lives OUTSIDE the `<Outlet />`, so it stays mounted across every
later navigation. Without a reset, one render bug in one page freezes that column for the rest of
the session while the nav keeps highlighting and the URL keeps changing — strictly worse than the
full-page card it replaced, whose Reload button at least worked.

```tsx
static getDerivedStateFromProps(props: Props, state: State): Partial<State> | null {
  if (props.resetKey === state.resetKey) return null;
  return state.failed
    ? { failed: false, chunk: false, offline: false, resetKey: props.resetKey }
    : { resetKey: props.resetKey };
}
```

Use a **prop**, not `key={location.pathname}` on the boundary. A key change remounts the subtree,
which destroys any "the route element never changes, so its loaded corpus survives open → back"
property the app relies on. A test that counts mounts is what tells the two implementations apart.

**4. Classify the error from a message you can actually read.** Browsers word a chunk failure four
different ways and none of them give it a code, so message matching is all there is:
`dynamically imported module` (Chrome/Edge), `error loading dynamically` (Firefox),
`Importing a module script failed` (Safari), plus Vite's own `Unable to preload CSS` — which becomes
reachable the moment a lazy chunk carries its own stylesheet. Read `.message` off a non-`Error`
rejection too: a promise can reject with anything, and `String({message: '…'})` is `"[object Object]"`,
which matches nothing and silently reclassifies a chunk failure as a render bug.

**5. Auto-reload is a state-destroying action; gate it on more than the error kind.** The same four
messages cover an offline transition, a captive portal and a proxy fault. Reloading offline trades a
page the user can still read for the browser's offline screen, from which the app cannot recover
itself — so check `navigator.onLine === false` and decline. Capture that flag in
`getDerivedStateFromError` as well, or the card tells the offline user the app was updated and
reloading will fix it, steering them into exactly the action the guard just declined.

**6. Guard the reload once per tab SESSION, never on a time window.** A chunk that is genuinely gone
fails again after every reload, so any expiring guard becomes a permanent reload cycle. Write the
`sessionStorage` key and then READ IT BACK: when storage is blocked the write is silently lost, and a
guard that cannot be stored cannot stop a loop — unavailable storage must mean "show the button".

## Why This Matters

Each of these was a live defect found by review on one PR, not a hypothetical. The assistant-drawer
case (2) and the route-chunk case (2 again, at a different site) each replaced the entire authenticated
app — nav, toast host, an open confirm dialog — over a single failed request. The reset-key case (3)
was introduced *by the fix for* the route case and caught only on a verification turn. None of them
is visible in a normal test run, because the code path only exists when a chunk request fails, which
never happens in a test or in local development where `dist` is never replaced underneath a live tab.

## When to Apply

Whenever you add the first `React.lazy` to an app, or add a split point in a place the user is not
actively waiting on. Two specific triggers:

- **A lazy component that mounts on a flag rather than on a click** — prefetched drawers, panels
  behind a closed disclosure, anything that loads "in the background so it's ready". That is the
  `panel` row, and the one where auto-reload is actively harmful.
- **A boundary placed outside the thing it protects** — around an `Outlet`, a slot, a portal host.
  That is where the reset key becomes mandatory rather than nice to have.

Verify the whole thing the only way it can be verified: rename an emitted chunk on disk, load the
app, navigate to that route, and watch what happens — then navigate somewhere else and confirm the
app recovered rather than staying on the card.
