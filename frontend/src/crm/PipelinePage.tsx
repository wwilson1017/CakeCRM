import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import type { PointerEvent as ReactPointerEvent, MouseEvent as ReactMouseEvent, KeyboardEvent as ReactKeyboardEvent } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api, ApiError } from '../core/api/client';
import type { CrmDeal } from '../core/types';
import { DealForm } from './components/DealForm';
import { DealDetailBody, type DealPatch } from './components/DealDetailBody';
import { CollectionDetail, denyEscapeBackdrop } from '../shared/collection';
import { boardNavOrder } from './boardNavOrder';
import { DEAL_DETAIL_CONFIG } from './dealDetailConfig';
import { DEAL_PARAM, parseDealParam } from './dealDeepLink';
import { ScorePill, TouchCountPill } from './components/badges';
import { STAGE_COLORS, STAGE_ORDER, OPEN_STAGES } from './constants';
import { IconPlus } from '../shared/icons';
import { useIsMobile } from '../shared/useIsMobile';
import { LoadError } from '../shared/LoadError';
import { toast } from '../shared/toast';
import {
  INK, INK_MUTE, INK_DIM, LINE, BG_CARD, ACCENT,
  FONT_DISPLAY, mono, formatNumber, inputStyle, tint,
} from '../shared/styles';
import { pageHeading, btnPrimary, btnSecondary, btnSmall, stageCard } from './styles';
import { KanbanBoard, type MoveEvent } from '../shared/dnd';
import PipelineFilterBar from './components/PipelineFilterBar';
import {
  type PipelineFilterState, type AdvancedFilters,
  EMPTY_FILTER_STATE, dealMatchesAdvanced, hasAdvanced, loadFilterState, saveFilterState,
} from './pipelineFilters';
import { applicableBulkIds } from './bulkSelection';
import { classifyBulkMove, describeBulkMove, type BulkMoveResponse, type BulkNotice } from './bulkOutcome';

// The /api/crm/deals payload also carries server-computed `stage_summary` and
// `total_pipeline_value`, but the board derives every total client-side from
// `deals` so they stay correct under optimistic moves — we intentionally read
// only `deals` here rather than trust aggregates the optimistic path can't update.
interface PipelineData {
  deals: CrmDeal[];
}

/**
 * The refusal `writeDeal` raises while a bulk move owns the board — its own type, not a bare
 * `Error`, so a caller can tell "we never sent this" apart from "the server rejected it".
 *
 * That distinction is what keeps the toasts honest. `writeDeal` already reports a failed stage
 * PUT itself, so a caller that reported every rejection would double-toast a genuine server
 * failure; one that reported none would let a bulk-lock refusal — which `writeDeal` cannot
 * toast, since it rejects before doing any work — vanish silently.
 */
class BulkLockError extends Error {
  constructor() {
    super('A bulk update is in progress — try again in a moment.');
    this.name = 'BulkLockError';
  }
}

// The `CollectionDetail` host config is shared with `CrmDashboardPage` — see `dealDetailConfig.ts`
// for why (both hosts open deals into the same shell, and `loadById` is what resolves an
// off-board or archived deal for a shared `?deal=` link; its board-position writes are then
// hidden here by `onBoard`).

