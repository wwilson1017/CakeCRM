import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import type { PointerEvent as ReactPointerEvent, MouseEvent as ReactMouseEvent, KeyboardEvent as ReactKeyboardEvent } from 'react';
import { useLocation, useSearchParams } from 'react-router-dom';
import { api, ApiError } from '../core/api/client';
import type { CrmDeal } from '../core/types';
import { DealForm } from './components/DealForm';
import { DealDetailBody, type DealPatch } from './components/DealDetailBody';
import { ScorePill, TouchCountPill } from './components/badges';
import DealTemperatureIcon from './components/DealTemperatureIcon';
import { DealTemperatureWriter } from './components/DealTemperatureCell';
import { STAGE_COLORS, STAGE_ORDER } from './constants';
import { stageWriteRequest } from './dealStageWrite';
import { IconPlus } from '../shared/icons';
import { useIsMobile } from '../shared/useIsMobile';
import { LoadError } from '../shared/LoadError';
import { formatDate } from '../shared/formatDate';
import { toast } from '../shared/toast';
import type { DealTemperature } from './dealTemperature';
import {
  INK, INK_MUTE, INK_DIM, LINE, LINE_STRONG, BG_CARD, BG_ELEV, BG_PAGE, ACCENT, ACCENT_TEXT, SHADOW,
  FONT_DISPLAY, mono, formatNumber, inputStyle, tint,
} from '../shared/styles';
import { pageHeading, btnPrimary, btnSecondary, btnSmall, stageCard, LAUNCHER_CLEARANCE_PX } from './styles';
import type { KanbanColumnDef } from '../shared/dnd';
import { useBoardScroller } from '../shared/dnd';
import { CollectionView, denyEscapeBackdrop, useCollectionState } from '../shared/collection';
import type { CollectionMoveEvent, CollectionSelectionProps } from '../shared/collection';
import { useUsers } from './useUsers';
import {
  boardColumnLayout, boardOrder, lastContactLabel, loadHiddenStages, openPipelineTotals,
  saveHiddenStages, stageFromToggleKey, stageLabel, stageToggleKey, visibleStageKeys,
} from './pipelineBoard';
import type { BoardDensity } from './pipelineBoard';
import { isArchivedDeal } from './pipelineFilters';
import { CORPUS_MAX_AGE_MS } from './usePatchableAssembly';
import { archivedSelectionIncludesArchived, makePipelineCollectionConfig } from './pipelineCollection';
import { buildPipelineListColumns } from './components/pipelineListColumns';
import StageChipBar from './components/StageChipBar';
import { STAGE_CRITERIA } from './stageCriteria';
import { applicableBulkIds } from './bulkSelection';
import {
  DEAL_DEEP_LINK_PARAM, deepLinkVerdict, parseDealDeepLinkId,
} from './dealDeepLink';
import { sweepPipelineDeals } from './pipelineAssembly';
import { classifyBulkMove, describeBulkMove, type BulkMoveResponse, type BulkNotice } from './bulkOutcome';

// The /api/crm/deals payload also carries server-computed `stage_summary` and
// `total_pipeline_value`, but the board derives every total client-side from
// `deals` so they stay correct under optimistic moves — we intentionally read
// only `deals` here rather than trust aggregates the optimistic path can't update.
// (Since #59 those two are `null` on a continuation page anyway: the sweep pays for that
// whole-table aggregate once rather than on every page. Nothing here notices, which is
// exactly why it was safe to stop computing them.)
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

