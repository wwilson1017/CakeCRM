---
title: A Copy button that works on a plain-http LAN install
date: 2026-09-04
category: design-patterns
module: frontend/src/shared/hooks/useCopyToClipboard.ts
tags: [clipboard, execCommand, non-secure-context, accessibility, ios, gtd, todo-web]
problem_type: pattern
---

## Context

`navigator.clipboard` is **undefined**, not merely restricted, in a non-secure context. So any
Copy affordance on a surface served over plain http on a LAN — CakeCRM's no-login
`/todo/{token}` app, or any dev box reached by IP — is a dead control. Both blueprint repos
accept this and have their hook `console.warn`, because both are HTTPS in production and
localhost in dev. CakeCRM's public todo surface is neither (#151, PR #152).

## Guidance

Three things, all load-bearing.

**1. Probe the async API with a synchronous optional chain, not a `try`/`await`.**

```ts
if (navigator.clipboard?.writeText) {
  try { await navigator.clipboard.writeText(text); return true; }
  catch (err) { console.warn('…trying the legacy path', err); }
}
return legacyCopy(text);
```

In the non-secure case the check short-circuits with no `await`, so `legacyCopy` runs in the
same tick as the click — while the user gesture `execCommand` requires is still live. Awaiting
a rejection first can spend the gesture.

**2. `focus()` before `select()`.** `select()` does not move focus, and `execCommand('copy')`
copies from the FOCUSED element. Without the focus call WebKit copies whatever the user was
last in. Restore the previous `activeElement` in a `finally`.

**3. Stage the textarea off-screen HORIZONTALLY at `top: 0`, with a 16px font.** A large
negative `top` makes WebKit scroll the page to reach the element when it takes focus, so
tapping Copy on a phone jumps to the top of the list. iOS zooms into any focused field under
16px.

## Feedback contract

Return a boolean and expose ONE `'idle' | 'copied' | 'failed'` status rather than a `copied`
flag beside a `failed` flag — the two are mutually exclusive and share a reset timer. Reset the
status at the START of every attempt: otherwise a success followed by a failed retry inside the
reset window leaves the previous attempt's green "Copied" standing as confirmation of the
attempt that just failed. Guard the write with an attempt counter, since two copies can be in
flight and resolution order is not click order, and with a mounted ref, since a promise
resolving after unmount otherwise schedules a timer after the cleanup meant to end them.

Accessibility: a fixed `aria-label` does not change when the visible text does, so lead it with
the visible word once there is an outcome (WCAG 2.5.3 Label in Name — a voice-control user
saying "Copied" must match something), AND keep a `role="status"` region, because screen
readers vary in whether they re-read a changed name on the focused element.

## Testing trap

The mock must read what the REAL API reads. Three tests covered this path and all three passed
against a version that never called `focus()`, because the mock did
`document.querySelector('textarea[readonly]')?.value`. Reading `document.activeElement` instead
made all three go red:

```js
const active = document.activeElement;
execCopied.push(active?.tagName === 'TEXTAREA' ? active.value : '');
```

The tell is a mock that reaches for a node by selector when the real API never takes one. The
same class covers `getSelection`, `activeElement` and drag data transfer.

## When to Apply

Any Copy/Cut affordance on a surface that can be reached over plain http — which in this repo
means every no-login surface (`/capture`, `/todo/{token}`) and any page a developer opens by
LAN IP. Not needed for surfaces only ever reached over HTTPS or localhost.