export function PipelinePage() {
  const [data, setData] = useState<PipelineData | null>(null);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  // The open deal is an ID, not a row: the panel must keep resolving against the CURRENT board
  // array (so a save or a silent refresh reaches it), and an id is also what a deep link carries.
  const [selectedDealId, setSelectedDealId] = useState<number | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const isMobile = useIsMobile();

  // Client-side facet filtering (issue #21). One envelope (search + advanced facets)
  // restored from / persisted to sessionStorage so a reload keeps the view, but it
  // never leaves the browser — filtering is a pure predicate over the already-loaded
  // board, no backend query params. Held as ONE object so restore/persist/clear-all
  // are single-path.
  const [filters, setFilters] = useState<PipelineFilterState>(loadFilterState);
  const { search, advanced } = filters;
  useEffect(() => { saveFilterState(filters); }, [filters]);
  const setSearch = useCallback((s: string) => setFilters(f => ({ ...f, search: s })), []);
  const setAdvanced = useCallback((a: AdvancedFilters) => setFilters(f => ({ ...f, advanced: a })), []);

  // Dashboard deep-link (?stage=X) — an explicit "show me this column" intent that overrides
  // restored session filters ENTIRELY (any restored facet could hide the target column or
  // match zero deals → the empty state, no columns, scroll no-ops). Handled REACTIVELY via
  // React's render-time "reset state when an input changes" pattern (a state compare, NOT an
  // effect — so no cascading setState-in-effect), so it fires whether the page just mounted OR
  // was already mounted when the search param changed. `seenDeepLink` starts null so a mount
  // with ?stage=X triggers the reset; an unknown stage is ignored (matches the scroll guard).
  const deepLinkStage = searchParams.get('stage');
  const validDeepLink = deepLinkStage && STAGE_ORDER.includes(deepLinkStage) ? deepLinkStage : null;
  const [seenDeepLink, setSeenDeepLink] = useState<string | null>(null);
  if (validDeepLink !== seenDeepLink) {
    setSeenDeepLink(validDeepLink);
    if (validDeepLink) setFilters(EMPTY_FILTER_STATE);
  }

  // Deal deep-link (?deal=N) — the other half of the Copy-link contract in `dealDeepLink.ts`.
  // Consumed the same reactive way as ?stage= above (a state compare, never an effect) so it
  // fires on mount AND when an already-mounted page's params change. The parameter itself is
  // stripped in the effect below; from here on the id lives in state, which is exactly why the
  // address bar is not a copy source.
  const deepLinkDeal = parseDealParam(searchParams.get(DEAL_PARAM));
  const [seenDeepLinkDeal, setSeenDeepLinkDeal] = useState<number | null>(null);
  if (deepLinkDeal !== seenDeepLinkDeal) {
    setSeenDeepLinkDeal(deepLinkDeal);
    if (deepLinkDeal !== null) setSelectedDealId(deepLinkDeal);
  }

  const columnRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  // Last stage the deep-link effect scrolled to — re-fires per NEW target, once each.
  const scrolledStage = useRef<string | null>(null);
  // Per-deal operation counter so out-of-order responses from rapid moves of the
  // SAME deal can't clobber each other — only the latest op reconciles/reverts.
  const dealOpSeq = useRef<Map<number, { seq: number; hasStage: boolean }>>(new Map());
  // Per-deal write chain: each deal's PUT is queued behind its prior in-flight
  // write so the SERVER applies moves in the user's action order (ending at the
  // latest intent), never racing two concurrent writes for the same deal.
  const dealWriteChain = useRef<Map<number, Promise<void>>>(new Map());
  // Per-deal last server-CONFIRMED stage — the ground truth a failed move reverts
  // to. Seeded from each load and advanced on every successful PUT (even when the
  // display reconcile is superseded), so a rolled-back move restores the real
  // server stage rather than an optimistic intermediate that itself never persisted.
  const dealConfirmedStage = useRef<Map<number, string>>(new Map());
  // Optimistic-write bookkeeping for the silent refresh (see `load`): a count of writes
  // still IN FLIGHT, plus a monotonic generation bumped whenever a write STARTS. Together
  // they let a silent GET detect a drag PUT that overlapped its flight — one that started
  // before it (pending>0) OR started-and-settled during it (generation changed) — and drop
  // its now-stale payload rather than reverting a move that actually succeeded.
  const pendingWrites = useRef(0);
  const writeGen = useRef(0);
  // A silent refresh that couldn't run safely (a stage write was racing it) is DEFERRED, not
  // dropped: moveDealStage re-fires it once the last write settles, so activity/derived fields
  // still update after a sheet dismissal even when a drag PUT overlapped the refresh.
  const pendingRefresh = useRef(false);

  // Bulk stage moves (issue #55). Selection is a plain Set of deal ids; `bulkPending` has a
  // ref twin because the mutators read it SYNCHRONOUSLY to bail out, and state wouldn't have
  // updated yet. The lock is held from the click until the reconcile refetch settles — that
  // is what stops a drag or a silent refresh from racing the server truth we're about to
  // fetch. `bulkNotice` is the one message that must outlive a toast (see bulkOutcome.ts).
  const [bulkSelected, setBulkSelected] = useState<Set<number>>(() => new Set());
  const [bulkPending, setBulkPending] = useState(false);
  const bulkPendingRef = useRef(false);
  const [bulkNotice, setBulkNotice] = useState<BulkNotice | null>(null);
  const [bulkStage, setBulkStage] = useState('');

  // `silent` refetches without the loading spinner — used to refresh the board after the
  // detail sheet closes, so a deal touched in-sheet (a logged note/activity) leaves the
  // "no activity" bucket without flashing the whole board. `data` stays the single source
  // of truth (issue #12): this re-derives everything from the server, no second optimistic layer.
  // Returns whether fresh server data was actually APPLIED — the bulk flow needs that fact
  // to word an "outcome unknown" notice honestly (a board that couldn't refresh may still be
  // showing the optimistic result). Existing callers ignore the value.
  const load = useCallback(async (silent = false): Promise<boolean> => {
    // `=== true` guards against a truthy non-boolean arg (e.g. a bare `onClick={load}`
    // handing in a MouseEvent) accidentally forcing silent mode.
    const isSilent = silent === true;
    // A silent refresh must not clobber an optimistic drag. If a stage write is already in
    // flight, don't even fire the GET — defer it (moveDealStage re-fires when writes settle).
    if (isSilent && pendingWrites.current > 0) { pendingRefresh.current = true; return false; }
    const startGen = writeGen.current;
    if (!isSilent) setLoading(true);
    try {
      const d = await api<PipelineData>('/api/crm/deals');
      // A write that STARTED during this GET's flight (generation changed) may have made the
      // payload stale — defer+retry rather than clobber a succeeded move OR lose the refresh.
      if (isSilent && (pendingWrites.current > 0 || writeGen.current !== startGen)) {
        pendingRefresh.current = true;
        return false;
      }
      setData(d);
      dealConfirmedStage.current = new Map(d.deals.map(deal => [deal.id, deal.stage]));
      return true;
    } catch { /* data stays null → LoadError below (silent: keep the current board) */ }
    finally { if (!isSilent) setLoading(false); }
    return false;
  }, []);

  useEffect(() => { queueMicrotask(load); }, [load]);

  // `data` is the single source of truth for the board. A stage change is applied
  // to it optimistically — the deal is re-staged IN PLACE (its list position is
  // untouched, so a failed move needs no position bookkeeping) — so the card,
  // column counts, and totals all move together at drop time. Persistence runs in
  // the background: on success we reconcile with the canonical PUT response (new
  // updated_at, joined names); on failure we revert this deal's stage to its last
  // server-confirmed value and toast. The ported Kanban hook mirrors `data` between
  // gestures, so it re-syncs to whichever branch wins — the only extra state is a
  // per-deal last-confirmed stage (server truth, not a board snapshot), and a
  // concurrent change made elsewhere (detail sheet, new deal) is never clobbered.
  //
  // Rapid moves of the SAME deal are made safe three ways: (1) the PUTs are chained
  // per deal so the server applies them in action order; (2) a per-deal op sequence
  // means only the latest op reconciles/reverts the client (no intermediate flicker
  // from an earlier op's response); (3) a failed move reverts to the deal's last
  // server-CONFIRMED stage (seeded on load, advanced on every successful PUT), not
  // its optimistic fromStage — so even if two chained writes for one deal BOTH fail,
  // the board rolls back to the true server stage instead of an intermediate stage
  // that never persisted.
  //
  // The detail panel's inline Save comes through here too, carrying its changed columns in the
  // SAME patch as any stage change — one PUT, because the server settles `probability` to 100/0
  // inside the transaction that moves the stage, so a stage write and a probability write split
  // across two requests is a race whose loser silently wins. What a fields-only write does NOT do
  // is therefore conditional on the patch carrying a stage — it paints nothing, records no
  // confirmed stage, and announces no failed move — while still taking a sequence number and
  // riding this deal's chain, so it is ordered against a concurrent drag rather than racing it.
  // The REVERT is the one step that is unconditional; see the catch block for why.
  const writeDeal = useCallback((deal: CrmDeal, patch: DealPatch, fromStage?: string): Promise<void> => {
    // A bulk move in flight owns the board until its reconcile refetch lands. A single-deal
    // write started now could reconcile (or roll back) against the stage the bulk request is
    // in the middle of changing, clobbering server truth we're about to fetch. It REJECTS rather
    // than returning quietly: dropping a redundant drag is invisible and fine, but dropping the
    // field edit someone just typed is not — the caller surfaces it and keeps the form open.
    if (bulkPendingRef.current) {
      return Promise.reject(new BulkLockError());
    }
    const dealId = deal.id;
    const toStage = patch.stage;
    const seq = (dealOpSeq.current.get(dealId)?.seq ?? 0) + 1;
    dealOpSeq.current.set(dealId, { seq, hasStage: toStage !== undefined });
    // Optimistic: restage the CURRENT record (not the captured drag snapshot, which
    // could be missing fields edited meanwhile), keeping its list position. Only the stage is
    // painted ahead of the server — a field edit has no drag gesture to keep up with, and its
    // response reconciles the row a moment later anyway.
    if (toStage !== undefined) {
      setData(prev => prev ? {
        ...prev,
        deals: prev.deals.map(d => d.id === dealId ? { ...d, stage: toStage } : d),
      } : prev);
    }
    pendingWrites.current++; // an unconfirmed optimistic write now exists (see `load`'s silent guard)
    writeGen.current++;      // ...and bump the generation so a silent GET spanning it is invalidated
    const prior = dealWriteChain.current.get(dealId) ?? Promise.resolve();
    let settle: () => void = () => {};
    let fail: (err: unknown) => void = () => {};
    const outcome = new Promise<void>((resolve, reject) => { settle = resolve; fail = reject; });
    const run = prior.then(async () => {
      try {
        const updated = await api<CrmDeal>(`/api/crm/deals/${dealId}`, {
          method: 'PUT', body: JSON.stringify(patch),
        });
        // Record server truth for THIS write regardless of supersession — a later
        // failed move in the same chain reverts to a real confirmed stage, not an
        // optimistic intermediate. Use the response's stage, not toStage, so the
        // ground truth is whatever the server actually stored.
        dealConfirmedStage.current.set(dealId, updated.stage);
        settle();
        if (dealOpSeq.current.get(dealId)?.seq !== seq) return; // a newer move superseded this one
        setData(prev => prev ? {
          ...prev,
          deals: prev.deals.map(d => d.id === dealId ? { ...d, ...updated } : d),
        } : prev);
      } catch (err) {
        // The caller always learns the outcome, superseded or not — the detail form has to keep
        // the user's draft on screen either way, and only the drag path can afford to shrug.
        fail(err);
        const latest = dealOpSeq.current.get(dealId);
        if (latest?.seq !== seq) {
          // Superseded — leave the newer state, which will reconcile the board itself. But say so
          // if this op wrote a STAGE and the op replacing it does not: "leave the newer state" was
          // written when only another stage write could supersede one, and a newer stage intent
          // genuinely subsumes an older one. A fields-only save expresses no stage intent, so when
          // it succeeds its response quietly puts the card back where it started — the rep's drag
          // undone with nothing on screen to say it failed. The revert is still the superseder's
          // job; only the report is ours.
          if (toStage !== undefined && latest && !latest.hasStage) {
            console.error('Failed to write deal:', err);
            toast.error('Failed to move deal.');
          }
          return;
        }
        console.error('Failed to write deal:', err);
        // THE LAST WRITER TO FAIL OWNS THE RECONCILIATION, whatever it happened to be writing.
        // A superseded stage write returns above without reverting (correctly — the op that
        // replaced it owns the board now), so the optimistic stage it left behind is cleaned up by
        // whichever op for this deal ends up being the latest — and since the detail form shares
        // this chain, that op may well be a fields-only save carrying no stage of its own. Gating
        // the revert on `toStage` therefore stranded a stage nobody stored: drag to won (seq 1),
        // save a field (seq 2), both fail, board keeps showing won until the next full load.
        //
        // So restore from server truth unconditionally. Where no optimistic paint is outstanding
        // this writes back the value already on screen (a harmless no-op); where one IS, this is
        // exactly the repair. Server-confirmed rather than this op's `fromStage`, because an
        // earlier failed write in the chain makes `fromStage` an intermediate that never
        // persisted; `?? fromStage` covers a deal with no confirmed entry yet (a first move,
        // where fromStage IS the confirmed stage).
        const confirmed = dealConfirmedStage.current.get(dealId) ?? fromStage ?? deal.stage;
        setData(prev => prev ? {
          ...prev,
          deals: prev.deals.map(d => d.id === dealId ? { ...d, stage: confirmed } : d),
        } : prev);
        // The TOAST stays stage-only, unlike the revert. A fields-only write announced no move,
        // so announcing a failed one would report a board change nobody asked for — its form
        // shows the error inline, next to the draft it is asking the user to retry. (A superseded
        // stage write is reported by the branch above instead, on its own terms.)
        if (toStage !== undefined) toast.error('Failed to move deal.');
      } finally {
        pendingWrites.current--; // write settled (reconciled or reverted)
        // Once ALL writes have settled, fire any silent refresh that was deferred while a
        // write was racing it — so a sheet dismissal (Close OR Mark Won/Lost) still lands the
        // fresh last_activity_at even though the stage PUT was in flight at dismissal time.
        if (pendingWrites.current === 0 && pendingRefresh.current) {
          pendingRefresh.current = false;
          load(true);
        }
      }
    });
    dealWriteChain.current.set(dealId, run);
    return outcome;
  }, [load]);

  // Drag handler. Resolves immediately so the Kanban hook ends its gesture and
  // re-syncs from `data` right away; persistence + rollback are data-driven (via
  // writeDeal), never snapshot-driven, so this never needs to throw.
  const handleKanbanMove = useCallback((event: MoveEvent<CrmDeal>): Promise<void> => {
    const from = String(event.fromColumnId);
    const to = String(event.toColumnId);
    if (from === to) {
      // Same-column drop persists nothing (deals carry no rank column). Bump the
      // deals array reference so the hook re-syncs and drops the transient reorder
      // rather than leaving an unsaved arrangement that jumps back on next load.
      setData(prev => prev ? { ...prev, deals: prev.deals.slice() } : prev);
      return Promise.resolve();
    }
    // The gesture is over either way — a failed move reverts the board from `data`, and a bulk
    // lock rejection is the "drag ignored while bulk is in flight" case that was always silent.
    void writeDeal(event.item, { stage: to }, from).catch(() => {});
    return Promise.resolve();
  }, [writeDeal]);

  // Detail-panel handler (Mark Won / Lost) — optimistic move, then close the panel ON SUCCESS
  // ONLY. Closing is this page's way of saying "that deal is closed now", so it has to wait for
  // the write: `writeDeal` REJECTS outright under the bulk lock, before any toast of its own, and
  // a fire-and-forget close there dismissed the panel as though the deal had been closed when
  // nothing was even sent. On rejection the panel stays open with the reason on screen, which is
  // also the honest answer for a server refusal (an archived deal cannot change stage).
  // The refresh on the way out is the close path's, so a note/activity logged in the panel before
  // this dismissal lands its last_activity_at; it defers until the stage PUT settles.
  const updateDealStage = useCallback(async (deal: CrmDeal, stage: string) => {
    if (deal.stage !== stage) {
      try {
        await writeDeal(deal, { stage }, deal.stage);
      } catch (err) {
        // Report ONLY the refusal `writeDeal` cannot report itself. A stage PUT that reached the
        // server and failed has already raised its own toast in there; toasting again here would
        // say the same thing twice.
        if (err instanceof BulkLockError) toast.error(err.message);
        return;
      }
    }
    setSelectedDealId(null);
    load(true);
  }, [writeDeal, load]);

  // `void` rather than returning the promise: the body's `onMarkWon`/`onMarkLost` are `void`
  // callbacks it fires without awaiting — the host owns its own error reporting, above.
  const markWon = useCallback((deal: CrmDeal) => { void updateDealStage(deal, 'won'); }, [updateDealStage]);
  const markLost = useCallback((deal: CrmDeal) => { void updateDealStage(deal, 'lost'); }, [updateDealStage]);
  // The inline form's ONE save. The patch may carry `stage`; `writeDeal` handles that itself.
  const saveDeal = useCallback(
    (deal: CrmDeal, patch: DealPatch) => writeDeal(deal, patch, deal.stage),
    [writeDeal],
  );

  const deals = useMemo(() => data?.deals ?? [], [data]);

  // ── Bulk selection + apply (issue #55) ─────────────────────────────────────
  const toggleSelect = useCallback((dealId: number) => {
    if (bulkPendingRef.current) return;
    setBulkSelected(prev => {
      const next = new Set(prev);
      if (!next.delete(dealId)) next.add(dealId);
      return next;
    });
  }, []);

  const toggleColumn = useCallback((columnIds: number[], select: boolean) => {
    if (bulkPendingRef.current) return;
    setBulkSelected(prev => {
      const next = new Set(prev);
      for (const id of columnIds) {
        if (select) next.add(id); else next.delete(id);
      }
      return next;
    });
  }, []);

  const clearSelection = useCallback(() => setBulkSelected(new Set()), []);

  const isFiltering = search.trim() !== '' || hasAdvanced(advanced);

  // The board loads every deal, so advanced filtering is a pure client-side predicate
  // over `deals` — no refetch. This memo is spliced between `deals` and `grouped`; when
  // nothing is active it returns `deals` by reference so unfiltered renders don't churn.
  // `now` is snapshotted per recompute (on any deals/search/advanced change), so an IDLE
  // tab left open across midnight keeps yesterday's date-bucket boundaries until the next
  // interaction — accepted (self-heals on any filter/drag/refresh; same class as the
  // documented UTC-vs-local date-part skew in pipelineFilters.ts).
  const filteredDeals = useMemo(() => {
    if (!isFiltering) return deals;
    const q = search.trim().toLowerCase();
    const now = new Date();
    return deals.filter(d => {
      if (q) {
        const hay = [d.title, d.contact_name, d.company_name].filter(Boolean).join(' ').toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return dealMatchesAdvanced(d, advanced, now);
    });
  }, [deals, search, advanced, isFiltering]);

  // The issue's "bulk actions operate on the currently filtered set" invariant, enforced
  // ONCE: `filteredDeals` already embeds #21's facet predicate (including the stage facet
  // that hides whole columns), and this single intersection feeds BOTH the bar's count and
  // the apply payload — so what the operator is told and what the server is sent cannot
  // disagree, even if the selection changed since the last render.
  const bulkIds = useMemo(
    () => applicableBulkIds(bulkSelected, filteredDeals),
    [bulkSelected, filteredDeals],
  );

  const applyBulkMove = useCallback(async (toStage: string) => {
    if (bulkPendingRef.current || !toStage) return;
    const ids = applicableBulkIds(bulkSelected, filteredDeals);
    if (ids.length === 0) return;

    setBulkNotice(null);
    const moving = new Set(ids);
    // Take the lock FIRST so no new single-deal write can start while we wait below.
    bulkPendingRef.current = true;
    setBulkPending(true);
    // Same bookkeeping a drag does: an unconfirmed optimistic write exists, and a silent GET
    // spanning it must be invalidated rather than allowed to clobber it. Incremented before
    // the wait so a drag settling during it can't see a zero count and fire its deferred
    // refresh into the middle of this operation.
    pendingWrites.current++;
    writeGen.current++;
    clearSelection();

    // The lock stops NEW single-deal writes, but a drag PUT already in flight for one of
    // these deals would race this POST with no ordering guarantee — and last-writer-wins
    // could leave the board on the drag's stage while we report the bulk succeeded. So wait
    // out the per-deal chains for exactly the ids we're about to move: the same ordering
    // guarantee moveDealStage gives two writes to one deal, extended across the batch.
    // Chains never reject (moveDealStage handles its own errors), but settle defensively.
    await Promise.allSettled(
      ids.map(id => dealWriteChain.current.get(id)).filter(Boolean) as Promise<void>[],
    );

    // Snapshot and paint AFTER the wait, so a drag that reconciled during it doesn't
    // immediately overwrite the optimistic stage we just set. `dealConfirmedStage` is a ref
    // and therefore already live; this map is only the fallback for a deal that has no
    // confirmed entry yet (one whose write never succeeded), so the captured `deals` is right.
    const prevStages = new Map(deals.map(d => [d.id, d.stage]));
    // Optimistic: restage every mover in one pass, positions untouched (same trick as
    // moveDealStage, so a revert needs no position bookkeeping).
    setData(prev => prev ? {
      ...prev,
      deals: prev.deals.map(d => moving.has(d.id) ? { ...d, stage: toStage } : d),
    } : prev);

    // try/finally for the same reason moveDealStage has one: the lock disables drag and
    // every single-deal mutator, and a leaked pendingWrites count defers every later silent
    // refresh — a throw that skipped either release would wedge the board until a reload.
    let writeSettled = false;
    try {
      let outcome;
      try {
        const result = await api<BulkMoveResponse>('/api/crm/deals/bulk-move', {
          method: 'POST', body: JSON.stringify({ deal_ids: ids, stage: toStage }),
        });
        // Record server truth for every deal that actually moved, exactly as moveDealStage
        // does on a successful PUT. Without this, a later FAILED drag on one of these deals
        // would revert it to its pre-bulk stage — contradicting a move that did commit —
        // whenever the reconcile refetch below didn't land.
        if (result.ok) {
          for (const id of result.updated_ids ?? []) dealConfirmedStage.current.set(id, toStage);
        }
        outcome = classifyBulkMove(result);
      } catch (err) {
        console.error('Bulk move failed:', err);
        outcome = classifyBulkMove({
          thrown: err instanceof ApiError ? { status: err.status, reason: err.detail } : {},
        });
      }

      if (outcome.kind === 'rejected') {
        // Nothing was written, so put the board back to server truth — stronger than undoing
        // to `prevStages`, which could itself be an optimistic value that never persisted.
        setData(prev => prev ? {
          ...prev,
          deals: prev.deals.map(d => moving.has(d.id)
            ? { ...d, stage: dealConfirmedStage.current.get(d.id) ?? prevStages.get(d.id) ?? d.stage }
            : d),
        } : prev);
      }
      if (outcome.kind === 'rejected' || outcome.kind === 'unconfirmed') {
        // Retain the submitted ids so the operator can fix the cause and retry without
        // re-selecting. Merged, not assigned, so a selection made mid-flight survives.
        // Deliberately NOT done for skips: those deals DID move, and the skipped ones are
        // gone from the board — re-selecting them would offer a retry that cannot succeed.
        setBulkSelected(prev => new Set([...prev, ...ids]));
      }

      pendingWrites.current--;
      writeSettled = true;
      // Reconcile from server truth before releasing the lock — the refetch is the authority
      // on what actually saved, and holding the lock across it keeps a drag from racing it.
      const reconciled = await load(true);
      const notice = describeBulkMove(outcome, ids.length, reconciled);
      if (notice) {
        if (notice.persistent) setBulkNotice(notice);
        else if (outcome.kind === 'rejected') toast.error(notice.text);
        else toast.info(notice.text);
      }
      // Fire a refresh that deferred while this write was in flight (same check moveDealStage
      // does), so a sheet dismissal during the bulk still lands its fresh derived fields.
      if (pendingWrites.current === 0 && pendingRefresh.current) {
        pendingRefresh.current = false;
        load(true);
      }
    } finally {
      if (!writeSettled) pendingWrites.current--;
      bulkPendingRef.current = false;
      setBulkPending(false);
    }
  }, [bulkSelected, filteredDeals, deals, clearSelection, load]);

  // #18: within each stage column, order by lead_score (hottest first); unscored rows
  // (null) sink below scored ones. Array.sort is stable, so the server's updated_at DESC
  // order is preserved for equal scores — matching the backend's DESC NULLS LAST idiom.
  // Sorts the fresh array `.filter()` returns, so #21's `filteredDeals` is never mutated.
  const grouped = useMemo(
    () => STAGE_ORDER.reduce<Record<string, CrmDeal[]>>((acc, stage) => {
      acc[stage] = filteredDeals
        .filter(d => d.stage === stage)
        .sort((a, b) => (b.lead_score ?? -1) - (a.lead_score ?? -1));
      return acc;
    }, {}),
    [filteredDeals],
  );

  // Stage facet doubles as a column filter: selecting stages hides the rest.
  const visibleStages = useMemo(
    () => (advanced.stages.length ? STAGE_ORDER.filter(s => advanced.stages.includes(s)) : STAGE_ORDER),
    [advanced.stages],
  );

  const kanbanColumns = useMemo(
    () => visibleStages.map(stage => ({ id: stage, data: { stage } })),
    [visibleStages],
  );

  // The ‹ › order IS the board — see `boardNavOrder`. Supplied explicitly because this page
  // renders its own kanban and runs no `useCollectionState` for the layer to read.
  const navOrder = useMemo(() => boardNavOrder(visibleStages, grouped), [visibleStages, grouped]);

  // Per-stage value totals for the column headers — precomputed once per data
  // change so drag re-renders (which fire at pointer-move frequency) don't re-reduce
  // every column on every frame.
  const columnTotals = useMemo(() => {
    const totals: Record<string, number> = {};
    for (const stage of STAGE_ORDER) {
      totals[stage] = (grouped[stage] || []).reduce((s, d) => s + (d.value || 0), 0);
    }
    return totals;
  }, [grouped]);

  // Open-pipeline $/count reflect the FILTERED set so the header describes what's shown
  // (a "showing X of Y" annotation below signals when a filter is narrowing the board).
  const { openTotal, openCount } = useMemo(() => {
    const open = filteredDeals.filter(d => OPEN_STAGES.includes(d.stage));
    return { openTotal: open.reduce((s, d) => s + (d.value || 0), 0), openCount: open.length };
  }, [filteredDeals]);

  // ONE effect strips BOTH deep-link params, and it has to be one: two effects would each build
  // `next` from the same pre-navigation `searchParams`, so the second `replace` would put the
  // key the first had just deleted straight back.
  //
  // ?stage=X (from the dashboard): once `data` has rendered the columns (refs populated), scroll
  // the requested column into view. `scrolledStage` gates the SCROLL only — gating the delete
  // with it left the param stuck in the address bar forever the second time the same stage was
  // linked, since the guard returned before the delete could run.
  //
  // A stage that is NOT in STAGE_ORDER (a typo, a renamed stage, an empty `?stage=`) is deleted
  // immediately and regardless of `data`: waiting for the board is only meaningful for a value
  // something will eventually scroll to, and nothing ever scrolls to a column that cannot exist —
  // so the old `data && valid` gate left a bad parameter in the address bar forever.
  //
  // ?deal=N: already consumed into state at render time above, so it is deleted unconditionally
  // — a board that never loads at all must still leave a clean address bar.
  useEffect(() => {
    const next = new URLSearchParams(searchParams);
    const s = searchParams.get('stage');
    if (s === null) {
      scrolledStage.current = null; // re-arm, so a later link to the same stage scrolls again
    } else if (!STAGE_ORDER.includes(s)) {
      next.delete('stage');
    } else if (data) {
      if (scrolledStage.current !== s) {
        scrolledStage.current = s;
        columnRefs.current.get(s)?.scrollIntoView({ behavior: 'smooth', inline: 'start', block: 'nearest' });
      }
      next.delete('stage');
    }
    next.delete(DEAL_PARAM);
    if (next.toString() !== searchParams.toString()) setSearchParams(next, { replace: true });
  }, [data, searchParams, setSearchParams]);

  // Declared before the early returns and rendered in ALL THREE branches: a shared `?deal=` link
  // must open the panel (through `loadById`) even while the board is still loading, or has failed
  // to load altogether. `CollectionView` does the same thing for the same reason.
  const detailPanel = (
    <CollectionDetail<CrmDeal>
      config={DEAL_DETAIL_CONFIG}
      // The real board array: it resolves `selectedId` before `loadById` is consulted, and
      // membership in it is the same test `onBoard` keys off below.
      items={deals}
      selectedId={selectedDealId}
      onSelect={id => {
        if (id === null) {
          setSelectedDealId(null);
          // Silent-refresh on close so a note or activity logged in the panel updates the deal's
          // last_activity_at (and touch count) without a spinner flash — this is what closes the
          // "filter stale deals → log a touch → it leaves the stale bucket" loop.
          load(true);
        } else {
          setSelectedDealId(Number(id));
        }
      }}
      navOrder={navOrder}
      detail={{
        render: (deal, ctx) => {
          const onBoard = deals.some(d => d.id === deal.id);
          return (
            <DealDetailBody
              deal={deal}
              onBoard={onBoard}
              // Off the board there is no board position to write: the stage move, Won and Lost
              // would all put the deal somewhere this board is not showing.
              stageWritable={onBoard}
              ctx={ctx}
              onMarkWon={markWon}
              onMarkLost={markLost}
              onSaveDeal={saveDeal}
            />
          );
        },
        onRequestClose: denyEscapeBackdrop,
      }}
    />
  );

  // ONE return, with the branch as a SIBLING of the panel rather than each branch carrying its
  // own copy. Three returns would put `detailPanel` at a different position in the element tree
  // per branch, so React unmounts and remounts it on every transition — and the very first
  // transition (loading → loaded) is one a `?deal=` link hits every time, throwing away the
  // record the layer had already fetched, its one-record memory, and any draft the body held.
  const board = loading ? (
    <div style={{ display: 'flex', justifyContent: 'center', padding: '80px 0' }}>
      <div className="w-6 h-6 border-2 border-ck-accent border-t-transparent rounded-full animate-spin" />
    </div>
  ) : !data ? (
    <LoadError label="Couldn't load pipeline" onRetry={() => load()} />
  ) : (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0, padding: isMobile ? '20px 16px' : '32px 44px' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: isMobile ? 16 : 24 }}>
        <div>
          <h1 style={pageHeading(isMobile)}>Pipeline</h1>
          <p style={{ fontSize: isMobile ? 14 : 20, color: INK_MUTE, marginTop: 6 }}>
            ${formatNumber(openTotal)} open · {openCount} open deal{openCount !== 1 ? 's' : ''}
            {isFiltering && (
              <span style={{ color: INK_DIM }}> · showing {filteredDeals.length} of {deals.length}</span>
            )}
          </p>
        </div>
        <button onClick={() => setShowCreate(true)} style={{
          ...btnPrimary,
          padding: '7px 14px', fontSize: 13,
          flexShrink: 0, marginTop: 8,
        }}>
          <IconPlus size={13} strokeWidth={2.25} /> {isMobile ? 'Add' : 'Add Deal'}
        </button>
      </div>

      <div style={{ marginBottom: isMobile ? 12 : 16 }}>
        <PipelineFilterBar
          search={search}
          advanced={advanced}
          onSearchChange={setSearch}
          onAdvancedChange={setAdvanced}
          isMobile={isMobile}
        />
      </div>

      {bulkNotice && (
        <div style={{
          ...stageCard(BG_CARD, ACCENT), padding: '12px 14px', marginBottom: 12,
          display: 'flex', alignItems: 'flex-start', gap: 12,
        }}>
          <span style={{ fontSize: 13, color: INK, lineHeight: 1.45, flex: 1 }}>{bulkNotice.text}</span>
          <button onClick={() => setBulkNotice(null)} style={{ ...btnSecondary, ...btnSmall, flexShrink: 0 }}>
            Dismiss
          </button>
        </div>
      )}

      {!isMobile && bulkIds.length > 0 && (
        <BulkBar
          count={bulkIds.length}
          stage={bulkStage}
          pending={bulkPending}
          onStageChange={setBulkStage}
          onApply={() => applyBulkMove(bulkStage)}
          onClear={clearSelection}
        />
      )}

      {isFiltering && filteredDeals.length === 0 ? (
        <EmptyFilterState onClear={() => setFilters(EMPTY_FILTER_STATE)} />
      ) : (
      <KanbanBoard<CrmDeal, { stage: string }>
        columns={kanbanColumns}
        items={grouped}
        onMove={handleKanbanMove}
        // Drag stays ENABLED while filtering (only `isMobile` disables it). CakeCRM's
        // board is stage-only: `handleKanbanMove` ignores `MoveEvent.newIndex`, same-column
        // drops persist nothing, and `moveDealStage` restages by deal id against the full
        // `data.deals` — so a drop while a filter hides cards is index-safe by construction
        // (unlike the blueprint, whose board persisted intra-column order and disabled drag).
        // A drop that makes a deal stop matching an active facet just removes it from the
        // filtered view — correct filter semantics.
        // Also disabled while a bulk move is in flight: `moveDealStage` would bail out
        // anyway, so a drag would animate and then silently snap back.
        dragDisabled={isMobile || bulkPending}
        // The ported KanbanBoard/KanbanColumn expose only className hooks (no style
        // prop), so board-scroller and column-body layout use Tailwind here; the
        // card and header visuals below use the CRM's inline design tokens.
        className={`flex gap-4 overflow-x-auto pb-3 pt-1${isMobile ? ' snap-x snap-mandatory' : ''}`}
        columnClassName="flex flex-col gap-2 overflow-y-auto max-h-[70vh] min-h-[80px] pr-1"
        renderColumn={(col, children) => {
          const stage = col.data.stage;
          const colDeals = grouped[stage] || [];
          const total = columnTotals[stage] || 0;
          return (
            <div
              key={col.id}
              data-stage={stage}
              ref={el => { if (el) columnRefs.current.set(stage, el); else columnRefs.current.delete(stage); }}
              style={{
                flexShrink: 0,
                width: isMobile ? '85vw' : 288,
                scrollSnapAlign: isMobile ? 'center' : undefined,
              }}
            >
              <StageHeader
                stage={stage} count={colDeals.length} total={total}
                // Select-all operates on this column's FILTERED ids, so it can never pick
                // up a deal the current facets are hiding.
                columnDealIds={isMobile ? [] : colDeals.map(d => d.id)}
                selectedIds={bulkSelected}
                onToggleColumn={toggleColumn}
              />
              {children}
            </div>
          );
        }}
        renderCard={(deal, columnId) => (
          <DealBoardCard
            deal={deal} columnStage={String(columnId)} onOpen={() => setSelectedDealId(deal.id)}
            selectable={!isMobile}
            isSelected={bulkSelected.has(deal.id)}
            onToggleSelect={() => toggleSelect(deal.id)}
          />
        )}
        renderEmptyColumn={() => (
          <div style={{
            fontSize: 12, color: INK_DIM, textAlign: 'center',
            padding: '16px 8px', border: `1px dashed ${LINE}`, borderRadius: 6,
          }}>No deals</div>
        )}
      />
      )}

      {/* Create only — editing a deal is inline in the detail panel now. */}
      {showCreate && <DealForm onClose={() => setShowCreate(false)} onSaved={() => { setShowCreate(false); load(); }} />}
    </div>
  );

  return (
    <>
      {board}
      {detailPanel}
    </>
  );
}

