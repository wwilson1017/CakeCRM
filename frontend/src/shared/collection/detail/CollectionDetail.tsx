/**
 * The collection layer's detail surface — a thin orchestrator over
 * `shared/overlay/DetailModal`. The modal owns presentation (takeover vs centred,
 * focus trap, Escape stack, backdrop gesture); this file owns three things it cannot:
 *
 *  • **The close CONTRACT.** Every leave path — Escape, Back, ×, backdrop, ‹ › nav — asks
 *    permission (`onRequestClose(reason)`, plus any body-registered guard) and the LAYER
 *    performs the result: close ⇒ `onSelect(null)`, nav ⇒ `onSelect(targetId)`. Permission
 *    and action are separated so an app handler and the layer can never both close, or
 *    neither. Default policy with no handler: allow everything except backdrop.
 *
 *  • **‹ › navigation** over `visibleOrder` — the order the user is LOOKING at in the current
 *    view (render caps deliberately excluded; see visibleOrder's docstring). Arrows disable
 *    at the ends, and entirely when the open record is not in the visible set (filtered out
 *    after opening, voided under 'hide', a deep link) — disabling beats guessing.
 *
 *  • **Record identity.** The body is keyed by `getItemId`, so ‹ › nav REMOUNTS it — per-record
 *    draft state must never leak across records (the blueprint `key={uuid}` lesson). A
 *    body-registered close guard is bound to the id it was registered under, so record B can
 *    never inherit record A's guard even transiently.
 *
 * Deep links ride `DetailConfig.loadById`: an id not in the canonical set renders from the
 * fetch (with a loading / retryable-error body) instead of waiting for assembly to reach it.
 *
 * That fetch path is also why this file keeps a ONE-RECORD memory (`lastResolvedRef`). An open
 * record can LEAVE the canonical array while the user is reading it — CRM's `items` exclude
 * manually-hidden stages, so marking a deal Won with the Won stage hidden drops it out; a
 * sibling surface's request leaves its closed-window set the same way; a background refresh can do it
 * to anyone. Without the memory the panel the user is looking at collapses to the "Loading…"
 * shell and, because the body is keyed by id, REMOUNTS — scroll position, galleries, custom-field
 * and chatter drafts all destroyed on the panel's primary gesture. So while a fetch is in flight
 * for the SAME id we keep rendering the last record we resolved and let the fetch swap in the
 * fresh copy when it lands. The memory is keyed to `selectedId` (never read for another record,
 * so it cannot leak across the keyed remount) and cleared when the panel closes, so reopening a
 * record later still fetches. A COLD deep link — nothing ever resolved for this id — still shows
 * the loading shell, and still reaches the retryable error body.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { IconChevronLeft, IconChevronRight } from '../../icons';
import DetailModal from '../../overlay/DetailModal';
import visibleOrder from '../visibleOrder';
import type {
  CollectionConfig,
  CollectionDetailProps,
  CollectionState,
  DetailCloseGuard,
  DetailCloseReason,
  DetailRenderContext,
  DragPolicy,
} from '../types';

/**
 * Only TERMINAL outcomes are stored. "Loading" is derived (`needsFetch` with no terminal result
 * matching the current id + attempt), which keeps the fetch effect free of a synchronous
 * `setState` that would cascade a render on every open. `nonce` is what a Retry bumps, so a
 * previous attempt's error stops matching and the loading shell shows while the retry is in
 * flight — the job the old explicit 'loading' write used to do.
 */
type FetchState<T> =
  | { id: string | number; nonce: number; status: 'error' }
  | { id: string | number; nonce: number; status: 'ready'; item: T };

/**
 * Invokes the body render prop as a COMPONENT rather than as a call in `CollectionDetail`'s own
 * render. `ctx` carries `registerCloseGuard`, which writes a ref; calling the prop inline would
 * make that a ref access during render. Rendering it here keeps the registration where it
 * belongs — in the body's own effects — and costs one trivial component.
 */
function DetailBody<T>({
  render,
  item,
  ctx,
}: {
  render: (item: T, ctx: DetailRenderContext) => ReactNode;
  item: T;
  ctx: DetailRenderContext;
}) {
  return <>{render(item, ctx)}</>;
}

