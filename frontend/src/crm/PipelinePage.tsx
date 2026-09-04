import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import type { PointerEvent as ReactPointerEvent, MouseEvent as ReactMouseEvent, KeyboardEvent as ReactKeyboardEvent } from 'react';
import { useLocation, useSearchParams } from 'react-router-dom';
import { api, ApiError } from '../core/api/client';
import type { CrmDeal } from '../core/types';
import { DealForm } from './components/DealForm';
import { DealDetailSheet } from './components/DealDetailSheet';
import { ScorePill, TouchCountPill } from './components/badges';
import { STAGE_COLORS, STAGE_ORDER, OPEN_STAGES } from './constants';
import { stageWriteRequest } from './dealStageWrite';
import { IconPlus } from '../shared/icons';
import { useIsMobile } from '../shared/useIsMobile';
import { LoadError } from '../shared/LoadError';
import { toast } from '../shared/toast';
import {
  INK, INK_MUTE, INK_DIM, LINE, LINE_STRONG, BG_CARD, ACCENT,
  FONT_DISPLAY, mono, formatNumber, inputStyle, tint,
} from '../shared/styles';
import { pageHeading, btnPrimary, btnSecondary, btnSmall, stageCard } from './styles';
import { KanbanBoard, type MoveEvent } from '../shared/dnd';
import PipelineFilterBar from './components/PipelineFilterBar';
import {
  type PipelineFilterState, type AdvancedFilters,
  EMPTY_FILTER_STATE, dealMatchesAdvanced, hasAdvanced, isArchivedDeal,
  loadFilterState, saveFilterState,
} from './pipelineFilters';
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
  const [editDeal, setEditDeal] = useState<CrmDeal | null>(null);
  const [selectedDeal, setSelectedDeal] = useState<CrmDeal | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  // Every navigation gets a fresh key, including one to the URL already showing. That is
  // what makes following the SAME deep link twice a distinguishable event (issue #145) —
  // the parsed id alone cannot tell a second click from no click at all.
  const location = useLocation();
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
    if (selectedDeal !== null && selectedDeal.id === linkOpenedDeal) {
      setSelectedDeal(null);
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
    setSelectedDeal(deepLinkedDeal);
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
    setSelectedDeal(noticedDealNowOnBoard);
    setLinkOpenedDeal(noticedDealNowOnBoard.id);
  }

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
  // ...and whether a deferred load must still REPORT a failure when it replays. Set when
  // the deferred load was user-initiated (an Archived-facet change): replaying it as a
  // plain background refresh would swallow its error, and under "Archived only" a swallowed
  // error renders an empty board that reads as "you have no archived deals".
  //
  // Only the error reporting is carried over, NOT the spinner: `loading` early-returns the
  // spinner INSTEAD OF the page, and a replay fires whenever the last write happens to
  // settle — so replaying loudly would blank an open DealForm or detail sheet mid-edit and
  // lose everything the user had typed. Reporting is what the user needs; the spinner
  // belongs to the interaction that asked for it, and that interaction is over.
  const pendingRefreshReportErrors = useRef(false);
  // Whether a payload has ever rendered. A failed load with no data yet already reports
  // itself through `LoadError`; toasting as well just stacks a second message on top of
  // the error screen, once per retry click.
  const hasLoadedOnce = useRef(false);
  // `load` referenced by the deferral path below, which has to re-fire it. A ref because
  // the callback cannot name itself, and assigned in an effect because a ref write during
  // render is a build-blocking lint error under this repo's react-hooks ruleset.
  const loadRef = useRef<(silent?: boolean, opts?: { reportErrors?: boolean }) => Promise<boolean>>(
    () => Promise.resolve(false),
  );
  // Load generation (issue #83) — see `load`. Distinct from `writeGen`: that one guards a
  // refresh against a racing WRITE; this one guards a load against a newer LOAD, which the
  // Archived facet made reachable by changing the request itself.
  const loadGen = useRef(0);
  // A generation for NON-SILENT loads only. Owns the spinner; see `load`.
  const spinnerGen = useRef(0);

  // Archived deals are swept out of the board payload server-side, so the Archived facet
  // is the one facet that must widen the FETCH as well as filter. Derived as a boolean on
  // purpose: the refetch keys off this rather than on `advanced`, or every keystroke and
  // every unrelated facet change would refetch the whole board. 'include' and 'only' need
  // the same payload — the difference between them is purely the client predicate.
  const includeArchived = advanced.archived !== null;
  const includeArchivedRef = useRef(includeArchived);

  // THE one definition of "wake the deferred load", consumed by all three places that can
  // wake one: a settling single-deal write, a settling bulk move, and `load`'s own
  // self-replay. It was three copies, and the rule they encode is subtle enough that a
  // future edit to one of them would very likely not be made to the other two: replay
  // SILENTLY (a replay fires whenever a write happens to settle, and `loading` returns the
  // spinner INSTEAD of the page — taking the screen at that moment blanks an open form
  // mid-edit) while still carrying the original request's error REPORTING across, so a
  // user-initiated load that got deferred does not have its failure swallowed.
  // No-ops when nothing is pending, so callers only have to know that writes have settled.
  const replayDeferredLoad = useCallback(() => {
    if (!pendingRefresh.current) return;
    pendingRefresh.current = false;
    const reportErrors = pendingRefreshReportErrors.current;
    pendingRefreshReportErrors.current = false;
    void loadRef.current(true, { reportErrors });
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
    // A background refresh normally stays quiet, but a REPLAYED user action must still
    // report its failure even though it no longer takes the spinner — see
    // `pendingRefreshReportErrors`.
    const reportErrors = !isSilent || opts?.reportErrors === true;
    // NO load may clobber an optimistic drag — not just a silent one. If a stage write is
    // already in flight, don't even fire the GET; defer it (moveDealStage re-fires it once
    // writes settle, and it reads the CURRENT facet from the ref, so a deferred refresh
    // still widens). This used to be silent-only, which was safe while every load was a
    // refresh of the same content set; the Archived facet made a load a user-initiated
    // action that could land a pre-write board on top of a drag the user had just made.
    if (pendingWrites.current > 0) {
      pendingRefresh.current = true;
      if (reportErrors) pendingRefreshReportErrors.current = true;
      return false;
    }
    const startGen = writeGen.current;
    // A SECOND generation, for loads rather than writes (issue #83). `writeGen` answers
    // "did a write invalidate this payload?"; this answers "is a newer LOAD already in
    // flight?" — which only became reachable when the Archived facet started changing the
    // request itself, since two quick facet flips can otherwise resolve out of order and
    // leave the board showing the wrong content set.
    const myLoad = ++loadGen.current;
    // Mirror the generation into state so the deep-link resolution can read it in render.
    setBoardLoads(b => ({ ...b, started: myLoad }));
    // The SPINNER gets its OWN generation, bumped only by non-silent loads. It cannot ride
    // `loadGen`, which silent refreshes bump too: gating the reset on that would strand the
    // spinner forever once a silent refresh started after a non-silent one — the loser
    // skips the reset and the winner, being silent, never touches `loading`. Nor can it be
    // a simple in-flight COUNT, which would let one hung request pin the spinner even after
    // a newer load had already painted the board. Newest-non-silent-wins is the only rule
    // that is correct in both.
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
        // A deferred load is normally replayed by the settling write's `finally`. But the
        // write that invalidated this payload may have STARTED AND FINISHED entirely
        // inside this GET's flight, in which case its finally already ran and saw nothing
        // pending — so no one is left to replay us and the load is simply dropped. Re-fire
        // it here. That was a silent staleness bug before #83; now that a facet change can
        // be the deferred load, it would read as the board ignoring the click outright.
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
      hasLoadedOnce.current = true;
      dealConfirmedStage.current = new Map(d.deals.map(deal => [deal.id, deal.stage]));
      // Intersect the selection with the deals this payload says are LIVE. Masking an
      // archived deal in `bulkIds` and on its card is not enough: the id stays in the Set,
      // so once the deal is restored somewhere else (the assistant, another tab) the next
      // payload brings it back ALREADY SELECTED, joining a bulk move nobody picked it for.
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
      // data stays null → LoadError below. But once data EXISTS a failure is invisible:
      // the previous payload keeps rendering, and under "Archived only" that means an
      // empty board — indistinguishable from "you have no archived deals". Say so.
      // (Silent refreshes stay quiet; being unobtrusive is their whole contract.)
      if (reportErrors && loadGen.current === myLoad && hasLoadedOnce.current) {
        toast.error('Failed to load deals.');
      }
    }
    finally { if (!isSilent && spinnerGen.current === mySpinner) setLoading(false); }
    return false;
    // `replayDeferredLoad` is a stable useCallback, so naming it here costs nothing
    // and keeps `load`'s identity stable — which the mount effect below depends on.
  }, [replayDeferredLoad]);

  useEffect(() => { loadRef.current = load; }, [load]);

  // Mount, and again whenever the Archived facet changes WHICH deals the server should
  // send. The ref is synced here rather than during render (a render-phase ref write is a
  // lint error under this repo's react-hooks ruleset) and, being in the same effect,
  // always lands before the load it triggers. A full non-silent load on purpose: the
  // board's content set is being replaced wholesale, and the spinner is the honest signal
  // for that — a silent swap would leave the old set on screen looking authoritative.
  useEffect(() => {
    includeArchivedRef.current = includeArchived;
    queueMicrotask(load);
  }, [includeArchived, load]);

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
  // `lostReason` (issue #128) only changes WHICH request this op issues — it is captured
  // per operation alongside `seq`, inside the same per-deal promise chain, so every
  // invariant above is untouched: the chain still serializes, the sequence check still
  // discards a superseded response, and rollback still reads the confirmed stage. Both
  // endpoints return the same `get_deal` projection, so the reconcile merge is unchanged.
  const moveDealStage = useCallback((
    deal: CrmDeal, toStage: string, fromStage: string, lostReason?: string,
  ) => {
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
        const { path, init } = stageWriteRequest(dealId, toStage, lostReason);
        const updated = await api<CrmDeal>(path, init);
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
        // last_activity_at even though the stage PUT was in flight at dismissal time. Since
        // #83 an Archived-facet change can be the deferred load too; it re-fires silently
        // but reads the CURRENT facet from the ref, so it still widens the board.
        if (pendingWrites.current === 0) replayDeferredLoad();
      }
    });
    dealWriteChain.current.set(dealId, run);
  }, [replayDeferredLoad]);

  // Drag handler. Resolves immediately so the Kanban hook ends its gesture and
  // re-syncs from `data` right away; persistence + rollback are data-driven (via
  // moveDealStage), never snapshot-driven, so this never needs to throw.
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
    moveDealStage(event.item, to, from);
    return Promise.resolve();
  }, [moveDealStage]);

  // Detail-sheet handler (Mark Won / Lost) — optimistic move + close the sheet. Also refresh
  // the board (like onClose) so an in-sheet note/activity logged before this dismissal lands
  // its last_activity_at; if a stage move fired, the refresh defers until that PUT settles.
  const updateDealStage = useCallback((deal: CrmDeal, stage: string, lostReason?: string) => {
    if (deal.stage !== stage) moveDealStage(deal, stage, deal.stage, lostReason);
    setSelectedDeal(null);
    load(true);
  }, [moveDealStage, load]);

  // A deal was restored from the detail sheet (issue #83). The sheet hands up the row the
  // server RETURNED, which is patched into `data` in place — deliberately not a refetch:
  // `load(true)` is silent and can fail invisibly, which would leave the board still
  // showing the deal as archived after a restore the server actually performed. Patching
  // the authoritative row is why POST /restore returns the deal instead of {"ok": true}.
  // The board then re-derives everything: under 'only' the predicate drops it, under
  // 'include' it becomes live, draggable and selectable.
  const restoreDeal = useCallback((restored: CrmDeal) => {
    // Invalidate any load already in flight. Without this, a silent refresh that STARTED
    // before the restore resolves afterwards, passes the generation check, and writes the
    // deal back to archived — undoing a write the server has already committed.
    loadGen.current++;
    setData(prev => (prev
      ? {
        ...prev,
        // MERGE, never replace. `POST /restore` returns `get_deal`'s projection, which is
        // narrower than the board's: `get_pipeline` also derives `last_activity_at`, and a
        // wholesale swap would drop it and drop the restored deal into the "no activity
        // logged" bucket of the Deal-activity facet.
        deals: prev.deals.map(d => (d.id === restored.id ? { ...d, ...restored } : d)),
      }
      : prev));
    dealConfirmedStage.current.set(restored.id, restored.stage);
    // Restoring is not a selection gesture. An id can still be sitting in `bulkSelected`
    // from before the deal was archived — masked everywhere while it stays archived — and
    // would otherwise silently rejoin the next bulk move the moment it came back.
    setBulkSelected(prev => {
      if (!prev.has(restored.id)) return prev;
      const next = new Set(prev);
      next.delete(restored.id);
      return next;
    });
    setSelectedDeal(null);
    // Then refresh in the background. The patch above is what makes the board CORRECT — it
    // deliberately does not depend on this landing — but a restore closes the sheet the same
    // way `onClose` does, and that path refreshes so an in-sheet note reaches the board's
    // derived `last_activity_at`. Without it, restoring a deal you just logged a note on
    // leaves it in the "No activity logged" bucket. It also replaces the in-flight load the
    // generation bump above just discarded.
    queueMicrotask(() => { void loadRef.current(true); });
  }, []);

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
    // `!isFiltering` alone is no longer sufficient to skip the predicate (issue #83): after
    // the Archived facet is CLEARED, archived rows are still in `data` until the narrowing
    // refetch lands, and the predicate's null branch is what hides them in the meantime.
    // The `.some` keeps the by-reference fast path for the overwhelmingly common case.
    if (!isFiltering && !deals.some(isArchivedDeal)) return deals;
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

  // The live half of the visible set. Archived deals are shown as CARDS (that is the whole
  // point of the facet) but are excluded from everything that means money or action:
  // the open-pipeline header, the per-column $ totals, and the bulk payload. The server
  // draws the same line — `stage_summary` keeps the sweep even when the deals query
  // doesn't — so the two cannot disagree about what counts. Reference-stable when nothing
  // is archived, so the unfiltered board still doesn't churn.
  const liveFilteredDeals = useMemo(
    () => (filteredDeals.some(isArchivedDeal)
      ? filteredDeals.filter(d => !isArchivedDeal(d))
      : filteredDeals),
    [filteredDeals],
  );

  // The issue's "bulk actions operate on the currently filtered set" invariant, enforced
  // ONCE: `filteredDeals` already embeds #21's facet predicate (including the stage facet
  // that hides whole columns), and this single intersection feeds BOTH the bar's count and
  // the apply payload — so what the operator is told and what the server is sent cannot
  // disagree, even if the selection changed since the last render.
  // ...intersected with the LIVE subset (issue #83): the server refuses a stage change on
  // an archived deal (`_classify_deal_update` raises), so including one could only ever
  // produce a per-deal error in the bulk response. Better never to offer it.
  const bulkIds = useMemo(
    () => applicableBulkIds(bulkSelected, liveFilteredDeals),
    [bulkSelected, liveFilteredDeals],
  );

  const applyBulkMove = useCallback(async (toStage: string) => {
    if (bulkPendingRef.current || !toStage) return;
    const ids = applicableBulkIds(bulkSelected, liveFilteredDeals);
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
      if (pendingWrites.current === 0) replayDeferredLoad();
    } finally {
      if (!writeSettled) pendingWrites.current--;
      bulkPendingRef.current = false;
      setBulkPending(false);
    }
  }, [bulkSelected, liveFilteredDeals, deals, clearSelection, load, replayDeferredLoad]);

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

  // Per-stage value totals for the column headers — precomputed once per data
  // change so drag re-renders (which fire at pointer-move frequency) don't re-reduce
  // every column on every frame. Archived deals contribute NO value (issue #83) even
  // though their cards are in the column: the column *count* describes what you can see,
  // the $ describes pipeline, and only the latter has to match the server's aggregates.
  const columnTotals = useMemo(() => {
    const totals: Record<string, number> = {};
    for (const stage of STAGE_ORDER) {
      totals[stage] = (grouped[stage] || [])
        .reduce((s, d) => (isArchivedDeal(d) ? s : s + (d.value || 0)), 0);
    }
    return totals;
  }, [grouped]);

  // Open-pipeline $/count reflect the FILTERED set so the header describes what's shown
  // (a "showing X of Y" annotation below signals when a filter is narrowing the board) —
  // minus archived deals, which are visible but are not open pipeline (issue #83).
  const { openTotal, openCount } = useMemo(() => {
    const open = liveFilteredDeals.filter(d => OPEN_STAGES.includes(d.stage));
    return { openTotal: open.reduce((s, d) => s + (d.value || 0), 0), openCount: open.length };
  }, [liveFilteredDeals]);

  // Dashboard deep-link (/crm/pipeline?stage=X): once `data` has rendered the columns (refs
  // populated), scroll the requested column into view, then clear the `stage` param
  // (preserving any others). Reactive per target — `scrolledStage` guards a re-scroll for the
  // same stage; the render-time guard above already cleared filters so every column is mounted.
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
    queueMicrotask(() => {
      void load(true).then(applied => {
        // A refresh that never landed leaves the verdict at `refresh` forever, and this
        // effect will not run again — so the link would sit armed until some unrelated load
        // minutes later (closing another card's sheet, a facet flip) happened to satisfy it
        // and popped a sheet open with no gesture toward it. Retire it instead: the page
        // stays silent, which is the documented trade, and following the link again retries
        // because the resolution keys off the navigation.
        //
        // `pendingRefresh` means the load was DEFERRED behind a write, not lost — that one
        // replays on its own and must stay armed. And setState here is inside a promise
        // continuation, not synchronously in the effect body, which is what the ruleset bans.
        if (!applied && !pendingRefresh.current) setHandledDeepLink(target);
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

  return (
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
        // Otherwise PER-CARD (issue #83): an archived deal is on the board to be found and
        // restored, not to be worked — the server refuses a stage change on one, so a drag
        // could only ever animate and revert. `isArchivedDeal` is module-level, so this is
        // a stable identity rather than a per-render closure.
        dragDisabled={isMobile || bulkPending ? true : isArchivedDeal}
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
                // Select-all collects LIVE ids only (issue #83) — an archived card has no
                // checkbox, so including it would select something the operator cannot see
                // selected. An all-archived column therefore shows no header checkbox,
                // which is right: nothing there is bulk-actionable.
                columnDealIds={isMobile ? [] : colDeals.filter(d => !isArchivedDeal(d)).map(d => d.id)}
                selectedIds={bulkSelected}
                onToggleColumn={toggleColumn}
              />
              {children}
            </div>
          );
        }}
        renderCard={(deal, columnId) => {
          // An archived card is inert: no checkbox, and never rendered as selected —
          // a deal archived elsewhere (the assistant, a merge) could otherwise still be
          // in `bulkSelected` from before, showing selected styling with no way to clear
          // it. It stays clickable, because opening it is how you reach Restore.
          const archived = isArchivedDeal(deal);
          return (
            <DealBoardCard
              deal={deal} columnStage={String(columnId)}
              // Opening a card by hand takes the sheet away from whatever link last owned
              // it, so a later link supersedes only what a link actually put there.
              onOpen={() => { setSelectedDeal(deal); setLinkOpenedDeal(null); }}
              selectable={!isMobile && !archived}
              isSelected={!archived && bulkSelected.has(deal.id)}
              onToggleSelect={() => toggleSelect(deal.id)}
              archived={archived}
            />
          );
        }}
        renderEmptyColumn={() => (
          <div style={{
            fontSize: 12, color: INK_DIM, textAlign: 'center',
            padding: '16px 8px', border: `1px dashed ${LINE}`, borderRadius: 6,
          }}>No deals</div>
        )}
      />
      )}

      {showCreate && <DealForm onClose={() => setShowCreate(false)} onSaved={() => { setShowCreate(false); load(); }} />}
      {editDeal && <DealForm deal={editDeal} onClose={() => setEditDeal(null)} onSaved={() => { setEditDeal(null); setSelectedDeal(null); load(); }} />}

      {selectedDeal && (
        <DealDetailSheet
          key={selectedDeal.id}
          deal={selectedDeal}
          isMobile={isMobile}
          // Silent-refresh the board on close so an in-sheet note/activity log updates the
          // deal's last_activity_at (and touch count) without a spinner flash — closes the
          // "filter stale deals → log a touch → it leaves the stale bucket" loop.
          onClose={() => { setSelectedDeal(null); setLinkOpenedDeal(null); load(true); }}
          onEdit={(d) => { setSelectedDeal(null); setEditDeal(d); }}
          onStageChange={updateDealStage}
          onRestored={restoreDeal}
        />
      )}
    </div>
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
        ...(isSelected ? { borderColor: ACCENT, boxShadow: `0 0 0 1px ${tint(ACCENT, 40)}` } : {}),
        // Dimmed rather than struck through: a card is mostly whitespace, so opacity plus
        // the explicit chip below reads faster than a line through the title would.
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
