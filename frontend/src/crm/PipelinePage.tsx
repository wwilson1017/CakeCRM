import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import type { PointerEvent as ReactPointerEvent, MouseEvent as ReactMouseEvent, KeyboardEvent as ReactKeyboardEvent } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api, ApiError } from '../core/api/client';
import type { CrmDeal } from '../core/types';
import { DealForm } from './components/DealForm';
import { DealDetailSheet } from './components/DealDetailSheet';
import { ScorePill, TouchCountPill } from './components/badges';
import { STAGE_COLORS, STAGE_ORDER } from './constants';
import { IconPlus } from '../shared/icons';
import { useIsMobile } from '../shared/useIsMobile';
import { LoadError } from '../shared/LoadError';
import { toast } from '../shared/toast';
import {
  INK, INK_MUTE, INK_DIM, LINE, LINE_STRONG, BG_CARD, BG_ELEV, ACCENT, ACCENT_TEXT, SHADOW,
  FONT_DISPLAY, mono, formatNumber, inputStyle, tint,
} from '../shared/styles';
import { pageHeading, btnPrimary, btnSecondary, btnSmall, stageCard } from './styles';
import type { KanbanColumnDef } from '../shared/dnd';
import { CollectionView, useCollectionState } from '../shared/collection';
import type { CollectionMoveEvent, CollectionSelectionProps } from '../shared/collection';
import { useUsers } from './useUsers';
import {
  boardOrder, loadHiddenStages, openPipelineTotals, saveHiddenStages,
  stageFromToggleKey, stageLabel, stageToggleKey, visibleStageKeys,
} from './pipelineBoard';
import { isArchivedDeal } from './pipelineFilters';
import { archivedSelectionIncludesArchived, makePipelineCollectionConfig } from './pipelineCollection';
import { buildPipelineListColumns } from './components/pipelineListColumns';
import StageChipBar from './components/StageChipBar';
import { STAGE_CRITERIA } from './stageCriteria';
import { applicableBulkIds } from './bulkSelection';
import { classifyBulkMove, describeBulkMove, type BulkMoveResponse, type BulkNotice } from './bulkOutcome';

// The /api/crm/deals payload also carries server-computed `stage_summary` and
// `total_pipeline_value`, but the board derives every total client-side from
// `deals` so they stay correct under optimistic moves — we intentionally read
// only `deals` here rather than trust aggregates the optimistic path can't update.
interface PipelineData {
  deals: CrmDeal[];
}

/** Per-column chrome the board renders, computed from the EXACT set of cards on screen. */
interface StageColumn {
  stage: string;
  count: number;
  total: number;
  dealIds: number[];
}