// Shown in place of the board when active filters match no deals (avoids a row of
// empty stage columns reading as "no deals at all").
function EmptyFilterState({ onClear }: { onClear: () => void }) {
  return (
    <div style={{
      display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12,
      padding: '56px 24px', textAlign: 'center', border: `1px dashed ${LINE}`, borderRadius: 8,
    }}>
      <p style={{ fontSize: 15, color: INK_MUTE, margin: 0 }}>No deals match your filters.</p>
      <button onClick={onClear} style={{ ...btnSecondary, ...btnSmall }}>Clear filters</button>
    </div>
  );
}

// The inline bulk bar (issue #55). An inline bar rather than the blueprint's modal: one
// action does not need a dropdown behind a dialog.
function BulkBar({ count, stage, pending, onStageChange, onApply, onClear }: {
  count: number; stage: string; pending: boolean;
  onStageChange: (s: string) => void; onApply: () => void; onClear: () => void;
}) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12,
      padding: '10px 14px', borderRadius: 6,
      background: tint(ACCENT, 8), border: `1px solid ${tint(ACCENT, 30)}`,
    }}>
      <span style={{ fontFamily: FONT_DISPLAY, fontSize: 14, color: INK }}>
        {count} deal{count !== 1 ? 's' : ''} selected
      </span>
      <select
        value={stage}
        onChange={e => onStageChange(e.target.value)}
        disabled={pending}
        aria-label="Move selected deals to stage"
        style={{ ...inputStyle, width: 'auto', textTransform: 'capitalize', marginLeft: 'auto' }}
      >
        <option value="">Move to…</option>
        {STAGE_ORDER.map(s => <option key={s} value={s}>{s}</option>)}
      </select>
      <button
        onClick={onApply}
        disabled={pending || !stage}
        style={{ ...btnPrimary, ...btnSmall, opacity: pending || !stage ? 0.5 : 1 }}
      >
        {pending ? 'Moving…' : 'Apply'}
      </button>
      <button onClick={onClear} disabled={pending} style={{ ...btnSecondary, ...btnSmall }}>Clear</button>
    </div>
  );
}

