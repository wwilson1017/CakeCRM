---
title: Serving authenticated binary content (images, attachments) with no cookie auth
date: 2026-08-27
category: architecture-patterns
module: frontend/src/crm, backend/crm
tags: [attachments, images, bearer-auth, object-urls, caching, abort]
problem_type: pattern
---

## Context

CakeCRM authenticates with a Bearer JWT in the `Authorization` header — there is no cookie
auth anywhere. That is a repo-level constraint with a consequence that is easy to forget at
design time: **a bare `<img src="/api/...">` (or `<a href>`, `<video src>`) can never
authenticate.** The browser attaches cookies to subresource requests; it never attaches
your `Authorization` header. Established on issue #57 (chatter note attachments, PR #123).

## Guidance

### Fetch with Bearer, render an object URL

Private binary content must arrive via `fetch` with the auth header, then render from
`URL.createObjectURL(blob)`. Wrap this in a hook so consumers never touch the mechanics.

### The object-URL hook must be path-keyed

The hook owns the URL's lifecycle (`revokeObjectURL` on cleanup). When the path prop
changes, a naive hook returns the *previous* path's URL while the new fetch is in flight —
and its own cleanup has already revoked it, so consumers render a dead `blob:` URL.
Return `null` while a new path is in flight; consumers show a placeholder.

### Abandoning the promise does not stop the bytes

Dropping the fetch promise on unmount stops you *using* the response; only an
`AbortSignal` stops the bytes *arriving*. For large originals (a download-only
attachment), wire the abort through, or a closed viewer keeps consuming bandwidth.

### Caching: never `immutable` on id-addressed resources

This repo's CRM reset runs `TRUNCATE ... RESTART IDENTITY`, so numeric ids are reissued.
A year-cached `/attachments/1/file` can be served for a *different* attachment after a
reset, and a fresh `immutable` response is never revalidated — a hard-deleted attachment
stays viewable in that browser. Use `Cache-Control: private, no-cache` + `ETag` +
`Vary: Authorization`: the response is still stored, every reuse revalidates (cheap 304),
and the bandwidth win survives.

## When to Apply

Any endpoint returning user-scoped binary content: attachments, thumbnails, exports,
generated files. The unauthenticated branding logo is the deliberate exception — it is
public by design and served plain.