export function PipelinePage() {
  const [data, setData] = useState<PipelineData | null>(null);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [editDeal, setEditDeal] = useState<CrmDeal | null>(null);
  // Id, not the record: the open deal is looked up from `data` each render, so a sheet left
  // open across a refresh (or an optimistic move) shows the current row rather than a frozen
  // copy — and #75 can swap the sheet for the collection layer's detail panel by changing one
  // render site instead of a state shape.
  const [selectedDealId, setSelectedDealId] = useState<number | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const isMobile = useIsMobile();
  const { users, nameFor } = useUsers();

  // Per-stage column visibility (issue #74). Client state, unlike the blueprint's `stages.hidden`
  // column — CakeCRM's stages are the STAGE_ORDER constants, so there is no row to persist to.
  // The page owns it because it also owns `items` (hidden stages are filtered out BEFORE the
  // collection layer sees them, see `items` below); deriving it from `state.toggles` instead
  // would be circular, since `items` is an input to the hook that produces them.
  const [hiddenStages, setHiddenStages] = useState<Set<string>>(loadHiddenStages);
  useEffect(() => { saveHiddenStages(hiddenStages); }, [hiddenStages]);

  // A stage move is an explicit request to put a deal THERE, so a destination the user had
  // put away gives way — the same call the deep link makes. Without this, moving deals into a
  // hidden stage makes them vanish from the board with no feedback at all: `describeBulkMove`
  // is deliberately silent on a clean run, and drag can't reach a hidden stage, so these two
  // paths (the bulk bar and the sheet's Mark Won/Lost) are the only ways to hit it.
  const revealStage = useCallback((stage: string) => {
    setHiddenStages(prev => (prev.has(stage) ? new Set([...prev].filter(s => s !== stage)) : prev));
  }, []);

  const columnRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  // Last stage the deep-link effect scrolled to — re-fires per NEW target, once each.
  const scrolledStage = useRef<string | null>(null);
  // Per-deal operation counter so out-of-order responses from rapid moves of the
  // SAME deal can't clobber each other — only the latest op reconciles/reverts.
  const dealOpSeq = useRef<Map<number, number>>(new Map());
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
  // ...and whether a deferred load must still REPORT a failure when it replays. Set when the
  // deferred load was user-initiated (an Archived-facet change): replaying it as a plain
  // background refresh would swallow its error, and under "Archived only" a swallowed error
  // renders an empty board that reads as "you have no archived deals".
  //
  // Only the error reporting is carried over, NOT the spinner: `loading` early-returns the
  // spinner INSTEAD OF the page, and a replay fires whenever the last write happens to settle —
  // so replaying loudly would blank an open DealForm or detail sheet mid-edit and lose
  // everything the user had typed. Reporting is what the user needs; the spinner belongs to the
  // interaction that asked for it, and that interaction is over.
  const pendingRefreshReportErrors = useRef(false);
  // Whether a payload has ever rendered. A failed load with no data yet already reports itself
  // through `LoadError`; toasting as well just stacks a second message on top of the error
  // screen, once per retry click.
  const hasLoadedOnce = useRef(false);
  // `load` referenced by the deferral path below, which has to re-fire it. A ref because the
  // callback cannot name itself, and assigned in an effect because a ref write during render is
  // a build-blocking lint error under this repo's react-hooks ruleset.
  const loadRef = useRef<(silent?: boolean, opts?: { reportErrors?: boolean }) => Promise<boolean>>(
    () => Promise.resolve(false),
  );
  // Load generation (issue #83) — see `load`. Distinct from `writeGen`: that one guards a
  // refresh against a racing WRITE; this one guards a load against a newer LOAD, which the
  // Archived facet made reachable by changing the request itself.
  const loadGen = useRef(0);
  // A generation for NON-SILENT loads only. Owns the spinner; see `load`.
  const spinnerGen = useRef(0);
  // Whether the CURRENT request should ask for archived deals. Synced from the facet in the
  // effect below rather than closed over, because the deferred refreshes that
  // `moveDealStage`/`applyBulkMove` fire when their writes settle can outlive the facet they
  // were created under.
  const includeArchivedRef = useRef(false);

  // THE one definition of "wake the deferred load", consumed by all three places that can wake
  // one: a settling single-deal write, a settling bulk move, and `load`'s own self-replay. It
  // was three copies, and the rule they encode is subtle enough that a future edit to one of
  // them would very likely not be made to the other two: replay SILENTLY (a replay fires
  // whenever a write happens to settle, and `loading` returns the spinner INSTEAD of the page —
  // taking the screen at that moment blanks an open form mid-edit) while still carrying the
  // original request's error REPORTING across, so a user-initiated load that got deferred does
  // not have its failure swallowed. No-ops when nothing is pending, so callers only have to
  // know that writes have settled.
  const replayDeferredLoad = useCallback(() => {
    if (!pendingRefresh.current) return;
    pendingRefresh.current = false;
    const reportErrors = pendingRefreshReportErrors.current;
    pendingRefreshReportErrors.current = false;
    void loadRef.current(true, { reportErrors });
  }, []);

  // Drop archived rows from the board when a LIVE-ONLY payload could not be applied — the load
  // was deferred behind a write, or it failed outright. #117 did this with a null branch in the
  // filter predicate; the collection layer skips an INACTIVE facet's predicate entirely, so the
  // prune has to happen on the data instead. Without it, clearing the Archived facet while a
  // drag is in flight (or onto a failing network) leaves archived cards on a board whose facet
  // says live-only — and on a failure that state is not transient, it persists until the next
  // successful load. Reference-stable when there is nothing to drop.
  const pruneArchivedFromBoard = useCallback(() => {
    if (includeArchivedRef.current) return;
    setData(prev => (prev && prev.deals.some(isArchivedDeal)
      ? { ...prev, deals: prev.deals.filter(d => !isArchivedDeal(d)) }
      : prev));
  }, []);

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
  const load = useCallback(async (
    silent = false,
    opts?: { reportErrors?: boolean },
  ): Promise<boolean> => {
    // `=== true` guards against a truthy non-boolean arg (e.g. a bare `onClick={load}`
    // handing in a MouseEvent) accidentally forcing silent mode.
    const isSilent = silent === true;
    // A background refresh normally stays quiet, but a REPLAYED user action must still report
    // its failure even though it no longer takes the spinner — see `pendingRefreshReportErrors`.
    const reportErrors = !isSilent || opts?.reportErrors === true;
    // NO load may clobber an optimistic drag — not just a silent one. If a stage write is
    // already in flight, don't even fire the GET; defer it (moveDealStage re-fires it once
    // writes settle, and it reads the CURRENT facet from the ref, so a deferred refresh still
    // widens). This used to be silent-only, which was safe while every load was a refresh of the
    // same content set; the Archived facet made a load a user-initiated action that could land a
    // pre-write board on top of a drag the user had just made.
    if (pendingWrites.current > 0) {
      pendingRefresh.current = true;
      if (reportErrors) pendingRefreshReportErrors.current = true;
      pruneArchivedFromBoard();
      return false;
    }
    const startGen = writeGen.current;
    // A SECOND generation, for loads rather than writes (issue #83). `writeGen` answers "did a
    // write invalidate this payload?"; this answers "is a newer LOAD already in flight?" — which
    // only became reachable when the Archived facet started changing the request itself, since
    // two quick facet flips can otherwise resolve out of order and leave the board showing the
    // wrong content set.
    const myLoad = ++loadGen.current;
    // The SPINNER gets its OWN generation, bumped only by non-silent loads. It cannot ride
    // `loadGen`, which silent refreshes bump too: gating the reset on that would strand the
    // spinner forever once a silent refresh started after a non-silent one — the loser skips the
    // reset and the winner, being silent, never touches `loading`. Nor can it be a simple
    // in-flight COUNT, which would let one hung request pin the spinner even after a newer load
    // had already painted the board. Newest-non-silent-wins is the only rule correct in both.
    const mySpinner = isSilent ? 0 : ++spinnerGen.current;
    if (!isSilent) setLoading(true);
    try {
      // Read the facet from the ref, never from a closure: `load` is stable, and the deferred
      // refreshes that `moveDealStage`/`applyBulkMove` fire when their writes settle can outlive
      // the facet they were created under. A captured value would let one of them re-fetch the
      // live-only board over the archived rows the user just asked to see — and, because the
      // newest load wins, do it deterministically.
      const d = await api<PipelineData>(
        `/api/crm/deals${includeArchivedRef.current ? '?include_archived=true' : ''}`,
      );
      if (loadGen.current !== myLoad) return false;
      // A write that STARTED during this GET's flight (generation changed) may have made the
      // payload stale — defer+retry rather than clobber a succeeded move OR lose the refresh.
      // Applies to every load, for the same reason as the pre-flight check above.
      if (pendingWrites.current > 0 || writeGen.current !== startGen) {
        pendingRefresh.current = true;
        if (reportErrors) pendingRefreshReportErrors.current = true;
        pruneArchivedFromBoard();
        // A deferred load is normally replayed by the settling write's `finally`. But the write
        // that invalidated this payload may have STARTED AND FINISHED entirely inside this GET's
        // flight, in which case its finally already ran and saw nothing pending — so no one is
        // left to replay us and the load is simply dropped. Re-fire it here. That was a silent
        // staleness bug before #83; now that a facet change can be the deferred load, it would
        // read as the board ignoring the click outright.
        if (pendingWrites.current === 0) {
          queueMicrotask(replayDeferredLoad);
        }
        return false;
      }
      setData(d);
      hasLoadedOnce.current = true;
      dealConfirmedStage.current = new Map(d.deals.map(deal => [deal.id, deal.stage]));
      // Intersect the selection with the deals this payload says are LIVE. Masking an archived
      // deal in the bulk payload and on its card is not enough: the id stays in the Set, so once
      // the deal is restored somewhere else (the assistant, another tab) the next payload brings
      // it back ALREADY SELECTED, joining a bulk move nobody picked it for.
      //
      // Intersecting on presence — rather than only pruning rows explicitly flagged archived —
      // is safe precisely because this payload is `get_pipeline`, which is unpaginated and
      // carries no server-side filter the board ever sets. So on a live-only fetch, "absent"
      // cannot mean "filtered out"; it means archived or gone. A deal you cannot see is a deal
      // you cannot act on, so it must not stay selected.
      setBulkSelected(prev => {
        if (prev.size === 0) return prev;
        const live = new Set(d.deals.filter(deal => !isArchivedDeal(deal)).map(deal => deal.id));
        const next = new Set([...prev].filter(id => live.has(id)));
        return next.size === prev.size ? prev : next;
      });
      return true;
    } catch {
      // data stays null → LoadError below. But once data EXISTS a failure is invisible: the
      // previous payload keeps rendering, and under "Archived only" that means an empty board —
      // indistinguishable from "you have no archived deals". Say so. (Silent refreshes stay
      // quiet; being unobtrusive is their whole contract.)
      if (reportErrors && loadGen.current === myLoad && hasLoadedOnce.current) {
        toast.error('Failed to load deals.');
      }
      // A NARROWING load that failed still has to honour the facet the user just cleared.
      if (loadGen.current === myLoad) pruneArchivedFromBoard();
    }
    finally { if (!isSilent && spinnerGen.current === mySpinner) setLoading(false); }
    return false;
    // `replayDeferredLoad`/`pruneArchivedFromBoard` are stable useCallbacks, so naming them here
    // costs nothing and keeps `load`'s identity stable — which the effects below depend on.
  }, [replayDeferredLoad, pruneArchivedFromBoard]);

  useEffect(() => { loadRef.current = load; }, [load]);

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
  // Adopting the collection layer (#74) inserted `useCollectionState` between `data` and the
  // board, and it deliberately did NOT add a second owner: the hook keeps no copy of the deals
  // (only query/facets/toggles/sort/view), deriving `visibleItems`/`kanbanItems` as pure memos
  // over the `items` array this page computes from `data`. Every write below still goes through
  // `setData` and nothing else caches a deal.
  //
  // Rapid moves of the SAME deal are made safe three ways: (1) the PUTs are chained
  // per deal so the server applies them in action order; (2) a per-deal op sequence
  // means only the latest op reconciles/reverts the client (no intermediate flicker
  // from an earlier op's response); (3) a failed move reverts to the deal's last
  // server-CONFIRMED stage (seeded on load, advanced on every successful PUT), not
  // its optimistic fromStage — so even if two chained writes for one deal BOTH fail,
  // the board rolls back to the true server stage instead of an intermediate stage
  // that never persisted.
  const moveDealStage = useCallback((deal: CrmDeal, toStage: string, fromStage: string) => {
    // A bulk move in flight owns the board until its reconcile refetch lands. A single-deal
    // write started now could reconcile (or roll back) against the stage the bulk request is
    // in the middle of changing, clobbering server truth we're about to fetch.
    if (bulkPendingRef.current) return;
    const dealId = deal.id;
    const seq = (dealOpSeq.current.get(dealId) ?? 0) + 1;
    dealOpSeq.current.set(dealId, seq);
    // Optimistic: restage the CURRENT record (not the captured drag snapshot, which
    // could be missing fields edited meanwhile), keeping its list position.
    setData(prev => prev ? {
      ...prev,
      deals: prev.deals.map(d => d.id === dealId ? { ...d, stage: toStage } : d),
    } : prev);
    pendingWrites.current++; // an unconfirmed optimistic write now exists (see `load`'s silent guard)
    writeGen.current++;      // ...and bump the generation so a silent GET spanning it is invalidated
    const prior = dealWriteChain.current.get(dealId) ?? Promise.resolve();
    const run = prior.then(async () => {
      try {
        const updated = await api<CrmDeal>(`/api/crm/deals/${dealId}`, {
          method: 'PUT', body: JSON.stringify({ stage: toStage }),
        });
        // Record server truth for THIS write regardless of supersession — a later
        // failed move in the same chain reverts to a real confirmed stage, not an
        // optimistic intermediate. Use the response's stage, not toStage, so the
        // ground truth is whatever the server actually stored.
        dealConfirmedStage.current.set(dealId, updated.stage);
        if (dealOpSeq.current.get(dealId) !== seq) return; // a newer move superseded this one
        setData(prev => prev ? {
          ...prev,
          deals: prev.deals.map(d => d.id === dealId ? { ...d, ...updated } : d),
        } : prev);
      } catch (err) {
        if (dealOpSeq.current.get(dealId) !== seq) return; // superseded — leave the newer state
        console.error('Failed to move deal:', err);
        toast.error('Failed to move deal.');
        // Revert to the last server-confirmed stage, not this op's optimistic
        // fromStage: if an earlier chained write for this deal also failed, fromStage
        // is an intermediate the server never stored, so it would leave the board out
        // of sync until the next load. `?? fromStage` covers a deal with no confirmed
        // entry yet (a first move, where fromStage IS the confirmed stage).
        const confirmed = dealConfirmedStage.current.get(dealId) ?? fromStage;
        setData(prev => prev ? {
          ...prev,
          deals: prev.deals.map(d => d.id === dealId ? { ...d, stage: confirmed } : d),
        } : prev);
      } finally {
        pendingWrites.current--; // write settled (reconciled or reverted)
        // Once ALL writes have settled, fire any refresh that was deferred while a write was
        // racing it — so a sheet dismissal (Close OR Mark Won/Lost) still lands the fresh
        // last_activity_at even though the stage PUT was in flight at dismissal time. Since #83
        // an Archived-facet change can be the deferred load too; it re-fires silently but reads
        // the CURRENT facet from the ref, so it still widens the board.
        if (pendingWrites.current === 0) replayDeferredLoad();
      }
    });
    dealWriteChain.current.set(dealId, run);
  }, [replayDeferredLoad]);

  // Drag handler. Resolves immediately so the Kanban hook ends its gesture and
  // re-syncs from `data` right away; persistence + rollback are data-driven (via
  // moveDealStage), never snapshot-driven, so this never needs to throw.
  //
  // `CollectionKanbanProps.onMove` documents "do not patch before this resolves", whose stated
  // reason is that `shared/dnd` rolls back on reject and a pre-resolve canonical write would
  // then double-apply. That branch is unreachable here: this function returns a RESOLVED promise
  // on every path — a failed PUT is handled inside `moveDealStage` against `data`, never by
  // rejecting — so the exemption the type's docstring names applies, and `useKanbanState`'s
  // `commitMove` records the same thing from the other side. Any edit that lets this reject
  // must also stop patching `data` first.
  const handleKanbanMove = useCallback((event: CollectionMoveEvent<CrmDeal>): Promise<void> => {
    const from = String(event.fromColumnId);
    const to = String(event.toColumnId);
    if (from === to) {
      // Same-column drop persists nothing (deals carry no rank column). Bump the
      // deals array reference so the hook re-syncs and drops the transient reorder
      // rather than leaving an unsaved arrangement that jumps back on next load.
      setData(prev => prev ? { ...prev, deals: prev.deals.slice() } : prev);
      return Promise.resolve();
    }
    moveDealStage(event.item, to, from);
    return Promise.resolve();
  }, [moveDealStage]);

  // Detail-sheet handler (Mark Won / Lost) — optimistic move + close the sheet. Also refresh
  // the board (like onClose) so an in-sheet note/activity logged before this dismissal lands
  // its last_activity_at; if a stage move fired, the refresh defers until that PUT settles.
  const updateDealStage = useCallback((deal: CrmDeal, stage: string) => {
    if (deal.stage !== stage) {
      moveDealStage(deal, stage, deal.stage);
      revealStage(stage);
    }
    setSelectedDealId(null);
    load(true);
  }, [moveDealStage, load, revealStage]);

  // A deal was restored from the detail sheet (issue #83). The sheet hands up the row the server
  // RETURNED, which is patched into `data` in place — deliberately not a refetch: `load(true)` is
  // silent and can fail invisibly, which would leave the board still showing the deal as archived
  // after a restore the server actually performed. Patching the authoritative row is why
  // POST /restore returns the deal instead of {"ok": true}. The board then re-derives everything:
  // under 'only' the facet drops it, under 'include' it becomes live, draggable and selectable.
  const restoreDeal = useCallback((restored: CrmDeal) => {
    // Invalidate any load already in flight. Without this, a silent refresh that STARTED before
    // the restore resolves afterwards, passes the generation check, and writes the deal back to
    // archived — undoing a write the server has already committed.
    loadGen.current++;
    setData(prev => (prev
      ? {
        ...prev,
        // MERGE, never replace. `POST /restore` returns `get_deal`'s projection, which is
        // narrower than the board's: `get_pipeline` also derives `last_activity_at`, and a
        // wholesale swap would drop it and drop the restored deal into the "no activity logged"
        // bucket of the Deal-activity facet.
        deals: prev.deals.map(d => (d.id === restored.id ? { ...d, ...restored } : d)),
      }
      : prev));
    dealConfirmedStage.current.set(restored.id, restored.stage);
    // Restoring is not a selection gesture. An id can still be sitting in `bulkSelected` from
    // before the deal was archived — masked everywhere while it stays archived — and would
    // otherwise silently rejoin the next bulk move the moment it came back.
    setBulkSelected(prev => {
      if (!prev.has(restored.id)) return prev;
      const next = new Set(prev);
      next.delete(restored.id);
      return next;
    });
    setSelectedDealId(null);
    // Then refresh in the background. The patch above is what makes the board CORRECT — it
    // deliberately does not depend on this landing — but a restore closes the sheet the same way
    // `onClose` does, and that path refreshes so an in-sheet note reaches the board's derived
    // `last_activity_at`. Without it, restoring a deal you just logged a note on leaves it in the
    // "No activity logged" bucket. It also replaces the in-flight load the bump above discarded.
    queueMicrotask(() => { void loadRef.current(true); });
  }, []);

  const deals = useMemo(() => data?.deals ?? [], [data]);

  // ── The collection layer (issue #74) ───────────────────────────────────────
  const listColumns = useMemo(() => buildPipelineListColumns(nameFor), [nameFor]);
  const config = useMemo(
    () => makePipelineCollectionConfig({ users, ownerName: nameFor, listColumns }),
    [users, nameFor, listColumns],
  );

  // The canonical array the layer filters, sorts and groups. Two things happen here and
  // nowhere else: hidden stages are removed (so their deals are invisible to search, sort, the
  // list view, the header totals AND the bulk intersection by construction, rather than each
  // having to re-apply the rule), and the rest are put in board order — stage-major, then
  // lead_score DESC — which is what the `boardOrder` arrayOrder sort field reads back at rest.
  const items = useMemo(
    () => boardOrder(hiddenStages.size === 0 ? deals : deals.filter(d => !hiddenStages.has(d.stage))),
    [deals, hiddenStages],
  );

  // The visibility checkboxes render through the layer's bar but the VALUES live here, so the
  // hook's controlled-toggle branch hands each click straight back (useCollectionState routes
  // any key present in `values` to `onToggle` and writes nothing itself).
  const toggleValues = useMemo(() => {
    const out: Record<string, boolean> = {};
    for (const stage of STAGE_ORDER) out[stageToggleKey(stage)] = !hiddenStages.has(stage);
    return out;
  }, [hiddenStages]);

  const onToggleStage = useCallback((key: string, visible: boolean) => {
    const stage = stageFromToggleKey(key);
    if (!stage) return;
    setHiddenStages(prev => {
      const next = new Set(prev);
      if (visible) next.delete(stage); else next.add(stage);
      return next;
    });
  }, []);

  const controlledToggles = useMemo(
    () => ({ values: toggleValues, onToggle: onToggleStage }),
    [toggleValues, onToggleStage],
  );

  const state = useCollectionState(config, items, { controlledToggles });

  // Archived deals are swept out of the board payload server-side, so the Archived facet is the
  // one facet that must widen the FETCH as well as filter. Derived as a boolean on purpose: the
  // refetch keys off this rather than off the whole selection map, or every keystroke and every
  // unrelated facet change would refetch the board. 'include' and 'only' need the SAME payload —
  // the difference between them is purely the client-side predicate.
  const includeArchived = archivedSelectionIncludesArchived(state.facetSelections.archived);

  // Mount, and again whenever the Archived facet changes WHICH deals the server should send. The
  // ref is synced here rather than during render (a render-phase ref write is a lint error under
  // this repo's react-hooks ruleset) and, being in the same effect, always lands before the load
  // it triggers. A full non-silent load on purpose: the board's content set is being replaced
  // wholesale, and the spinner is the honest signal for that — a silent swap would leave the old
  // set on screen looking authoritative.
  useEffect(() => {
    includeArchivedRef.current = includeArchived;
    queueMicrotask(load);
  }, [includeArchived, load]);

  // The live half of the visible set. Archived deals are shown as CARDS (that is the whole point
  // of the facet) but are excluded from everything that means money or action: the open-pipeline
  // header, the per-column $ totals, the select-all checkboxes and the bulk payload. The server
  // draws the same line — `stage_summary` keeps the sweep even when the deals query doesn't — so
  // the two cannot disagree about what counts. Reference-stable when nothing is archived, so the
  // ordinary board still doesn't churn.
  const liveVisibleItems = useMemo(
    () => (state.visibleItems.some(isArchivedDeal)
      ? state.visibleItems.filter(d => !isArchivedDeal(d))
      : state.visibleItems),
    [state.visibleItems],
  );

  // What the layer is told is selected. Pruned to the live set for the same reason the payload
  // is: a deal archived somewhere else (the assistant, a merge) can still be sitting in
  // `bulkSelected` from before, and the bulk bar's COUNT comes from the layer while the PAYLOAD
  // is recomputed here — so both sides have to be given the same set or they report different
  // numbers for one click. `load` prunes the state itself on the next payload; this covers the
  // window until then. Reference-stable when nothing was pruned.
  const liveSelectedIds = useMemo(() => {
    if (bulkSelected.size === 0) return bulkSelected;
    const archived = new Set(deals.filter(isArchivedDeal).map(d => d.id));
    if (archived.size === 0) return bulkSelected;
    const next = new Set([...bulkSelected].filter(id => !archived.has(id)));
    return next.size === bulkSelected.size ? bulkSelected : next;
  }, [bulkSelected, deals]);

  // Bumped whenever the page clears the filters programmatically, and used as the
  // CollectionView key. A remount is what actually empties the search box: SearchInput adopts
  // an external value only when it CHANGES, and clearing while `state.query` is already `''`
  // leaves locally-typed text whose 250ms debounce has not settled — which would then re-filter
  // the board a moment after the reset. `useCollectionState` lives here and is NOT remounted,
  // so only the toolbar's own transient state resets, which is exactly the state at fault.
  const [resetSeq, setResetSeq] = useState(0);
  const clearAllFilters = useCallback(() => {
    state.setQuery('');
    state.clearFacets();
    setResetSeq(n => n + 1);
  }, [state]);

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

  const applyBulkMove = useCallback(async (toStage: string) => {
    if (bulkPendingRef.current || !toStage) return;
    // Recomputed at CLICK time rather than reusing the set the layer handed the bar at render
    // time — the selection can change in between. The two agree because both intersect the
    // selection with `state.visibleItems`: the layer's own count comes from the current view's
    // items, and the config declares no `getVoided`, which is what keeps `kanbanItems` and
    // `visibleItems` the same array. Hidden-stage deals are in neither, being absent from `items`.
    // Both sides also drop archived deals — the layer is handed `liveSelectedIds`, this is
    // handed `liveVisibleItems` — because the server refuses a stage change on an archived deal
    // (`_classify_deal_update` raises), so including one could only ever produce a per-deal error
    // in the bulk response. Better never to offer it.
    const ids = applicableBulkIds(bulkSelected, liveVisibleItems);
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
      // Same reasoning as updateDealStage: if any deal actually landed in a stage the user
      // had put away, show that column rather than letting the rows disappear silently.
      // Deliberately includes `skips` — classifyBulkMove carries no updated-count to gate on,
      // and revealing a column for a batch that skipped everything is the safe direction to
      // be wrong in (the skip notice explains itself; hidden rows would not).
      if (outcome.kind !== 'rejected') revealStage(toStage);
      const notice = describeBulkMove(outcome, ids.length, reconciled);
      if (notice) {
        if (notice.persistent) setBulkNotice(notice);
        else if (outcome.kind === 'rejected') toast.error(notice.text);
        else toast.info(notice.text);
      }
      // Fire a refresh that deferred while this write was in flight (same check moveDealStage
      // does), so a sheet dismissal during the bulk still lands its fresh derived fields.
      if (pendingWrites.current === 0) replayDeferredLoad();
    } finally {
      if (!writeSettled) pendingWrites.current--;
      bulkPendingRef.current = false;
      setBulkPending(false);
    }
  }, [bulkSelected, liveVisibleItems, deals, clearSelection, load, revealStage, replayDeferredLoad]);

  // ── Board derivations ──────────────────────────────────────────────────────
  // Trimmed to match `facets.ts`, which normalizes both sides of a multi-facet comparison
  // through `facetKey`. Comparing raw here would let a persisted `" won "` keep Won deals in
  // `visibleItems` (and so in the bulk count AND payload) while rendering no Won column —
  // the exact split the no-getVoided rule exists to prevent. `coerceSelection` accepts any
  // scalar, so a hand-edited or legacy envelope can carry one.
  const stageFacet = useMemo(
    () => ((state.facetSelections.stage ?? []) as (string | number)[])
      .map(v => (typeof v === 'string' ? v.trim() : v)),
    [state.facetSelections.stage],
  );

  // Column chrome from the EXACT rendered set, so a header's count, its $ total and its
  // select-all checkbox all describe what is actually on screen under the current filters.
  const columns = useMemo<KanbanColumnDef<StageColumn>[]>(() => {
    const byStage = new Map<string, CrmDeal[]>();
    for (const d of state.kanbanItems) {
      const list = byStage.get(d.stage);
      if (list) list.push(d); else byStage.set(d.stage, [d]);
    }
    return visibleStageKeys(hiddenStages, stageFacet).map(stage => {
      const list = byStage.get(stage) ?? [];
      return {
        id: stage,
        data: {
          stage,
          // The COUNT describes what you can see, so archived cards count. The $ describes
          // PIPELINE, so they do not (issue #83) — only the money has to match the server's
          // aggregates, which keep the archived sweep unconditionally.
          count: list.length,
          total: list.reduce((s, d) => (isArchivedDeal(d) ? s : s + (d.value || 0)), 0),
          // Select-all collects LIVE ids only — an archived card has no checkbox, so including
          // it would select something the operator cannot see selected. An all-archived column
          // therefore shows no header checkbox, which is right: nothing there is bulk-actionable.
          dealIds: list.filter(d => !isArchivedDeal(d)).map(d => d.id),
        },
      };
    });
  }, [state.kanbanItems, hiddenStages, stageFacet]);

  // Filters active but nothing matched: show one explanation instead of a row of empty
  // columns reading as "there are no deals at all".
  // `items.length > 0` matters: when the board holds nothing at all, the collection layer
  // early-returns its OWN empty state (before its toolbar), so without this guard a fresh
  // install carrying a persisted query would stack "No deals to show." on top of "No deals
  // match your filters." — two different explanations for one blank screen.
  const filteredToNothing = items.length > 0 && state.isFiltering && state.visibleItems.length === 0;

  // Open-pipeline $/count reflect the visible set so the header describes what's shown (the
  // toolbar's own "N of M deals" readout signals when a filter is narrowing the board) — minus
  // archived deals, which are visible but are not open pipeline (issue #83).
  const { openTotal, openCount } = useMemo(
    () => openPipelineTotals(liveVisibleItems),
    [liveVisibleItems],
  );

  const selectedDeal = useMemo(
    () => (selectedDealId === null ? null : deals.find(d => d.id === selectedDealId) ?? null),
    [deals, selectedDealId],
  );

  const handleSelectionChange = useCallback((next: Set<string | number>) => {
    // Same synchronous bail as toggleSelect/toggleColumn: a bulk move in flight owns the board.
    if (bulkPendingRef.current) return;
    setBulkSelected(new Set([...next].map(Number)));
  }, []);

  const selection = useMemo<CollectionSelectionProps>(() => ({
    selectedIds: liveSelectedIds,
    onChange: handleSelectionChange,
    // The layer passes only ids that are BOTH selected and in the current view, and renders
    // this at all only when that set is non-empty — so the count shown and the payload
    // `applyBulkMove` recomputes describe the same deals (see the note there).
    renderBulkBar: (_ids, count) => (
      <BulkBar
        count={count}
        stage={bulkStage}
        pending={bulkPending}
        onStageChange={setBulkStage}
        onApply={() => applyBulkMove(bulkStage)}
        onClear={clearSelection}
      />
    ),
  }), [liveSelectedIds, handleSelectionChange, bulkStage, bulkPending, applyBulkMove, clearSelection]);

  // ── Mobile: which board column is currently snapped into view ──────────────
  const scrollerRef = useRef<HTMLDivElement>(null);
  const [activeStage, setActiveStage] = useState<string | null>(null);
  useEffect(() => {
    if (!isMobile || state.view !== 'kanban') return;
    const root = scrollerRef.current;
    if (!root) return;
    const observer = new IntersectionObserver(
      entries => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          const stage = (entry.target as HTMLElement).dataset.stage;
          if (stage) setActiveStage(stage);
        }
      },
      { root, threshold: 0.6 },
    );
    for (const el of columnRefs.current.values()) observer.observe(el);
    return () => observer.disconnect();
    // `state.view` is a deliberate dependency even though the body reads it once: switching
    // views remounts the board WITHOUT necessarily changing `columns`, which would leave this
    // observer watching detached column nodes through a stale scroller root — the active chip
    // would silently stop following swipes. (`resetSeq` no longer belongs here: it is routed
    // to the search box as a nonce now and remounts nothing.)
  }, [isMobile, state.view, columns]);

  const scrollToStage = useCallback((stage: string) => {
    columnRefs.current.get(stage)?.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
  }, []);

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
    if (validDeepLink) {
      clearAllFilters();
      // A persisted List view has no column to scroll to, and a stage the user put away has no
      // column at all — the link is an explicit request to look at one, so both give way.
      if (state.view !== 'kanban') state.setView('kanban');
      setHiddenStages(prev => (prev.has(validDeepLink) ? new Set([...prev].filter(s => s !== validDeepLink)) : prev));
    }
  }

  // Once `data` has rendered the columns (refs populated), scroll the requested column into
  // view, then clear the `stage` param (preserving any others). Reactive per target —
  // `scrolledStage` guards a re-scroll for the same stage.
  useEffect(() => {
    if (!data) return;
    const s = searchParams.get('stage');
    if (!s || !STAGE_ORDER.includes(s) || scrolledStage.current === s) return;
    scrolledStage.current = s;
    columnRefs.current.get(s)?.scrollIntoView({ behavior: 'smooth', inline: 'start', block: 'nearest' });
    const next = new URLSearchParams(searchParams);
    next.delete('stage');
    setSearchParams(next, { replace: true });
  }, [data, searchParams, setSearchParams]);

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', padding: '80px 0' }}>
        <div className="w-6 h-6 border-2 border-ck-accent border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (!data) return <LoadError label="Couldn't load pipeline" onRetry={() => load()} />;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0, padding: isMobile ? '20px 16px' : '32px 44px' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: isMobile ? 16 : 24 }}>
        <div>
          <h1 style={pageHeading(isMobile)}>Pipeline</h1>
          <p style={{ fontSize: isMobile ? 14 : 20, color: INK_MUTE, marginTop: 6 }}>
            ${formatNumber(openTotal)} open · {openCount} open deal{openCount !== 1 ? 's' : ''}
            {hiddenStages.size > 0 && (
              <>
                <span style={{ color: INK_DIM }}> · {hiddenStages.size} stage{hiddenStages.size !== 1 ? 's' : ''} hidden</span>{' '}
                {/* The way back is HERE, above CollectionView, and not inside it. Hiding every
                    stage empties `items`, and the layer answers an empty `items` with a bare
                    empty state rendered BEFORE its toolbar — so the visibility checkboxes that
                    would undo it are gone at exactly the moment they are needed. This button is
                    always mounted while anything is hidden, so no combination of hides (or a
                    restored all-hidden preference) can strand the board. */}
                <button onClick={() => setHiddenStages(new Set())} style={headerLinkStyle}>Show all</button>
              </>
            )}
            {/* The SAME way back, for the other thing that can empty `items` — a board with no
                rows in the current content set. The layer's empty state replaces its toolbar,
                so the Archived facet is unreachable exactly when it matters most: archive your
                last open deal and the recovery view is behind a control that is no longer on
                screen. It swings both ways, because "Archived only" with nothing archived
                strands the board just as completely. */}
            {items.length === 0 && (
              <>
                <span style={{ color: INK_DIM }}> · </span>
                <button
                  onClick={() => state.setFacet('archived', includeArchived ? null : 'only')}
                  style={headerLinkStyle}
                >{includeArchived ? 'Show live deals' : 'Show archived deals'}</button>
              </>
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

      {isMobile && state.view === 'kanban' && items.length > 0 && !filteredToNothing && (
        <StageChipBar
          stages={columns.map(c => ({ stage: c.data.stage, count: c.data.count }))}
          // Derived from the RENDERED columns, so a facet that just hid the active stage
          // cannot leave the bar highlighting a column that is no longer there.
          activeStage={
            activeStage && columns.some(c => c.data.stage === activeStage)
              ? activeStage
              : columns[0]?.data.stage ?? null
          }
          onSelect={scrollToStage}
        />
      )}

      <CollectionView<CrmDeal, StageColumn>
        config={config}
        state={state}
        items={items}
        searchPlaceholder="Search deals, contacts, companies..."
        // What opens a deal from the LIST view: `CollectionListView` wires every row to
        // `onSelect`, and `ListView` gives each row a pointer cursor and a hover highlight
        // unconditionally — so without this the rows advertise a click and swallow it.
        // This mounts no `CollectionDetail`: the layer gates that on `detail && config.detail`,
        // and the pipeline declares neither until #75. The board card has its own onOpen.
        selectedId={selectedDealId}
        onSelect={id => setSelectedDealId(id === null ? null : Number(id))}
        searchResetNonce={resetSeq}
        // Desktop only: card checkboxes and the bulk bar have always been a pointer-and-keyboard
        // affordance here, and passing `selection` unconditionally would put a bulk bar on
        // phones as a side effect of adopting the layer.
        selection={isMobile ? undefined : selection}
        kanban={{
          // No columns while filtered to nothing — the explanation below replaces the board
          // rather than sitting under a row of empty stage columns.
          columns: filteredToNothing ? [] : columns,
          onMove: handleKanbanMove,
          // The layer's own gate is off (`dragPolicy: 'column'`), so these are the whole gate:
          // touch drag conflicts with the board's horizontal scroll, and a bulk move in flight
          // owns the board — moveDealStage would bail anyway, so a drag would animate then
          // silently snap back.
          // Otherwise PER-CARD (issue #83): an archived deal is on the board to be found and
          // restored, not to be worked — the server refuses a stage change on one, so a drag
          // could only ever animate and revert. `isArchivedDeal` is module-level, so this is a
          // stable identity rather than a per-render closure.
          dragDisabled: isMobile || bulkPending ? true : isArchivedDeal,
          scrollerRef,
          // The ported KanbanBoard/KanbanColumn expose only className hooks (no style
          // prop), so board-scroller and column-body layout use Tailwind here; the
          // card and header visuals below use the CRM's inline design tokens.
          className: `flex gap-4 overflow-x-auto pb-3 pt-1${isMobile ? ' snap-x snap-mandatory' : ''}`,
          columnClassName: 'flex flex-col gap-2 overflow-y-auto max-h-[70vh] min-h-[80px] pr-1',
          renderColumn: (col, children) => (
            <div
              key={col.id}
              data-stage={col.data.stage}
              ref={el => { if (el) columnRefs.current.set(col.data.stage, el); else columnRefs.current.delete(col.data.stage); }}
              style={{
                flexShrink: 0,
                width: isMobile ? '85vw' : 288,
                scrollSnapAlign: isMobile ? 'center' : undefined,
              }}
            >
              <StageHeader
                stage={col.data.stage} count={col.data.count} total={col.data.total}
                // Select-all operates on this column's FILTERED ids, so it can never pick
                // up a deal the current facets are hiding.
                columnDealIds={isMobile ? [] : col.data.dealIds}
                selectedIds={bulkSelected}
                onToggleColumn={toggleColumn}
                onHide={() => onToggleStage(stageToggleKey(col.data.stage), false)}
              />
              {children}
            </div>
          ),
          renderCard: (deal, columnId) => {
            // An archived card is inert: no checkbox, and never rendered as selected — a deal
            // archived elsewhere (the assistant, a merge) could otherwise still be in
            // `bulkSelected` from before, showing selected styling with no way to clear it. It
            // stays clickable, because opening it is how you reach Restore.
            const archived = isArchivedDeal(deal);
            return (
              <DealBoardCard
                deal={deal} columnStage={String(columnId)} onOpen={() => setSelectedDealId(deal.id)}
                selectable={!isMobile && !archived}
                isSelected={!archived && bulkSelected.has(deal.id)}
                onToggleSelect={() => toggleSelect(deal.id)}
                archived={archived}
              />
            );
          },
          renderEmptyColumn: () => (
            <div style={{
              fontSize: 12, color: INK_DIM, textAlign: 'center',
              padding: '16px 8px', border: `1px dashed ${LINE}`, borderRadius: 6,
            }}>No deals</div>
          ),
        }}
      />

      {state.view === 'kanban' && filteredToNothing && <EmptyFilterState onClear={clearAllFilters} />}

      {showCreate && <DealForm onClose={() => setShowCreate(false)} onSaved={() => { setShowCreate(false); load(); }} />}
      {editDeal && <DealForm deal={editDeal} onClose={() => setEditDeal(null)} onSaved={() => { setEditDeal(null); setSelectedDealId(null); load(); }} />}

      {/* ── THE DEAL-DETAIL SEAM ────────────────────────────────────────────────
          Issue #75 replaces exactly this block, in three steps: add
          `detail: { getTitle, loadById }` to the config in `pipelineCollection.ts`; pass the
          `detail` prop to CollectionView above (`selectedId` and `onSelect` are ALREADY
          passed — they drive list-row selection today, so that half needs no edit); then
          delete these lines. `DealDetailBody` renders inside `CollectionDetail`'s render
          prop, never here — the page keeps owning only WHICH deal is open, by id, which is
          what makes this a one-file swap. `kanbanColumnIds` already flows
          CollectionView → CollectionDetail off `kanban.columns`, so ‹ › walks the board
          column-major with no extra wiring. ──────────────────────────────────────────── */}
      {selectedDeal && (
        <DealDetailSheet
          key={selectedDeal.id}
          deal={selectedDeal}
          isMobile={isMobile}
          // Silent-refresh the board on close so an in-sheet note/activity log updates the
          // deal's last_activity_at (and touch count) without a spinner flash — closes the
          // "filter stale deals → log a touch → it leaves the stale bucket" loop.
          onClose={() => { setSelectedDealId(null); load(true); }}
          onEdit={(d) => { setSelectedDealId(null); setEditDeal(d); }}
          onStageChange={updateDealStage}
          onRestored={restoreDeal}
        />
      )}
    </div>
  );
}