export default function CollectionDetail<T>({
  config,
  state,
  items,
  selectedId,
  onSelect,
  detail,
  kanbanColumnIds = [],
  navOrder,
}: {
  config: CollectionConfig<T, DragPolicy>;
  state: CollectionState<T>;
  items: readonly T[];
  selectedId: string | number | null;
  onSelect: (id: string | number | null) => void;
  detail: CollectionDetailProps<T>;
  /** The app's ordered kanban column ids — required for column-major ‹ › order on the board. */
  kanbanColumnIds?: readonly (string | number)[];
  /**
   * Override for the ‹ › order.
   *
   * `visibleOrder` can only describe the views the LAYER renders. A page may legitimately
   * render a record set outside `CollectionView` while still opening THIS detail —
   * a sibling surface's dashboard-filtered branch draws a different query's rows, archived
   * ones included. In that state the computed order describes a set the user is not looking
   * at, so navigation either walks the wrong records or, when the open row is absent from it
   * entirely, disables both arrows. Only the page knows that order, so the page supplies it.
   *
   * Same contract as the computed order: the whole filtered+sorted set in the order the user
   * sees, render caps excluded. Omitting it (or `undefined`) uses `visibleOrder`; `[]` is a
   * real and different answer meaning "nothing to navigate".
   */
  navOrder?: readonly (string | number)[];
}) {
  const detailConfig = config.detail;

  const canonical = useMemo(
    () =>
      selectedId == null
        ? undefined
        : items.find(item => config.getItemId(item) === selectedId),
    [items, selectedId, config],
  );

  // Deep-link fetch. Self-invalidating by id (render only trusts a FetchState whose id
  // matches selectedId), so no clearing effect is needed when the record changes.
  const [fetched, setFetched] = useState<FetchState<T> | null>(null);
  const loadById = detailConfig?.loadById;
  const needsFetch = selectedId != null && canonical === undefined && loadById !== undefined;
  // Bumping re-runs the fetch effect for the SAME id after an error.
  const [retryNonce, setRetryNonce] = useState(0);
  useEffect(() => {
    if (!needsFetch || selectedId == null || !loadById) return;
    let stale = false;
    loadById(selectedId).then(
      item => {
        if (!stale) setFetched({ id: selectedId, nonce: retryNonce, status: 'ready', item });
      },
      () => {
        if (!stale) setFetched({ id: selectedId, nonce: retryNonce, status: 'error' });
      },
    );
    return () => {
      stale = true;
    };
  }, [needsFetch, selectedId, loadById, retryNonce]);

  /** A terminal fetch result that belongs to the record and attempt being rendered right now. */
  const settled =
    fetched && fetched.id === selectedId && fetched.nonce === retryNonce ? fetched : null;

  /** The record the CURRENT render can prove: canonical, else a completed fetch for this id. */
  const fresh = canonical ?? (settled?.status === 'ready' ? settled.item : undefined);

  // The one-record memory (see the file docstring). Kept as state and adjusted DURING render —
  // React's documented alternative to a syncing effect — so no cascading commit is needed.
  //
  // INVARIANT THIS RELIES ON: `items` holds stable element identities across renders, changing
  // only when the data actually changes. The adjustment below settles in one extra render
  // because the guard compares `lastResolved.item` to `fresh`; a consumer that rebuilt its item
  // objects on every render would make that comparison never converge and React would report
  // "too many re-renders". That invariant is not new here — `canonical`'s `useMemo`, the keyed
  // body remount, and `useCollectionState`'s memos all already depend on it, and the layer's own
  // `usePageAssembly` satisfies it — but it is written down because this is the one place where
  // breaking it fails loudly rather than merely wasting work.
  //
  // The write is guarded on `fresh` being present, which is what makes it equivalent to the
  // effect form it replaces: when `fresh` is undefined (the record just left `items`) nothing is
  // written, so `remembered` below still holds what the previous commit painted, which is
  // exactly what that case needs. Cleared on close so a later reopen of the same id is a genuine
  // fetch rather than a resurrection of a record that may be long gone.
  const [lastResolved, setLastResolved] = useState<{ id: string | number; item: T } | null>(null);
  if (selectedId == null) {
    if (lastResolved !== null) setLastResolved(null);
  } else if (
    fresh !== undefined &&
    (lastResolved === null || lastResolved.id !== selectedId || lastResolved.item !== fresh)
  ) {
    setLastResolved({ id: selectedId, item: fresh });
  }

  // Keyed to `selectedId`: an entry minted for another record is never read, so the memory can
  // never survive the keyed body remount that keeps per-record drafts apart.
  const remembered =
    lastResolved !== null && lastResolved.id === selectedId ? lastResolved.item : undefined;
  const resolved = fresh ?? remembered;

  // Body-registered close guard, BOUND to the record it was registered under. Written only from
  // the registration callback and read only from `request()` (an event handler) — never during
  // render, which is why the body receives `ctx` through the `<DetailBody>` component below
  // rather than by calling the render prop inline here.
  const guardRef = useRef<{ id: string | number; guard: DetailCloseGuard } | null>(null);
  const registerCloseGuard = useCallback(
    (guard: DetailCloseGuard) => {
      if (selectedId != null) guardRef.current = { id: selectedId, guard };
      return () => {
        if (guardRef.current?.guard === guard) guardRef.current = null;
      };
    },
    [selectedId],
  );
  const ctx: DetailRenderContext = useMemo(
    () => ({ registerCloseGuard }),
    [registerCloseGuard],  );

  // One leave request in flight at a time — a slow async guard must not queue up a second
  // dismissal behind the dialog it is still deciding about.
  const pendingRef = useRef(false);
  const request = async (reason: DetailCloseReason, targetId: string | number | null) => {
    if (pendingRef.current) return;
    pendingRef.current = true;
    try {
      const entry = guardRef.current;
      if (entry && entry.id === selectedId && !(await entry.guard(reason))) return;
      const appGuard = detail.onRequestClose;
      const allowed = appGuard ? await appGuard(reason) : reason !== 'backdrop';
      if (!allowed) return;
      onSelect(targetId);
    } finally {
      pendingRef.current = false;
    }
  };

  const columnIds = kanbanColumnIds;
  const computedOrder = useMemo(
    () => visibleOrder(state.view, config, state.visibleItems, state.kanbanItems, columnIds),
    [state.view, config, state.visibleItems, state.kanbanItems, columnIds],
  );
  // A page rendering its records outside `CollectionView` owns the order the user sees; the
  // computed one would describe a different set entirely (see the `navOrder` prop docs).
  const order = navOrder ?? computedOrder;

  if (selectedId == null || !detailConfig) return null;

  const index = order.indexOf(selectedId);
  const prevId = index > 0 ? order[index - 1] : null;
  const nextId = index >= 0 && index < order.length - 1 ? order[index + 1] : null;

  const navButton = (
    label: string,
    Icon: typeof IconChevronLeft,
    targetId: string | number | null,
  ) => (
    <button
      type="button"
      aria-label={label}
      disabled={targetId == null}
      onClick={() => {
        if (targetId != null) void request('nav', targetId);
      }}
      className="rounded p-1 text-muted hover:text-charcoal disabled:opacity-30 disabled:hover:text-muted"
    >
      <Icon size={18} />
    </button>
  );

  const headerActions = (
    <div className="flex items-center">
      {navButton('Previous record', IconChevronLeft, prevId)}
      {navButton('Next record', IconChevronRight, nextId)}
    </div>
  );

  const shell = (title: string, subtitle: string | null, body: ReactNode) => (
    <DetailModal
      title={title}
      subtitle={subtitle ?? undefined}
      onClose={source => void request(source ?? 'button', null)}
      onBackdropClick={() => void request('backdrop', null)}
      headerActions={headerActions}
    >
      {body}
    </DetailModal>
  );

  if (resolved !== undefined) {
    return shell(
      detailConfig.getTitle(resolved),
      detailConfig.getSubtitle?.(resolved) ?? null,
      // Keyed by record id — the remount on ‹ › nav is load-bearing (draft state, guards).
      <div key={String(selectedId)}>
        <DetailBody render={detail.render} item={resolved} ctx={ctx} />
      </div>,
    );
  }

  // Reached only when NOTHING is renderable for this id — no canonical row, no completed fetch,
  // and no memory. A remembered record deliberately outranks this: "Record unavailable" over a
  // panel the user is already reading would be both false (the record exists) and destructive
  // (the keyed body remounts). A cold deep link is what this branch is for, and still gets it.
  if (settled?.status === 'error') {
    return shell(
      'Record unavailable',
      null,
      <div className="flex flex-col items-start gap-3 p-6 text-sm text-muted">
        <p>This record could not be loaded.</p>
        <button
          type="button"
          onClick={() => setRetryNonce(n => n + 1)}
          className="rounded-lg border border-line px-3 py-1.5 text-charcoal hover:bg-sand"
        >
          Retry
        </button>
      </div>,
    );
  }

  if (needsFetch) {
    return shell(
      'Loading…',
      null,
      <div className="p-6 text-sm text-muted">Loading record…</div>,
    );
  }

  // No canonical row, no loader: nothing renderable (a stale deep link on a config without
  // loadById). Rendering nothing beats an empty modal.
  return null;
}