export function PipelinePage() {
  const [data, setData] = useState<PipelineData | null>(null);
  // Board load generations: the newest load STARTED, and the newest whose payload was
  // APPLIED. Both are state rather than refs because the deep-link block below reads them
  // during render, and both halves are needed to answer its one question — "has the server
  // answered ABOUT THIS LINK?".
  //
  // `data`'s own identity is no signal at all — every optimistic update on this page (a
  // drag's stage patch, its rollback, the same-column reorder, a bulk reconcile) replaces
  // the object without asking the server anything. That was a real false accusation.
  //
  // A count of APPLIED payloads would fix that much, but generations answer a sharper
  // question: a load that started BEFORE the link cannot know about a deal created after
  // it, so its landing must not settle the link. In practice `load` already drops such a
  // payload — a newer load bumps `loadGen`, and a racing write defers it — so this is the
  // last line rather than the only one, and it is kept because it costs nothing and does
  // not depend on those guards keeping their current shape.
  const [boardLoads, setBoardLoads] = useState({ started: 0, applied: 0 });
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  // Id, not the record: the open deal is resolved against the CURRENT board array each render,
  // so a save, an optimistic move or a silent refresh reaches the panel rather than leaving it
  // on a frozen copy — and an id is also what a deep link carries. #75 swapped the sheet for
  // the collection layer's detail panel by changing one render site, not this state shape.
  const [selectedDealId, setSelectedDealId] = useState<number | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  // Every navigation gets a fresh key, including one to the URL already showing. That is
  // what makes following the SAME deep link twice a distinguishable event (issue #145) —
  // the parsed id alone cannot tell a second click from no click at all.
  const location = useLocation();
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

  // Deal deep link (/crm/pipeline?deal=N) — issue #145. The assistant attaches this URL
  // to every deal it names, in chat and in messages that leave the app, so clicking one
  // has to land on that deal. Sibling of the ?stage= link above, and resolved the same
  // way: React's render-time "reset state when an input changes" pattern, NOT an effect.
  // That is not a style choice — this repo's react-hooks ruleset makes a synchronous
  // setState inside an effect a build-blocking error, and suppressing it is not an option.
  //
  // The RULES live in `dealDeepLink.ts` as a pure function. vitest runs in node, so logic
  // left in this component is untestable, and deciding whether a deal is really gone is
  // the whole correctness story of the feature.
  //
  // Unlike ?stage=, the parameter is NOT stripped once acted on — the resolution keys off
  // the NAVIGATION instead. ?stage= is a one-shot "scroll here" intent; ?deal= names a
  // record, which is what a URL is for, so keeping it makes reload reopen the deal and the
  // address bar a real copy source.
  //
  // Stripping was tried, because leaving the id in the URL makes a second click on the same
  // link indistinguishable from no click at all — and a chat transcript is exactly where one
  // gets clicked twice. Keying on `location.key` answers that without a rewrite: every
  // navigation is its own event, including one to the URL already showing. Stripping also
  // turned out to cost more than it bought — the rewrite is itself a navigation, so it
  // re-armed the link it had just resolved and fired a second board refresh, and it raced
  // the ?stage= consumer for the same parameter object.
  //
  // Worth flagging for #75, which specifies stripping for its Copy-link button: that came
  // from the blueprint, whose own notes call the lost deal on refresh a caveat. Here the
  // address bar survives a reload, so the button has something real to copy.
  const deepLinkDealId = parseDealDeepLinkId(searchParams.get(DEAL_DEEP_LINK_PARAM));
  // The link being resolved, plus the newest load generation that had already STARTED when
  // it arrived. One object so a new target resets both together and they can never disagree
  // about which link the snapshot belongs to. See `boardLoads` above for why a generation
  // and not an object identity or a plain count.
  const [deepLink, setDeepLink] = useState<{
    dealId: number | null; loadsAtArrival: number; key: string | null;
  }>({ dealId: null, loadsAtArrival: 0, key: null });
  // The target the user has already dealt with, so the sheet does not spring back open the
  // moment they close it. Cleared when a new target arrives, which is what lets the same
  // link be followed again later.
  const [handledDeepLink, setHandledDeepLink] = useState<number | null>(null);
  // The dead-link notice, held as its own state rather than derived from the verdict — it
  // has to survive the board changing underneath it, and the user has to be able to dismiss
  // it without the next render putting it straight back.
  const [deadDeepLinkDealId, setDeadDeepLinkDealId] = useState<number | null>(null);
  // The navigation currently being resolved, readable from an async continuation — the
  // retirement check below runs after an await and cannot see the render's value.
  const deepLinkKeyRef = useRef<string | null>(null);
  // Which deal, if any, the sheet on screen was opened by a LINK. Distinct from
  // `handledDeepLink`, which never clears until the next link: this one clears the moment
  // the user takes over the sheet (closes it, or opens a card themselves), so a later link
  // supersedes only a sheet a link put there. Tracking the id alone would close a card the
  // user had opened by hand just because a previous link had once opened the same deal.
  const [linkOpenedDeal, setLinkOpenedDeal] = useState<number | null>(null);
  // Re-arm on the NAVIGATION, not on the parsed id. Two reasons, both dead ends otherwise:
  // following the same link twice never changes the id, and a link whose refresh FAILED is
  // never consumed, so its parameter is still sitting there and a retry would look
  // identical to no click at all.
  // Leaving the parameter behind forgets which navigation we resolved. Back/Forward
  // RESTORES a history entry's original key rather than minting one, so without this,
  // returning to a `?deal=` entry the user had already visited would not re-arm — a reload
  // reopened the deal but Back did not, which nobody would predict.
  if (deepLinkDealId === null && deepLink.key !== null) {
    setDeepLink({ dealId: null, loadsAtArrival: 0, key: null });
    // Clear the notice with it. Navigating away — clicking the Pipeline nav item from
    // `/crm/pipeline?deal=404`, say — leaves this page mounted, so a notice about a link
    // that is no longer in the URL would otherwise sit there with nothing to dismiss it.
    // `linkOpenedDeal` deliberately survives: the sheet is still open and still the one a
    // link put there, so a later link should still supersede it.
    setDeadDeepLinkDealId(null);
    setHandledDeepLink(null);
  }
  if (deepLinkDealId !== null && location.key !== deepLink.key) {
    setDeepLink({ dealId: deepLinkDealId, loadsAtArrival: boardLoads.started, key: location.key });
    setHandledDeepLink(null);
    setDeadDeepLinkDealId(null);
    // A new link supersedes the sheet the PREVIOUS one opened — otherwise following a link
    // to a deal that turns out to be gone leaves the old deal's sheet on screen, reading as
    // though the new link had opened the wrong record.
    if (selectedDealId !== null && selectedDealId === linkOpenedDeal) {
      setSelectedDealId(null);
      setLinkOpenedDeal(null);
    }
  }
  // Membership is asked of the WHOLE payload, never of `filteredDeals`: a session facet
  // that hides a card says nothing about whether the deal exists, and the detail sheet
  // opens over the board regardless of what the columns are showing. An archived deal IS
  // genuinely absent (the fetch excludes them unless the Archived facet is on), which is
  // the case the notice exists for.
  const deepLinkedDeal = deepLink.dealId === null
    ? null
    : (data?.deals ?? []).find(d => d.id === deepLink.dealId) ?? null;
  const deepLinkState = deepLinkVerdict({
    dealId: deepLink.dealId,
    boardLoaded: data !== null,
    dealOnBoard: deepLinkedDeal !== null,
    boardRefreshedSinceLink: boardLoads.applied > deepLink.loadsAtArrival,
  });
  // Act only on a render where the resolved target is still the one this navigation named.
  // The block above SCHEDULES a new target, but this render still evaluates the old one —
  // React discards the pass's output, not the state updates queued during it — so without
  // this gate a superseded link could raise its notice on the way out and leave it there.
  //
  // Honest about coverage: this one is defence in depth and no test isolates it. The
  // sequence it guards needs an unresolved link to reach a verdict in the very render a
  // second link supersedes it, which is not reachable from the page's own controls. It is
  // kept because it costs a comparison and the alternative is reasoning about queued
  // updates from a discarded render pass every time this block is touched.
  const deepLinkIsCurrent = deepLink.key === location.key;
  if (deepLinkIsCurrent && deepLinkState === 'open' && deepLinkedDeal
      && handledDeepLink !== deepLink.dealId) {
    setHandledDeepLink(deepLink.dealId);
    // Un-hide the deal's stage, exactly as `?stage=` does for the column it names. A facet and a
    // hidden STAGE are not the same thing here, and that difference is the whole reason this line
    // exists: facets are applied by `useCollectionState` DOWNSTREAM of `items`, so a facet-hidden
    // deal is still a record `CollectionDetail` can resolve — but a hidden stage is filtered out
    // of `items` itself (#74's rule, so the List view and the board agree about what is on the
    // board), and `items` is the array `CollectionDetail` resolves `selectedId` against. Without
    // this, a linked deal in a hidden stage falls to the layer's `loadById` path: an avoidable
    // second fetch on the happy path, and a panel with no card behind it on the board.
    //
    // Since #124 hides `won` and `lost` by DEFAULT, that is every link to a closed deal on a
    // default install — and the assistant attaches one to every deal it names, `crm_mark_deal_won`
    // included. So this is the ordinary path, not an edge.
    //
    // The verdict above is unchanged and stays filter-blind: membership is still asked of the
    // WHOLE payload, so the dead-link notice cannot misfire. This acts on 'open', it does not
    // decide it.
    revealStage(deepLinkedDeal.stage);
    setSelectedDealId(deepLinkedDeal.id);
    setLinkOpenedDeal(deepLink.dealId);
  } else if (deepLinkIsCurrent && deepLinkState === 'dead'
             && handledDeepLink !== deepLink.dealId) {
    setHandledDeepLink(deepLink.dealId);
    setDeadDeepLinkDealId(deepLink.dealId);
  }
  // The notice claims the deal is not on this board, and tells the user how to bring it
  // back — the Archived facet is the documented route, and it refetches. The moment the
  // deal is there the claim is false, so the notice goes; opening it is what following the
  // link asked for. It cannot be left to the resolution above, which acts once per
  // navigation and has already had its turn by the time the facet refetches.
  const noticedDealNowOnBoard = deadDeepLinkDealId === null
    ? null
    : (data?.deals ?? []).find(d => d.id === deadDeepLinkDealId) ?? null;
  if (noticedDealNowOnBoard) {
    setDeadDeepLinkDealId(null);
    setSelectedDealId(noticedDealNowOnBoard.id);
    setLinkOpenedDeal(noticedDealNowOnBoard.id);
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
  // WHEN a payload was last applied (ms since epoch), 0 until the first one. Two readers, and
  // the second is why this is a timestamp rather than the boolean it used to be:
  //   • the toast gate in `load`'s catch — a failed load with no data yet already reports itself
  //     through `LoadError`, and toasting as well just stacks a second message on the error
  //     screen, once per retry click;
  //   • the return-to-tab staleness bound (issue #129), which needs the age, not the fact.
  const appliedAt = useRef(0);
  // Set when the FIRST load was skipped because the tab was hidden — a route can be opened into a
  // background tab (a Cmd-click, a session restore), and sweeping the whole deal corpus for a
  // board nobody is looking at is work with no reader. The visibility listener below fires it on
  // the first return. A ref, not state: nothing renders differently for it (the page is already
  // showing its spinner) and it is only ever read inside effects and handlers.
  const deferredMountLoad = useRef(false);
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
  //
  // The counter is read across an `await` by `updateDealStage`, which is the one caller whose own
  // refresh a replay can make redundant — see there for why a second load is not merely wasteful.
  const replayCount = useRef(0);
  const replayDeferredLoad = useCallback(() => {
    if (!pendingRefresh.current) return;
    pendingRefresh.current = false;
    const reportErrors = pendingRefreshReportErrors.current;
    pendingRefreshReportErrors.current = false;
    replayCount.current++;
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
    // Mirror the generation into state so the deep-link resolution can read it in render.
    setBoardLoads(b => ({ ...b, started: myLoad }));
    // The SPINNER gets its OWN generation, bumped only by non-silent loads. It cannot ride
    // `loadGen`, which silent refreshes bump too: gating the reset on that would strand the
    // spinner forever once a silent refresh started after a non-silent one — the loser skips the
    // reset and the winner, being silent, never touches `loading`. Nor can it be a simple
    // in-flight COUNT, which would let one hung request pin the spinner even after a newer load
    // had already painted the board. Newest-non-silent-wins is the only rule correct in both.
    const mySpinner = isSilent ? 0 : ++spinnerGen.current;
    if (!isSilent) setLoading(true);
    try {
      // Read the facet from the ref, never from a closure: `load` is stable, and the
      // deferred refreshes that `moveDealStage`/`applyBulkMove` fire when their writes
      // settle can outlive the facet they were created under. A captured value would let
      // one of them re-fetch the live-only board over the archived rows the user just
      // asked to see — and, because the newest load wins, do it deterministically.
      // #59: one GET became a keyset sweep. It resolves only with the COMPLETE corpus, in
      // the server's own recency order, so everything below this line is unchanged — the
      // board still holds every deal and every facet still filters an in-memory array.
      const d: PipelineData = {
        deals: await sweepPipelineDeals(
          includeArchivedRef.current,
          () => loadGen.current === myLoad,
        ),
      };
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
      // The ONLY place a server payload is applied. Record WHICH load applied it, so a
      // reader can ask whether the answer post-dates something rather than merely that an
      // answer arrived (issue #145). `Math.max` because loads can settle out of order.
      setBoardLoads(b => (myLoad > b.applied ? { ...b, applied: myLoad } : b));
      appliedAt.current = Date.now();
      dealConfirmedStage.current = new Map(d.deals.map(deal => [deal.id, deal.stage]));
      // Intersect the selection with the deals this payload says are LIVE. Masking an archived
      // deal in the bulk payload and on its card is not enough: the id stays in the Set, so once
      // the deal is restored somewhere else (the assistant, another tab) the next payload brings
      // it back ALREADY SELECTED, joining a bulk move nobody picked it for.
      //
      // Intersecting on presence — rather than only pruning rows explicitly flagged
      // archived — is safe because this payload is the COMPLETE corpus: `get_pipeline`
      // carries no server-side filter the board ever sets, so on a live-only fetch
      // "absent" cannot mean "filtered out". A deal you cannot see is a deal you cannot
      // act on, so it must not stay selected.
      //
      // #59 made the fetch a keyset sweep, which is snapshotless: a deal can also be
      // absent because it committed (or was restored) behind the cursor mid-sweep. That
      // widens "absent" but does not weaken this, because the miss can only ever DROP an
      // id — a transient loss the user fixes by re-selecting. The failure this prune
      // exists to prevent needs a deal to APPEAR already selected, which requires the
      // opposite error. Refresh is the answer to a missed row, as it is for the #77 lists.
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
      if (reportErrors && loadGen.current === myLoad && appliedAt.current !== 0) {
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

  // Mirrored in an effect, not during render — a render-phase ref write is a build error
  // under this repo's react-hooks ruleset.
  useEffect(() => { deepLinkKeyRef.current = deepLink.key; }, [deepLink.key]);

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
  //
  // The detail panel's inline Save comes through here too, carrying its changed columns in the
  // SAME patch as any stage change — one PUT, because the server settles `probability` to 100/0
  // inside the transaction that moves the stage, so a stage write and a probability write split
  // across two requests is a race whose loser silently wins. What a fields-only write does NOT do
  // is therefore conditional on the patch carrying a stage — it paints nothing, records no
  // confirmed stage, and announces no failed move — while still taking a sequence number and
  // riding this deal's chain, so it is ordered against a concurrent drag rather than racing it.
  // The REVERT is the one step that is unconditional; see the catch block for why.
  //
  // `lostReason` (issue #128) only changes WHICH request this op issues — it is captured
  // per operation alongside `seq`, inside the same per-deal promise chain, so every
  // invariant above is untouched: the chain still serializes, the sequence check still
  // discards a superseded response, and rollback still reads the confirmed stage. Both
  // endpoints return the same `get_deal` projection, so the reconcile merge is unchanged.
  const writeDeal = useCallback((
    deal: CrmDeal, patch: DealPatch, fromStage?: string, lostReason?: string,
  ): Promise<CrmDeal> => {
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
    // The outcome carries the SERVER's row, not just success: the detail panel folds it into its
    // own read channel so a save that changed the stage shows the probability the server derived
    // (100/0) rather than the one the form sent. Resolved before the supersession check below, so
    // a superseded write still answers its caller.
    let settle: (updated: CrmDeal) => void = () => {};
    let fail: (err: unknown) => void = () => {};
    const outcome = new Promise<CrmDeal>((resolve, reject) => { settle = resolve; fail = reject; });
    const run = prior.then(async () => {
      try {
        // `POST /mark-lost` is the ONE write here that is not the patch PUT (#128): it zeroes
        // `probability` and appends the reason to the notes thread inside the transaction that
        // closes the deal, neither of which `PUT {stage:'lost'}` does. `stageWriteRequest` owns
        // that choice for every host and keys on `lostReason !== undefined` — the ACTION, not the
        // text — so an explicit close with the box left blank still takes the verb. No patch is
        // ever stranded by the narrower body: the reason dialog is the only caller that supplies
        // a reason, and it sends the stage alone.
        const { path, init } = patch.stage === 'lost' && lostReason !== undefined
          ? stageWriteRequest(dealId, patch.stage, lostReason)
          : { path: `/api/crm/deals/${dealId}`, init: { method: 'PUT', body: JSON.stringify(patch) } };
        const updated = await api<CrmDeal>(path, init);
        // Record server truth for THIS write regardless of supersession — a later
        // failed move in the same chain reverts to a real confirmed stage, not an
        // optimistic intermediate. Use the response's stage, not toStage, so the
        // ground truth is whatever the server actually stored.
        dealConfirmedStage.current.set(dealId, updated.stage);
        settle(updated);
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
        // Once ALL writes have settled, fire any refresh that was deferred while a write was
        // racing it — so a sheet dismissal (Close OR Mark Won/Lost) still lands the fresh
        // last_activity_at even though the stage PUT was in flight at dismissal time. Since #83
        // an Archived-facet change can be the deferred load too; it re-fires silently but reads
        // the CURRENT facet from the ref, so it still widens the board.
        if (pendingWrites.current === 0) replayDeferredLoad();
      }
    });
    dealWriteChain.current.set(dealId, run);
    return outcome;
  }, [replayDeferredLoad]);

  // Drag handler. Resolves immediately so the Kanban hook ends its gesture and
  // re-syncs from `data` right away; persistence + rollback are data-driven (via
  // writeDeal), never snapshot-driven, so this never needs to throw.
  //
  // `CollectionKanbanProps.onMove` documents "do not patch before this resolves", whose stated
  // reason is that `shared/dnd` rolls back on reject and a pre-resolve canonical write would
  // then double-apply. That branch is unreachable here: this function returns a RESOLVED promise
  // on every path — a failed PUT is handled inside `writeDeal` against `data`, never by
  // rejecting — so the exemption the type's docstring names applies, and `useKanbanState`'s
  // `commitMove` records the same thing from the other side. Any edit that lets this reject
  // must also stop patching `data` first.
  const handleKanbanMove = useCallback((event: CollectionMoveEvent<CrmDeal, 'column'>): Promise<void> => {
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
  //
  // `lostReason` is present only for a Mark Lost taken through the reason dialog (#128); it rides
  // through to `writeDeal`, which is where the endpoint is chosen.
  const updateDealStage = useCallback(async (deal: CrmDeal, stage: string, lostReason?: string) => {
    // Read BEFORE the await. If a load is deferred behind this very write — the user reaching for
    // the Archived facet while the PUT is in the air — the write's own `finally` replays it, and
    // that replay is issued AFTER the server answered, so it already carries everything the
    // refresh below would ask for. Issuing a second one would not merely be redundant: `load`
    // bumps `loadGen`, so the later request DISCARDS the replay, and with it the failure report a
    // user-initiated facet load is owed. Under "Archived only" that swallowed failure renders an
    // empty board reading as "you have no archived deals" — the exact false answer #83 fixed.
    const replaysBefore = replayCount.current;
    if (deal.stage !== stage) {
      // Un-hide the destination BEFORE the write, not after it. `writeDeal` moves the card
      // optimistically, so the move and the reveal have to be ONE gesture: with the reveal after
      // the await, a Mark Won into a stage the user has put away makes the card disappear for the
      // whole duration of the PUT, with nothing on screen to say where it went — and since #124
      // `won` and `lost` are put away by DEFAULT, so that is the ordinary case rather than a rare
      // one. That vanishing is the exact failure this reveal exists to prevent; Mark Won/Lost is
      // one of the two paths that can reach a hidden stage (drag cannot, and the bulk bar reveals
      // for the same reason).
      //
      // The other ordering is not free either, and this is the cheaper cost: a failed write
      // reverts the stage, so a revealed column can end up holding no card. An empty column the
      // user can see and put away again is harmless; a card that vanishes mid-write is not.
      revealStage(stage);
      try {
        await writeDeal(deal, { stage }, deal.stage, lostReason);
      } catch (err) {
        // Report ONLY the refusal `writeDeal` cannot report itself. A stage PUT that reached the
        // server and failed has already raised its own toast in there; toasting again here would
        // say the same thing twice.
        if (err instanceof BulkLockError) toast.error(err.message);
        // RETHROWN, not swallowed: the panel's own close-out is awaiting this, and a resolve is
        // what tells it the deal is closed and it may go. Reporting is still ours — `writeDeal`
        // toasts a failed stage PUT itself, so only the lock refusal is added above.
        throw err;
      }
    }
    // No dismissal here. `DealDetailBody` closes itself on a successful write, because only a
    // MOUNTED body can answer "is the panel in front of me still the one that asked" — see its
    // `onClose` prop. This function's remaining job is the board's: the destination stage is
    // already revealed above, so what is left is the refresh.
    if (replayCount.current === replaysBefore) load(true);
  }, [writeDeal, load, revealStage]);

  // These RETURN the promise rather than `void` it: `DealDetailBody` awaits them to keep its
  // close-out buttons `disabled` for the whole write (#128) — that attribute IS the re-entry
  // guard, and a second Mark Lost appends a second "Deal lost —" note. `updateDealStage` reports
  // its own failures and never rejects, so awaiting it here can only ever settle.
  const markWon = useCallback((deal: CrmDeal) => updateDealStage(deal, 'won'), [updateDealStage]);
  const markLost = useCallback(
    (deal: CrmDeal, lostReason?: string) => updateDealStage(deal, 'lost', lostReason),
    [updateDealStage],
  );
  // The inline form's ONE save. The patch may carry `stage`; `writeDeal` handles that itself.
  const saveDeal = useCallback(
    (deal: CrmDeal, patch: DealPatch) => writeDeal(deal, patch, deal.stage),
    [writeDeal],
  );

  // Deal temperature (issue #125) — the rep's hot/warm/cold read, cycled in place from a board
  // card or a List row. A fields-only patch through the SAME `writeDeal`, so it serialises with a
  // drag of the same deal on that deal's write chain: both reconcile from `get_deal`'s full row,
  // and in parallel a stage response would spread a stale `deal_temperature` over the one just
  // written. `DealTemperatureIcon` owns the optimistic glyph and puts it back on failure, so
  // nothing is painted here.
  //
  // It TOASTS, unlike `saveDeal`, because `writeDeal`'s own toast is stage-only by design (a
  // fields-only write announced no move, so it announces no failed one) and the form that
  // normally shows such an error inline does not exist for a one-click control. A `BulkLockError`
  // carries its own sentence, exactly as `updateDealStage` handles it.
  //
  // The board card takes this as a plain prop; the List reaches it through
  // `DealTemperatureWriter` below, because its columns are built in a `useMemo` that may not
  // hold a ref-reading callback. That file has the full reason.
  const cycleDealTemperature = useCallback(
    async (deal: CrmDeal, next: DealTemperature | null): Promise<void> => {
      try {
        await writeDeal(deal, { deal_temperature: next }, deal.stage);
      } catch (err) {
        toast.error(err instanceof BulkLockError ? err.message : 'Failed to update deal temperature.');
        throw err;   // the icon releases its optimistic glyph either way; this only reports
      }
    },
    [writeDeal],
  );

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
    // Not dismissed here either — the body does that, and only while it is still on screen.
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
    // Issue #129: a route can mount into a tab nobody is looking at — a Cmd-click onto the board,
    // a browser restoring its session — and this load is a keyset sweep of the whole deal corpus.
    // Defer it until the tab is first shown. The page is honest while deferred: `loading` starts
    // true, so it renders its spinner rather than an empty board, and the `?deal=` verdict stays
    // `idle` because no payload has landed, so a gated board can never accuse a live deal of
    // being gone.
    //
    // The FIRST load only. A facet change is a user action performed on a visible tab, and it
    // replaces the board's whole content set — deferring that would leave the previous set on
    // screen looking authoritative.
    if (appliedAt.current === 0 && document.visibilityState === 'hidden') {
      deferredMountLoad.current = true;
      return;
    }
    queueMicrotask(load);
  }, [includeArchived, load]);

  // Returning to a backgrounded tab: run the deferred first load, or re-sweep a corpus that has
  // gone stale (issue #129).
  //
  // This is the list pages' idiom — `useCrmCorpus`'s `visibilitychange` + `CORPUS_MAX_AGE_MS`
  // (`usePatchableAssembly.ts`) — extended to the one swept corpus that had no staleness bound at
  // all. Deliberately the SAME constant and the same mechanism, not a second timer model: the
  // board has exactly the writers the list pages do (the assistant's CRM tools, Telegram, the
  // Gmail touch scan, another seat, another tab), and a board left open overnight was filtering
  // and dragging yesterday's deals with nothing on screen saying so.
  //
  // The staleness re-sweep is SILENT on purpose — a returning user must not get a spinner that
  // wipes an open sheet — and it needs no bookkeeping of its own: `load` already defers behind
  // in-flight writes, drops a payload a newer load superseded, and prunes archived rows. The
  // deferred FIRST load is not silent, because it owns the spinner the page is currently showing.
  //
  // Note what is deliberately NOT gated: the refetches that settle a WRITE — `applyBulkMove`'s
  // reconcile and `replayDeferredLoad`. Those can indeed run while the tab is hidden, but they
  // are the second half of an action the user just took, not background polling, and
  // `applyBulkMove` holds `bulkPendingRef` (which disables drag and refresh) until its reconcile
  // lands. Gating them would wedge the board behind a lock until the user came back.
  useEffect(() => {
    const onVisibilityChange = () => {
      if (document.visibilityState !== 'visible') return;
      if (deferredMountLoad.current) {
        deferredMountLoad.current = false;
        void loadRef.current();
        return;
      }
      if (appliedAt.current === 0) return;
      if (Date.now() - appliedAt.current < CORPUS_MAX_AGE_MS) return;
      void loadRef.current(true);
    };
    document.addEventListener('visibilitychange', onVisibilityChange);
    return () => document.removeEventListener('visibilitychange', onVisibilityChange);
  }, []);

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
  // ONE archived-id set behind both halves of the selection contract: the prune below, and
  // the layer's `isSelectable`. Two derivations of "which deals are archived" could disagree
  // for a render and put a checkbox on a row whose id is being thrown away.
  const archivedIds = useMemo(
    () => new Set<string | number>(deals.filter(isArchivedDeal).map(d => d.id)),
    [deals],
  );

  const liveSelectedIds = useMemo(() => {
    if (bulkSelected.size === 0) return bulkSelected;
    if (archivedIds.size === 0) return bulkSelected;
    const next = new Set([...bulkSelected].filter(id => !archivedIds.has(id)));
    return next.size === bulkSelected.size ? bulkSelected : next;
  }, [bulkSelected, archivedIds]);

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

  // How wide a column gets and how much its cards say, from the ONE number that says how
  // focused this board is: how many stage columns are actually rendering (issue #182). Hiding
  // stages is the gesture — via #124's default, the per-tab hide, or the stage facet — and all
  // three arrive here already resolved, because `columns` is built from `visibleStageKeys`.
  //
  // Depends on the LENGTH, not the array: the memo above rebuilds on every deal edit (counts and
  // totals change), and re-deriving a tier that cannot have moved would re-render every card for
  // nothing. The tier only changes when a column appears or disappears.
  const boardLayout = useMemo(() => boardColumnLayout(columns.length), [columns.length]);

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


  const handleSelectionChange = useCallback((next: Set<string | number>) => {
    // Same synchronous bail as toggleSelect/toggleColumn: a bulk move in flight owns the board.
    if (bulkPendingRef.current) return;
    setBulkSelected(new Set([...next].map(Number)));
  }, []);

  const selection = useMemo<CollectionSelectionProps>(() => ({
    selectedIds: liveSelectedIds,
    onChange: handleSelectionChange,
    // The LIST view's counterpart to the board's `selectable={!isMobile && !archived}`. Both
    // views render the same records, so both owe the same answer: an archived deal is
    // findable, never money, and never actionable. Without this the list offered a checkbox
    // that `liveSelectedIds` pruned straight back out — a control that could be clicked
    // forever and never tick.
    isSelectable: id => !archivedIds.has(id),
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
  }), [liveSelectedIds, archivedIds, handleSelectionChange, bulkStage, bulkPending, applyBulkMove, clearSelection]);

  // ── Mobile: which board column is currently snapped into view ──────────────
  // The board's scroll region. Two consumers, one node: on desktop `useBoardScroller` bounds its
  // height so its scrollbars land inside the window (issue #129), and on mobile the chip bar
  // below observes it to track which stage is snapped into view.
  //
  // Not bounded when the facets match nothing: the board then renders zero columns and
  // `EmptyFilterState` explains why, so a 320px floor would park a blank band above the
  // explanation. Not bounded on mobile either — see the hook.
  const { ref: boardScrollerRef, node: boardScrollerNode } = useBoardScroller(!isMobile && !filteredToNothing);
  const [activeStage, setActiveStage] = useState<string | null>(null);
  useEffect(() => {
    if (!isMobile || state.view !== 'kanban') return;
    const root = boardScrollerNode.current;
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
    //
    // `boardScrollerNode` is a `useRef` object and so never changes identity — it is listed only
    // because it crosses a custom-hook boundary, where exhaustive-deps cannot see that.
  }, [isMobile, state.view, columns, boardScrollerNode]);

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
  // view, then clear the `stage` param (preserving any others — `?deal=` in particular, which
  // #145 keeps on purpose). Reactive per target.
  //
  // `scrolledStage` gates the SCROLL only. Gating the DELETE with it too left the parameter
  // stuck in the address bar forever the second time the same stage was linked, because the
  // guard returned before the delete could run.
  //
  // A stage that is not in STAGE_ORDER (a typo, a renamed stage, an empty `?stage=`) is deleted
  // immediately and regardless of `data`: waiting for the board is only meaningful for a value
  // something will eventually scroll to, and nothing ever scrolls to a column that cannot exist —
  // so the old `data && valid` gate left a bad parameter up forever.
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
    if (next.toString() !== searchParams.toString()) setSearchParams(next, { replace: true });
  }, [data, searchParams, setSearchParams]);

  // The only side effect the deep link needs: the board on screen predates the link, so
  // refresh once before deciding anything. The assistant hands out links to deals it has
  // just created, and this page stays mounted while its drawer is open — that board is
  // silent about the deal, not evidence against it.
  //
  // Bounded to ONE attempt by these deps rather than by a flag: a `refresh` verdict is
  // stable, so a failed load — which applies no payload and so changes neither `data` nor
  // the verdict — does not re-run this. The pipeline then says nothing at all rather than
  // accusing anyone, which is the trade `deepLinkVerdict` documents.
  //
  // `queueMicrotask` for the same reason the mount effect below uses it: `load` calls
  // setState, and calling it synchronously from an effect body is a build-blocking error
  // under this repo's react-hooks ruleset.
  // Keyed on the NAVIGATION as well as the verdict. Two cases need it, and both are dead
  // ends on a verdict-only dependency because the verdict sits at `refresh` for each: a
  // second link followed while the first is still refreshing would ride the first one's
  // request, whose generation predates it and so can never settle it; and retrying the same
  // link after a failed refresh changes neither the verdict nor the id, so nothing re-runs
  // at the moment the user has most reason to try again.
  useEffect(() => {
    if (deepLinkState !== 'refresh') return;
    const target = deepLink.dealId;
    const attemptKey = deepLink.key;
    queueMicrotask(() => {
      const genBefore = loadGen.current;
      void load(true).then(applied => {
        // A refresh that never landed leaves the verdict at `refresh` forever, and this
        // effect will not run again — so the link would sit armed until some unrelated load
        // minutes later (closing another card's sheet, a facet flip) happened to satisfy it
        // and popped a sheet open with no gesture toward it. Retire it instead: the page
        // stays silent, which is the documented trade, and following the link again retries
        // because the resolution keys off the navigation.
        //
        // But `applied === false` is not the same as "failed". `load` also returns false
        // when it was DEFERRED behind a write and when it was SUPERSEDED by a newer load,
        // and in both of those someone else is still going to settle this link — retiring
        // on them makes the link do nothing at all, which is the opposite of the fix. So
        // all three have to hold: the load really failed, nothing replaced it, and the
        // navigation it belonged to is still the one on screen.
        if (applied) return;
        if (pendingRefresh.current) return;                    // deferred; it replays itself
        if (loadGen.current !== genBefore + 1) return;         // superseded by a newer load
        if (deepLinkKeyRef.current !== attemptKey) return;     // a newer navigation owns this
        setHandledDeepLink(target);
      });
    });
  }, [deepLinkState, deepLink.dealId, deepLink.key, load]);


  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', padding: '80px 0' }}>
        <div className="w-6 h-6 border-2 border-ck-accent border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (!data) return <LoadError label="Couldn't load pipeline" onRetry={() => load()} />;

  // The detail panel is NOT hoisted above these returns, deliberately — an earlier revision of
  // #75 did that, when the panel was a page-owned sibling. It buys nothing here: the panel is
  // mounted by `CollectionView`, which only exists in this branch, so an early return and a
  // ternary unmount it identically. And nothing can be selected before the board lands anyway —
  // `deepLinkVerdict` answers `idle` until a payload has been applied, precisely so a network
  // blip is never read as "that deal is gone".
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

      {deadDeepLinkDealId !== null && (
        <div style={{
          ...stageCard(BG_CARD, ACCENT), padding: '12px 14px', marginBottom: 12,
          display: 'flex', alignItems: 'flex-start', gap: 12,
        }}>
          <span style={{ fontSize: 13, color: INK, lineHeight: 1.45, flex: 1 }}>
            That link points to deal #{deadDeepLinkDealId}, which isn't on this board — it
            may have been archived or deleted. Turn on the Archived filter to look for it.
          </span>
          <button onClick={() => setDeadDeepLinkDealId(null)}
                  style={{ ...btnSecondary, ...btnSmall, flexShrink: 0 }}>
            Dismiss
          </button>
        </div>
      )}

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

      {/* Supplies the List view's temperature cells with this page's writer. The board card takes
          the same callback as a plain prop — only the memoized columns need the indirection. */}
      <DealTemperatureWriter value={cycleDealTemperature}>
      <CollectionView<CrmDeal, StageColumn, 'column'>
        config={config}
        state={state}
        items={items}
        searchPlaceholder="Search deals, contacts, companies..."
        // What opens a deal from the LIST view: `CollectionListView` wires every row to
        // `onSelect`, and `ListView` gives each row a pointer cursor and a hover highlight
        // unconditionally — so without this the rows advertise a click and swallow it.
        // Since #75 this also drives the detail panel: the layer mounts `CollectionDetail`
        // itself off `detail` + `config.detail`, ABOVE its own loading branch, so a shared
        // `?deal=` link opens the record rather than a spinner. The board card has its own onOpen.
        selectedId={selectedDealId}
        onSelect={id => {
          if (id === null) {
            setSelectedDealId(null);
            // Closing by hand releases whatever link owned the panel, so a second click on the
            // same link is still a distinguishable event rather than a no-op.
            setLinkOpenedDeal(null);
            // Silent-refresh on close so a note or activity logged in the panel updates the
            // deal's last_activity_at (and touch count) without a spinner flash — this is what
            // closes the "filter stale deals → log a touch → it leaves the stale bucket" loop.
            load(true);
          } else {
            setSelectedDealId(Number(id));
            setLinkOpenedDeal(null);
          }
        }}
        detail={{
          render: (deal, ctx) => {
            // The two flags answer two DIFFERENT questions, so each reads the array that answers
            // its own — conflating them is a bug in whichever direction you pick.
            //
            // `onBoard` is "did this row come from the host's canonical array, so a write patches
            // it in place?". That array is `items`, which is what the layer resolved `selectedId`
            // against — and `items` drops the stages the user has put away, so such a deal came
            // from `loadById` and is a ONE-SHOT snapshot nothing will ever replace. Answering
            // this from `deals` would tell the body the prop is authoritative when it is frozen,
            // and its canonical-wins merge would then repaint pre-save values over its own
            // refreshed detail after every save.
            const onBoard = items.some(d => d.id === deal.id);
            return (
              <DealDetailBody
                deal={deal}
                onBoard={onBoard}
                // `stageWritable` is "is there a board position to write?", which IS a question
                // about the whole payload: a put-away column is a view preference and says
                // nothing about whether the deal is on this board. Off the payload entirely, the
                // stage move, Won and Lost would all put the deal somewhere this board is not
                // showing.
                //
                // ARCHIVED is deliberately NOT tested here. The body gates on it too, from its
                // OWN detail fetch — the only reader that knows, since a deal archived after the
                // board loaded carries `archived_at: null` in this row. A second check on the
                // stale row could only ever be wrong in one direction: denying a restored deal
                // its stage controls with no way back.
                stageWritable={deals.some(d => d.id === deal.id)}
                ctx={ctx}
                onMarkWon={markWon}
                onMarkLost={markLost}
                onSaveDeal={saveDeal}
                onRestored={restoreDeal}
                onClose={() => { setSelectedDealId(null); setLinkOpenedDeal(null); }}
              />
            );
          },
          onRequestClose: denyEscapeBackdrop,
        }}
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
          scrollerRef: boardScrollerRef,
          // The ported KanbanBoard/KanbanColumn expose only className hooks (no style
          // prop), so board-scroller and column-body layout use Tailwind here; the
          // card and header visuals below use the CRM's inline design tokens.
          //
          // DESKTOP (issue #129): ONE scroll region, both axes. `useBoardScroller` above gives
          // this element an explicit height ending at the bottom of the window, so `overflow-auto`
          // puts BOTH scrollbars on screen — where `overflow-x-auto` on an in-flow block put the
          // horizontal one at the bottom of the tallest column, off screen on any busy board.
          // `items-start` is what lets a stage header's `sticky top-0` travel at all: flex's
          // default `stretch` sizes every column wrapper to the CONTAINER, and a sticky element
          // cannot leave its containing block, so headers came unstuck after about one viewport.
          //
          // MOBILE is byte-for-byte what it was: an in-flow board of `85vw` snap columns, each
          // with its own `max-h-[70vh]` scrollport. There is no persistent scrollbar to reach on
          // a touch platform, sideways movement is a swipe, and the chrome above the board can be
          // a third of the viewport — bounding it would only shrink it.
          className: isMobile
            ? 'flex gap-4 overflow-x-auto pb-3 pt-1 snap-x snap-mandatory'
            : 'flex items-start gap-4 overflow-auto pb-3 pt-1',
          columnClassName: isMobile
            ? 'flex flex-col gap-2 overflow-y-auto max-h-[70vh] min-h-[80px] pr-1'
            : 'flex flex-col gap-2 min-h-[80px] pr-1',
          renderColumn: (col, children) => (
            <div
              key={col.id}
              data-stage={col.data.stage}
              ref={el => { if (el) columnRefs.current.set(col.data.stage, el); else columnRefs.current.delete(col.data.stage); }}
              style={{
                // MOBILE is untouched by #182: a fixed `85vw` snap column. Flexing it would
                // fight the snap-scroll, and there is no freed width to reclaim on a phone —
                // the board shows one column at a time by design.
                //
                // DESKTOP (issue #182) flexes to fill the row instead of sitting at a fixed
                // 288px. `flex: 1` is `1 1 0%`, so every column is an equal share of the scrollport,
                // floored at `minWidth` and capped at `maxWidth` — both from the density tier.
                // The floor is the old fixed width, so a full 5-or-6-stage board still lays out
                // exactly as it did, and once the floors overflow, `min-width` stops the shrink
                // and #129's single scroll region takes over sideways.
                //
                // The two branches are written as whole objects rather than as per-property
                // ternaries so no render ever carries both the `flex` shorthand and a
                // conflicting `flexShrink`/`width` longhand — React warns about exactly that
                // mix, and which one wins is then order-dependent (the same trap `DealBoardCard`
                // documents on its border).
                ...(isMobile
                  ? { flexShrink: 0, width: '85vw', scrollSnapAlign: 'center' as const }
                  : { flex: 1, minWidth: boardLayout.minWidth, maxWidth: boardLayout.maxWidth }),
                // The fixed "Ask Baker" pill floats over the bottom-left of the viewport. Every
                // other page scrolls out from under it using CrmLayout's own bottom padding; a
                // board bounded to the window cannot, so the last card of the leftmost column
                // would sit permanently beneath the pill. Same clearance, applied where this
                // board's scrolling actually happens.
                paddingBottom: isMobile ? undefined : LAUNCHER_CLEARANCE_PX,
              }}
            >
              <StageHeader
                sticky={!isMobile}
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
                deal={deal} columnStage={String(columnId)}
                // Opening a card by hand takes the sheet away from whatever link last owned
                // it, so a later link supersedes only what a link actually put there.
                onOpen={() => { setSelectedDealId(deal.id); setLinkOpenedDeal(null); }}
                selectable={!isMobile && !archived}
                isSelected={!archived && bulkSelected.has(deal.id)}
                onToggleSelect={() => toggleSelect(deal.id)}
                archived={archived}
                onCycleTemperature={cycleDealTemperature}
                // Issue #182: the card says more as the board narrows, and the column it sits
                // in is wider by the same tier. `nameFor` is the resolver the LIST view's Owner
                // column already uses, so the two views cannot name the same owner differently.
                density={boardLayout.density}
                ownerName={nameFor}
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
      </DealTemperatureWriter>

      {state.view === 'kanban' && filteredToNothing && <EmptyFilterState onClear={clearAllFilters} />}

      {/* Create only — editing a deal is inline in the detail panel now. */}
      {showCreate && <DealForm onClose={() => setShowCreate(false)} onSaved={() => { setShowCreate(false); load(); }} />}
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

function StageHeader({ stage, count, total, columnDealIds = [], selectedIds, onToggleColumn, onHide, sticky = false }: {
  stage: string; count: number; total: number;
  columnDealIds?: number[];
  selectedIds?: ReadonlySet<number>;
  onToggleColumn?: (ids: number[], select: boolean) => void;
  onHide?: () => void;
  /** Pin the header while the BOARD scrolls vertically (issue #129, desktop only). */
  sticky?: boolean;
}) {
  const color = STAGE_COLORS[stage]?.fill || INK_DIM;
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
    // Sticky since #129, because the desktop board scrolls vertically and this header sits above
    // the column body rather than inside it — so it would otherwise scroll away and take the
    // stage name, the total and the select-all checkbox with it.
    //
    // Three details are load-bearing. The background must be OPAQUE (`BG_PAGE`, the page ground
    // this board sits on) so cards pass underneath rather than through. The 10px gap below moves
    // from `marginBottom` to `padding`, because a margin under a sticky element is transparent
    // and cards would show in the strip. And `zIndex: 1` beats the sortable cards, which are
    // transformed but keep `z-index: auto`.
    //
    // Known limit, and it is upstream's too: with `items-start` each column wrapper is only as
    // tall as its own content, and a sticky element cannot leave its containing block — so a
    // SHORT column's header unpins once its own cards have scrolled past. Keeping every header
    // pinned needs a scrollport/header-row split (one sticky row above one scrolling body), which
    // is a restructure of `renderColumn`'s contract rather than a rider on this issue.
    <div style={sticky
      ? { position: 'sticky', top: 0, zIndex: 1, background: BG_PAGE, padding: '0 2px 10px' }
      : { marginBottom: 10, padding: '0 2px' }}>
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

function DealBoardCard({ deal, columnStage, onOpen, selectable = false, isSelected = false, onToggleSelect, archived = false, onCycleTemperature, density, ownerName }: {
  deal: CrmDeal; columnStage: string; onOpen: () => void;
  selectable?: boolean; isSelected?: boolean; onToggleSelect?: () => void;
  /** Soft-archived (issue #83): dimmed + labelled, un-draggable, not selectable. */
  archived?: boolean;
  /** Issue #125. Omitted ⇒ the glyph renders read-only, with no button and no tab stop. */
  onCycleTemperature?: (deal: CrmDeal, next: DealTemperature | null) => void | Promise<unknown>;
  /**
   * How much room this card has, from the number of stage columns on screen (issue #182).
   * `compact` is the pre-#182 field set exactly; each wider tier only ADDS. Nothing is ever
   * taken away as the board narrows, so a field a rep learned to look for cannot disappear
   * because they hid one more column.
   */
  density: BoardDensity;
  /** Resolves `owner_id` to a display name — the LIST view's own resolver, so the two agree. */
  ownerName: (ownerId: number | null | undefined) => string;
}) {
  // Both wider tiers show these; only the widest adds the owner. Named rather than inlined
  // three times so the tier boundaries read as one decision instead of three coincidences.
  const roomy = density !== 'compact';

  // Built once and rendered from EITHER branch below — the Won card's #129 swap, or an open
  // card at a wider tier. One expression, so the two surfaces cannot drift on the label, the
  // tooltip or the no-activity wording.
  const lastContact = (
    <span title={deal.last_activity_at
      ? `Most recent logged note or activity: ${formatDate(deal.last_activity_at)}`
      : 'No note or activity has been logged on this deal'}>
      {lastContactLabel(deal.last_activity_at)}
    </span>
  );
  // Colour from the column the card currently sits in (its bucket) rather than
  // deal.stage — during an optimistic drop the bucket updates before the deal's
  // own stage field does, so this keeps the accent correct instantly.
  const color = STAGE_COLORS[columnStage]?.fill || INK_DIM;
  const bg = STAGE_COLORS[columnStage]?.bg || BG_CARD;
  return (
    <div
      role="button"
      // NAMED EXPLICITLY, because a role="button" with no label takes its accessible name from
      // its own contents — which here begin with the selection checkbox's "Select <title>".
      // Screen readers therefore announced this control as "Select Acme renewal, Acme renewal,
      // $600 …": the wrong verb (it OPENS the deal, it does not select it) followed by the
      // title twice. An explicit label also keeps the money and the metadata row out of the
      // name, and carries the ARCHIVED chip's state — the one thing in the subtree worth
      // hearing — rather than losing it with the rest (issue #176).
      aria-label={archived ? `Open ${deal.title} (archived)` : `Open ${deal.title}`}
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
        // An archived card is de-emphasised by COLOUR, never by `opacity` — see the note on
        // the title span below. #83 dimmed the whole card here at 0.55; #119 removed it.
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
        {/* De-emphasised by COLOUR, not by `opacity` (issue #119). #83 dimmed the whole
            card at 0.55, which fades text and backdrop together and drags everything inside
            it below WCAG AA — worst 2.08:1 on the ScorePill, and no opacity below 1.0 fixes
            that, because the pill hues are tuned to sit just over 4.5:1 unfaded. Stepping
            the title to `ink-dim` keeps #83's "dimmed rather than struck through" read
            (the ARCHIVED chip beside it carries the rest) while every pixel stays legible;
            `ink-dim` on a stage-washed deal card is a surface inkContrast.test.ts guards. */}
        {/* `flex: 1` so the title OWNS the row's free space instead of being centred in it.
            The row is `space-between` over up to four children, which distributes leftover
            width BETWEEN them — so a short title on a wide card drifted into the middle of
            the card with a gap on either side. Invisible at the old fixed 288px, where the
            title and the value very nearly filled the row; obvious the moment #182 let a
            column grow to 560. `minWidth: 0` lets a long unbroken title wrap rather than
            push the value off the card, which a `flex-basis: 0` item does not do by default. */}
        <span style={{
          flex: 1, minWidth: 0,
          fontSize: 13, color: archived ? INK_DIM : INK, lineHeight: 1.3,
        }}>{deal.title}</span>
        <span style={{
          fontFamily: FONT_DISPLAY,
          fontSize: 14, color: archived ? INK_DIM : INK, flexShrink: 0,
        }}>${deal.value.toLocaleString()}</span>
      </div>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', fontSize: 11, color: INK_DIM }}>
        {deal.contact_name && <span>{deal.contact_name}</span>}
        {deal.probability > 0 && <span>{deal.probability}%</span>}
        {/* Close date is conditional at `compact` (it was always conditional) and PROMOTED to
            always-visible once there is room (issue #182). The promotion is the point: on a
            focused board an absent close date is a state worth reading — the same rule the
            Won card's "No contact logged" follows (#128) — where on a six-column board an
            extra line on every undated card is just noise. */}
        {(deal.expected_close_date || roomy) && (
          <span>{deal.expected_close_date || 'No close date'}</span>
        )}
        {/* The widest tier only (1-2 stages visible). `ownerName` resolves NULL to
            "Unassigned", which is a real state and renders unconditionally — #128's rule. */}
        {density === 'wide' && <span>{ownerName(deal.owner_id)}</span>}
        {/* Won cards trade the two open-deal nudges for a last-contact line (issue #129).
            The score answers "is this still alive" and the touch count answers "are we working
            it enough to close" — neither question survives the close, and post-sale the board is
            read for a different one: which accounts have gone quiet.

            Keyed off the COLUMN, not `deal.stage`, exactly as the card's own colour is a few
            lines above: a card mid-drop into Won reads right immediately, before the PUT settles.

            Plain text in the row's existing ink — no chip, no `tint()` background and no
            `opacity`, so nothing here owes an entry in `inkContrast.test.ts`'s surface registry
            (#68) or has to clear `hueContrast`'s 4.5:1 (#119). */}
        {columnStage === 'won' ? (
          lastContact
        ) : (
          <>
            <ScorePill score={deal.lead_score} compact />
            <TouchCountPill count={deal.ai_touch_count} />
            {/* An OPEN card gets the same line ALONGSIDE its two nudges once there is room
                (issue #182), which is not a walk-back of #129's trade. That trade is about a
                CLOSED deal, where the two nudges answer questions the close retired; here
                nothing is retired and nothing is swapped out — the line is simply added,
                because "when did we last talk to them" is worth a slot on a focused board and
                is not worth one on a six-column board. */}
            {roomy && lastContact}
          </>
        )}
        {/* The only interactive thing in the metadata row. Inert on an archived deal for the
            same reason the card cannot be dragged or selected (issue #83): it is on the board
            to be found and restored, not worked. */}
        <DealTemperatureIcon
          value={deal.deal_temperature}
          disabled={archived}
          onCycle={onCycleTemperature ? next => onCycleTemperature(deal, next) : undefined}
        />
      </div>
    </div>
  );
}