// The two page-header escape hatches share one look: a plain underlined text link in the
// subtitle line. Both exist because emptying `items` takes the layer's toolbar off screen
// with it, so anything that can empty it needs its undo mounted ABOVE `CollectionView`.
const headerLinkStyle = {
  background: 'none', border: 'none', padding: 0, cursor: 'pointer',
  font: 'inherit', color: ACCENT_TEXT, textDecoration: 'underline',
} as const;

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
      display: 'flex', alignItems: 'center', gap: 10,
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
        style={{ ...inputStyle, width: 'auto', marginLeft: 'auto' }}
      >
        <option value="">Move to…</option>
        {/* stageLabel, not a CSS text-transform: one title-casing rule for every stage name
            on this page, so a future multi-word stage cannot render three ways. */}
        {STAGE_ORDER.map(s => <option key={s} value={s}>{stageLabel(s)}</option>)}
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

function StageHeader({ stage, count, total, columnDealIds = [], selectedIds, onToggleColumn, onHide }: {
  stage: string; count: number; total: number;
  columnDealIds?: number[];
  selectedIds?: ReadonlySet<number>;
  onToggleColumn?: (ids: number[], select: boolean) => void;
  onHide?: () => void;
}) {
  const color = STAGE_COLORS[stage]?.color || INK_DIM;
  const selectedHere = selectedIds ? columnDealIds.filter(id => selectedIds.has(id)).length : 0;
  const allSelected = columnDealIds.length > 0 && selectedHere === columnDealIds.length;
  const [showCriteria, setShowCriteria] = useState(false);
  const criteria = STAGE_CRITERIA[stage];

  // Escape closes the popover, matching every other dismissible surface in the CRM.
  useEffect(() => {
    if (!showCriteria) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setShowCriteria(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [showCriteria]);

  return (
    <div style={{ marginBottom: 10, padding: '0 2px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
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
        {criteria ? (
          <button
            type="button"
            onClick={() => setShowCriteria(v => !v)}
            aria-expanded={showCriteria}
            title={`What belongs in ${stageLabel(stage)}?`}
            style={{
              fontFamily: FONT_DISPLAY,
              fontSize: 15, letterSpacing: '-0.01em',
              color: INK, cursor: 'help', padding: 0,
              background: 'none', border: 'none',
              borderBottom: `1px dashed ${LINE_STRONG}`,
            }}
          >{stageLabel(stage)}</button>
        ) : (
          <span style={{ fontFamily: FONT_DISPLAY, fontSize: 15, letterSpacing: '-0.01em', color: INK }}>
            {stageLabel(stage)}
          </span>
        )}
        <span style={mono(10, INK_DIM)}>{count}</span>
        <span style={{ ...mono(10, INK_MUTE), marginLeft: 'auto' }}>${total.toLocaleString()}</span>
        {onHide && (
          <button
            type="button"
            onClick={onHide}
            aria-label={`Hide ${stageLabel(stage)} column`}
            title={`Hide ${stageLabel(stage)} column`}
            style={{
              background: 'none', border: 'none', cursor: 'pointer', padding: '0 2px',
              color: INK_DIM, fontSize: 14, lineHeight: 1, flexShrink: 0,
            }}
          >×</button>
        )}
      </div>
      {showCriteria && criteria && (
        // Rendered INLINE rather than absolutely positioned: the board is a horizontal
        // `overflow-x: auto` scroller, which clips an absolutely-positioned popover no matter
        // what z-index it carries. Expanding the header instead is immune to that.
        <div style={{
          marginTop: 8, padding: 12, borderRadius: 6,
          background: BG_ELEV, border: `1px solid ${LINE_STRONG}`,
          boxShadow: `0 8px 40px ${SHADOW}`,
        }}>
          <p style={{ fontSize: 12, color: INK_MUTE, margin: 0, lineHeight: 1.5 }}>{criteria.summary}</p>
          <p style={{ fontSize: 12, color: INK, margin: '10px 0 6px', fontWeight: 500 }}>
            Criteria to enter this stage:
          </p>
          <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 4 }}>
            {criteria.checklist.map(item => (
              <li key={item} style={{ fontSize: 12, color: INK_MUTE, lineHeight: 1.45, display: 'flex', gap: 6 }}>
                <span style={{ color: INK_DIM, flexShrink: 0 }}>☐</span>{item}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function DealBoardCard({ deal, columnStage, onOpen, selectable = false, isSelected = false, onToggleSelect, archived = false }: {
  deal: CrmDeal; columnStage: string; onOpen: () => void;
  selectable?: boolean; isSelected?: boolean; onToggleSelect?: () => void;
  /** Soft-archived (issue #83): dimmed + labelled, un-draggable, not selectable. */
  archived?: boolean;
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
        // Restates the same two properties `stageCard` sets rather than reaching for the
        // `borderColor` longhand: mixing a longhand into an object that already carries the
        // `border` shorthand makes React warn on every selection toggle ("Updating a style
        // property during rerender when a conflicting property is set"), and which one wins
        // is then order-dependent. Visually identical — the whole border goes accent, the
        // left edge keeps its 3px weight. Pre-existing since #55; fixed here because this
        // PR owns the file.
        ...(isSelected
          ? {
              border: `1px solid ${ACCENT}`,
              borderLeft: `3px solid ${ACCENT}`,
              boxShadow: `0 0 0 1px ${tint(ACCENT, 40)}`,
            }
          : {}),
        // Dimmed rather than struck through: a card is mostly whitespace, so opacity plus the
        // explicit chip below reads faster than a line through the title would.
        ...(archived ? { opacity: 0.55 } : {}),
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8, marginBottom: 4 }}>
        {archived && (
          <span style={{
            ...mono(9, INK_DIM), border: `1px solid ${LINE_STRONG}`, borderRadius: 3,
            padding: '1px 4px', flexShrink: 0, alignSelf: 'center',
          }}>ARCHIVED</span>
        )}
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
