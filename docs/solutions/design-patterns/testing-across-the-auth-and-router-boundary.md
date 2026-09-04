---
title: Component tests are blind to the auth and router boundary
date: 2026-09-04
category: design-patterns
module: frontend/src/crm/PipelinePage.tsx
tags: [testing, react-router, memoryrouter, sessionStorage, deep-links, noopener]
problem_type: pattern
---

## Context

Every `PipelinePage` test renders the component directly under `MemoryRouter`. That is correct
for the component and means the whole suite is blind to everything between the URL and the
component: `ProtectedRoute`, the token store, the login redirect. A P1 lived in that gap on
CakeCRM #145 — assistant links opened in a `noopener` tab, which does **not** inherit
`sessionStorage`, so the token was absent and the deep link was discarded through `/login` →
`/crm`.

Worth remembering whenever a feature's value depends on arriving from OUTSIDE the app: a link
in a message, a push notification, a bookmark. The component test proves the component and says
nothing about whether the user ever reaches it.

## Guidance

**1. `MemoryRouter` never touches `window.location`.** A test asserting
`new URL(window.location.href).searchParams` under `MemoryRouter` passes no matter what the
page does — the router keeps its history in memory. Assert the router's own location through a
probe component that calls `useLocation()`, writing the value in an effect since this repo
forbids writing to an outer binding during render.

**2. Know what `location.key` means before keying behaviour off it.**

- push and replace mint a FRESH key, so an internal `setSearchParams(…, { replace: true })` is
  a navigation and re-triggers anything keyed on the key;
- Back/Forward RESTORE the entry's original key, so returning to a URL you have visited does
  NOT look like a new navigation.

Both matter for "did the user follow this link again?". The second needs an explicit reset when
the parameter goes away, or reload reopens a record but Back does not.

**3. Cover the boundary itself once.** One test that mounts the real route tree — provider,
`ProtectedRoute`, the token store — with the token absent is enough to pin that an external
arrival reaches the page rather than the login redirect. It does not replace the component
tests; it covers what they structurally cannot see.

## When to Apply

Any feature whose entry point is a URL produced elsewhere — deep links from the assistant, from
Telegram, from a notification, or a shared bookmark. Also whenever a test's assertion reads a
global the router does not write.