// Stops a checkbox interaction from reaching the card beneath it. All three matter: CakeCRM's
// KanbanCard spreads the dnd-kit pointer listeners over the WHOLE card (there is no dedicated
// drag grip), and the card itself is a role="button" that opens the detail sheet on click and
// on Space. Without these, ticking a checkbox would start a drag, open the sheet, or both.
const stopCardInteraction = {
  onPointerDown: (e: ReactPointerEvent) => e.stopPropagation(),
  onClick: (e: ReactMouseEvent) => e.stopPropagation(),
  onKeyDown: (e: ReactKeyboardEvent) => e.stopPropagation(),
};

const checkboxStyle = { accentColor: ACCENT, width: 14, height: 14, cursor: 'pointer', flexShrink: 0 };

function StageHeader({ stage, count, total, columnDealIds = [], selectedIds, onToggleColumn }: {
  stage: string; count: number; total: number;
  columnDealIds?: number[];
  selectedIds?: ReadonlySet<number>;
  onToggleColumn?: (ids: number[], select: boolean) => void;
}) {
  const color = STAGE_COLORS[stage]?.color || INK_DIM;
  const selectedHere = selectedIds ? columnDealIds.filter(id => selectedIds.has(id)).length : 0;
  const allSelected = columnDealIds.length > 0 && selectedHere === columnDealIds.length;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10, padding: '0 2px' }}>
      {columnDealIds.length > 0 && onToggleColumn && (
        <input
          type="checkbox"
          checked={allSelected}
          // Indeterminate is not an attribute — it has to be set on the DOM node.
          ref={el => { if (el) el.indeterminate = selectedHere > 0 && !allSelected; }}
          onChange={() => onToggleColumn(columnDealIds, !allSelected)}
          aria-label={`Select all ${stage} deals`}
          style={checkboxStyle}
        />
      )}
      <span style={{ width: 8, height: 8, borderRadius: '50%', flexShrink: 0, background: color }} />
      <span style={{
        fontFamily: FONT_DISPLAY,
        fontSize: 15, letterSpacing: '-0.01em', textTransform: 'capitalize',
        color: INK,
      }}>{stage}</span>
      <span style={mono(10, INK_DIM)}>{count}</span>
      <span style={{ ...mono(10, INK_MUTE), marginLeft: 'auto' }}>${total.toLocaleString()}</span>
    </div>
  );
}

