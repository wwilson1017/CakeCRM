---
title: Safely coexisting a background "silent refresh" with an optimistic-update UI
date: 2026-07-27
category: design-patterns
module: frontend/src/crm/PipelinePage.tsx
tags: [react, optimistic-ui, race-condition, aba, kanban, drag-and-drop, bulk]
problem_type: pattern
---

## Context

The CRM pipeline board (issue #21) filters by "deal activity" recency, whose value
(`last_activity_at`) is a server-derived field the client can't compute. A note logged in the
deal detail sheet must move the deal out of the "no activity" bucket — so on sheet close the
board does a **silent refresh** (a full `GET /api/crm/deals` with no loading spinner) and
re-derives everything from the server. But the board also has an **optimistic drag** (stage
moves applied to local `data` immediately, reconciled/reverted by a background PUT). A naive
silent refresh races those in-flight PUTs.

## Guidance

Two independent guards, plus a defer-retry, let a background refresh coexist with optimistic
writes without ever clobbering a succeeded move:

1. **Never trust a pending-COUNT alone — use a write GENERATION captured at the GET's start.**
   A count of in-flight writes cannot see an ABA: a write that STARTS and SETTLES entirely
   inside the GET's flight returns the counter to its original value, so at resolve time
   `pendingWrites === 0` looks safe while the GET's payload is actually pre-commit and stale.
   Bump a monotonic `writeGen` whenever a write starts; snapshot it when the GET fires; drop the
   response if `writeGen` changed OR a write is still pending:

   ```ts
   const pendingWrites = useRef(0);   // ++ on optimistic write, -- in the write's finally
   const writeGen = useRef(0);        // ++ on optimistic write (start)
   const load = useCallback(async (silent = false) => {
     const isSilent = silent === true;                       // guard truthy non-boolean args
     if (isSilent && pendingWrites.current > 0) { pendingRefresh.current = true; return; }
     const startGen = writeGen.current;
     const d = await api('/api/crm/deals');
     if (isSilent && (pendingWrites.current > 0 || writeGen.current !== startGen)) {
       pendingRefresh.current = true;                         // a write raced this GET → defer
       return;
     }
     setData(d);
   }, []);
   ```

2. **DEFER + RETRY, don't drop.** Dropping the refusal silently loses the update the refresh
   existed for. Set a `pendingRefresh` flag when deferring, and re-fire the refresh from the
   write's `finally` once the LAST write settles:

   ```ts
   } finally {
     pendingWrites.current--;
     if (pendingWrites.current === 0 && pendingRefresh.current) {
       pendingRefresh.current = false;
       load(true);
     }
   }
   ```

3. **Route EVERY dismissal path through the refresh.** The sheet's Close button used `onClose`,
   but "Mark Won/Lost" used a separate `onStageChange` that bypassed it — so a note logged before
   Marking Won left the field stale. Both paths must call `load(true)`.

4. **`data` stays the single source of truth.** The refresh re-derives the whole board from the
   server; it is NOT a second optimistic layer (which is the classic optimistic-DnD trap).

## Why This Matters

An ABA race is invisible to the obvious safety check. On #21 the author's own verifier PASSED a
`pendingWrites` counter-balance check, and two prior review passes (4 Sonnet personas + fable ×3
turns) missed it — a Codex pass found a drag PUT that started and settled inside the GET's flight,
silently reverting a succeeded move with no self-heal until a manual reload. The
generation-at-GET-start is what a count structurally cannot provide.

## When to Apply

Any surface that pairs optimistic local mutations with a background refetch that overwrites the
same state (kanban boards, inline-editable lists, anything with an "auto-refresh after action").
The default remains "no follow-up GET after an optimistic mutation" — reach for this pattern only
when a background refresh is genuinely required (a derived server field the optimistic path can't
compute). Prefer a targeted single-record patch when feasible; use the full-board guarded refresh
when the derivation spans the whole payload.

## Addendum — a SET write racing the same board's single-record writes (#55, audited #99)

The bulk stage-move (#55) added a second writer to the same board, and reviewers have twice read
`moveDealStage`'s guard — `if (bulkPendingRef.current) return;` — as the *only* protection and
concluded that a selected, optimistically-dragged card can race the bulk request. **It cannot, and
the reason is a different mechanism one function over.** `applyBulkMove` orders four steps, and the
order is the whole guarantee:

1. **Take the lock BEFORE the first `await`.** `bulkPendingRef.current = true` runs in the same
   synchronous block as the click, so from that instant `moveDealStage` refuses to start any *new*
   single-deal write.
2. **Drain the chains for exactly the ids being moved**, which handles the writes already in flight
   — the ones the lock is too late for:
   ```ts
   await Promise.allSettled(
     ids.map(id => dealWriteChain.current.get(id)).filter(Boolean) as Promise<void>[],
   );
   ```
   This is the per-deal write-chain from the drag path (see guard 1 above), extended across a set.
3. **Only then** snapshot `prevStages`, paint optimistically, and POST.
4. **Reconcile while still holding the lock**, releasing it in `finally`.

Steps 1 and 2 are complementary, not redundant: the lock alone cannot stop a PUT already on the
wire, and the drain alone cannot stop a drag that starts mid-`await`. Removing either reopens the
race.

**Why it is load-bearing.** The server has no CAS on `deals.stage` — `bulk_move_deals` and
`_write_deal_update` both take a plain `FOR UPDATE` and last writer wins — so this client-side
ordering is the *only* thing sequencing two writes to one deal. It is also, as of this audit,
untested; a rewrite that "simplifies away" the `allSettled` would pass CI. Pin it with a test when
a co-located `PipelinePage.test.tsx` next lands.

**The boundary — state it whenever you cite this.** All of the above is *per mounted component*:
the refs are component-local. Two browser tabs, two users, or any other client still race each
other, and the server will silently take whichever write commits last. That is unchanged by #55
and #99, and it is a different problem (it needs a version column, not a client-side chain).

**Verification recipe** — checked at main `486fe7d` and at PR #109's rewrite of the same file
(`feature/issue-74-pipeline-parity-board-list-views`, tip `bd44a2f`), where the drain survived
byte-identical:
```bash
git show <ref>:frontend/src/crm/PipelinePage.tsx | grep -n 'bulkPendingRef.current = true\|allSettled\|dealWriteChain'
```
The lock line must appear *before* the `allSettled` line, and that must appear before the POST.