function DealBoardCard({ deal, columnStage, onOpen, selectable = false, isSelected = false, onToggleSelect }: {
  deal: CrmDeal; columnStage: string; onOpen: () => void;
  selectable?: boolean; isSelected?: boolean; onToggleSelect?: () => void;
}) {
  // Colour from the column the card currently sits in (its bucket) rather than
  // deal.stage — during an optimistic drop the bucket updates before the deal's
  // own stage field does, so this keeps the accent correct instantly.
  const color = STAGE_COLORS[columnStage]?.color || INK_DIM;
  const bg = STAGE_COLORS[columnStage]?.bg || BG_CARD;
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onOpen(); } }}
      style={{
        ...stageCard(bg, color), padding: '10px 12px', cursor: 'pointer',
        ...(isSelected ? { borderColor: ACCENT, boxShadow: `0 0 0 1px ${tint(ACCENT, 40)}` } : {}),
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8, marginBottom: 4 }}>
        {selectable && onToggleSelect && (
          <input
            type="checkbox"
            checked={isSelected}
            onChange={onToggleSelect}
            aria-label={`Select ${deal.title}`}
            style={{ ...checkboxStyle, alignSelf: 'center' }}
            {...stopCardInteraction}
          />
        )}
        <span style={{ fontSize: 13, color: INK, lineHeight: 1.3 }}>{deal.title}</span>
        <span style={{
          fontFamily: FONT_DISPLAY,
          fontSize: 14, color: INK, flexShrink: 0,
        }}>${deal.value.toLocaleString()}</span>
      </div>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', fontSize: 11, color: INK_DIM }}>
        {deal.contact_name && <span>{deal.contact_name}</span>}
        {deal.probability > 0 && <span>{deal.probability}%</span>}
        {deal.expected_close_date && <span>{deal.expected_close_date}</span>}
        <ScorePill score={deal.lead_score} compact />
        <TouchCountPill count={deal.ai_touch_count} />
      </div>
    </div>
  );
}
