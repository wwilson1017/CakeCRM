/**
 * The config-driven collection shell — one component that renders an app's
 * whole record surface from a `CollectionConfig` + `useCollectionState`: the shared bar
 * (`shared/search`), the Board/List/Cards switcher (`shared/listview`), the active view, the
 * visible-only bulk bar, the assembly progress surface, and the detail orchestration.
 *
 * The shell is WIRING, not logic: every rule (filter semantics, drag gate, persistence,
 * void policy) lives in the hook or the pure modules; every pixel of the bar and the table
 * lives in the kit. What the shell owns is the translation — facet defs → `FacetGroup`s and
 * disclosure-panel controls, sort config → `SortControl` props, selection → the
 * visible-intersection bulk bar — and the rule that ALL five facet kinds surface in the
 * bar's active-count / chip-row / clear-all ("filtered but you cannot tell by what" is the
 * failure the bar exists to prevent; `toolbarExtras` is actions only, never filters).
 */
import { useMemo, type ReactNode } from 'react';
import { IconX } from '../icons';
import { ChipButton, SearchFilterBar, toggleValue } from '../search';
import type { FacetGroup, FacetOption } from '../search';
import { ViewSwitcher } from '../listview';
import { deriveFacetOptions, selectionActive } from './facets';
import { restingSort } from './useCollectionState';
import EmptyState from './EmptyState';
import KanbanView from './views/KanbanView';
import CollectionListView from './views/CollectionListView';
import CardsView from './views/CardsView';
import CollectionDetail from './detail/CollectionDetail';
import type {
  CollectionViewKind,
  CollectionViewProps,
  FacetDef,
  RangeValue,
  VoidedFilter,
} from './types';

/** Removable chip for a non-group facet (range) in the collapsed row. Module scope — a
 *  component declared during render remounts its subtree every render. */
function ActiveChip({ label, onRemove }: { label: string; onRemove: () => void }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-sand py-1 pl-2.5 pr-1 text-xs text-charcoal">
      {label}
      <button
        type="button"
        onClick={onRemove}
        aria-label={`Remove filter ${label}`}
        className="inline-flex h-4 w-4 items-center justify-center rounded-full text-muted hover:bg-line/50 hover:text-charcoal"
      >
        <IconX className="h-3 w-3" aria-hidden="true" />
      </button>
    </span>
  );
}

function rangeChipLabel(label: string, r: RangeValue, format?: (value: number) => string): string {
  const fmt = format ?? String;
  if (r.min !== null && r.max !== null) return `${label}: ${fmt(r.min)}–${fmt(r.max)}`;
  if (r.min !== null) return `${label}: ≥ ${fmt(r.min)}`;
  return `${label}: ≤ ${fmt(r.max!)}`;
}

const VOIDED_OPTIONS: FacetOption[] = [
  { value: 'hide', label: 'Hide voided' },
  { value: 'only', label: 'Voided only' },
];

export default function CollectionView<T, C = unknown>({
  config,
  state,
  items,
  selectedId = null,
  onSelect,
  selection,
  kanban,
  cards,
  detail,
  toolbarExtras,
  searchPlaceholder,
  searchResetNonce,
  loading,
}: CollectionViewProps<T, C>) {
  const facets = useMemo(() => config.facets ?? [], [config]);
  const noun = config.itemNoun?.plural ?? 'items';

  // Option derivation scans the whole set per multi facet — memoized on the data, unlike the
  // group closures below, which are cheap and close over live state on purpose.
  const derivedOptions = useMemo(() => {
    const map = new Map<string, FacetOption[]>();
    for (const def of facets) {
      if (def.kind === undefined || def.kind === 'multi') {
        map.set(def.key, deriveFacetOptions(items, def));
      }
    }
    return map;
  }, [facets, items]);

  const groups: FacetGroup[] = [];
  const panelExtras: ReactNode[] = [];
  const chipExtras: ReactNode[] = [];
  for (const def of facets) {
    const raw = state.facetSelections[def.key];
    switch (def.kind) {
      case 'single': {
        const value = (raw ?? null) as string | number | null;
        groups.push({
          key: def.key,
          label: def.label,
          options: def.options,
          selected: value === null ? [] : [value],
          onToggle: v => state.setFacet(def.key, value === v ? null : v),
        });
        break;
      }
      case 'boolean': {
        const active = raw === true;
        panelExtras.push(
          <ChipButton
            key={`bool:${def.key}`}
            label={def.label}
            active={active}
            onClick={() => state.setFacet(def.key, !active)}
          />,
        );
        if (active) {
          chipExtras.push(
            <ActiveChip
              key={`bool:${def.key}`}
              label={def.label}
              onRemove={() => state.setFacet(def.key, false)}
            />,
          );
        }
        break;
      }
      case 'range': {
        const r = (raw ?? { min: null, max: null }) as RangeValue;
        const setBound = (bound: 'min' | 'max', text: string) => {
          const parsed = text === '' ? null : Number(text);
          state.setFacet(def.key, {
            ...r,
            [bound]: parsed === null || Number.isNaN(parsed) ? null : parsed,
          });
        };
        panelExtras.push(
          <div key={`range:${def.key}`}>
            <div className="mb-1.5 text-xs font-medium text-charcoal">{def.label}</div>
            <div className="flex items-center gap-2">
              <input
                type="number"
                value={r.min ?? ''}
                onChange={e => setBound('min', e.target.value)}
                placeholder="Min"
                aria-label={`${def.label} minimum`}
                className="w-24 rounded border border-line bg-cream px-2 py-1 text-xs text-charcoal placeholder:text-muted"
              />
              <span className="text-xs text-muted">to</span>
              <input
                type="number"
                value={r.max ?? ''}
                onChange={e => setBound('max', e.target.value)}
                placeholder="Max"
                aria-label={`${def.label} maximum`}
                className="w-24 rounded border border-line bg-cream px-2 py-1 text-xs text-charcoal placeholder:text-muted"
              />
            </div>
          </div>,
        );
        if (r.min !== null || r.max !== null) {
          chipExtras.push(
            <ActiveChip
              key={`range:${def.key}`}
              label={rangeChipLabel(def.label, r, def.format)}
              onRemove={() => state.setFacet(def.key, { min: null, max: null })}
            />,
          );
        }
        break;
      }
      case 'custom': {
        const value = def.coerce(raw);
        panelExtras.push(
          <div key={`custom:${def.key}`}>
            {def.renderControl(value, next => state.setFacet(def.key, next))}
          </div>,
        );
        if (selectionActive(def as FacetDef<unknown>, raw)) {
          chipExtras.push(
            <span key={`custom:${def.key}`}>
              {def.renderChip(value, () => state.setFacet(def.key, def.defaultValue))}
            </span>,
          );
        }
        break;
      }
      case 'multi':
      case undefined: {
        const selected = (Array.isArray(raw) ? raw : []) as (string | number)[];
        groups.push({
          key: def.key,
          label: def.label,
          options: derivedOptions.get(def.key) ?? [],
          selected,
          onToggle: v => state.setFacet(def.key, toggleValue(selected, v)),
          display: def.searchable ? 'list' : 'chips',
          hint: def.hint,
        });
        break;
      }
    }
  }
  if (config.getVoided) {
    groups.push({
      key: '__voided',
      label: 'Voided',
      options: VOIDED_OPTIONS,
      selected: state.voided === null ? [] : [state.voided],
      onToggle: v => state.setVoided(state.voided === v ? null : (v as VoidedFilter)),
    });
  }

  const resting = restingSort(config.sort);
  const sortActive =
    config.sort !== undefined &&
    (state.sort.field !== resting.field || state.sort.dir !== resting.dir);

  const viewOptions = useMemo(() => {
    const out: { value: CollectionViewKind; label: string }[] = [];
    if (config.kanban) out.push({ value: 'kanban', label: 'Board' });
    if (config.list) out.push({ value: 'list', label: 'List' });
    if (config.cards) out.push({ value: 'cards', label: 'Cards' });
    return out;
  }, [config]);

  const currentViewItems = state.view === 'kanban' ? state.kanbanItems : state.visibleItems;

  // The visible-intersection invariant: the bulk bar only ever receives ids that are both
  // selected AND in the current view's visible set — select → filter → bulk cannot mutate a
  // hidden record.
  const selectedIds = selection?.selectedIds;
  const visibleSelectedIds = useMemo(() => {
    if (!selectedIds) return null;
    const visibleIds = new Set<string | number>();
    for (const item of currentViewItems) visibleIds.add(config.getItemId(item));
    const out = new Set<string | number>();
    for (const id of selectedIds) if (visibleIds.has(id)) out.add(id);
    return out;
  }, [selectedIds, currentViewItems, config]);

  const kanbanColumns = kanban?.columns;
  const kanbanColumnIds = useMemo(
    () => (kanbanColumns ?? []).map(c => c.id),
    [kanbanColumns],
  );

  // The detail overlay, shared by EVERY branch below — loading, error, empty and normal. A deep
  // link (or any `selectedId` outside the canonical set) resolves through `detail.loadById`, so it
  // MUST stay mounted even when `items` is empty — otherwise a failed/empty list load would
  // kill a record deep link that the old unconditional panel served fine.
  //
  // It is declared ABOVE the loading early-return, not below it, and that order is the whole
  // point: `usePageAssembly` reports `loading` with `items: null` for the ENTIRE assembly
  // and never surfaces a partial set, so a shell that returned its progress panel before building
  // this block made a deep link un-openable until the last page landed — on CRM Contacts, that is
  // a shared link showing a progress bar instead of the record it names. `CollectionDetail`
  // fetches an absent id by itself, so mounting it beside the progress panel is all it needs.
  const detailBlock = detail && config.detail && onSelect && (
    <CollectionDetail
      config={config}
      state={state}
      items={items}
      selectedId={selectedId}
      onSelect={onSelect}
      detail={detail}
      kanbanColumnIds={kanbanColumnIds}
    />
  );

  if (loading && (loading.loading || loading.error !== null)) {
    return (
      <div>
        {loading.error !== null ? (
          <div className="rounded-xl border border-line bg-cream px-6 py-8 text-center text-sm text-muted">
            <p className="mb-3">{loading.error}</p>
            <button
              type="button"
              onClick={loading.retry}
              className="rounded-lg border border-line px-3 py-1.5 text-charcoal hover:bg-sand"
            >
              Retry
            </button>
          </div>
        ) : (
          // Never a bare spinner (long-running-fetch rule): text with progress + a thin brand bar.
          <div className="px-2 py-8 text-center">
            <p className="mb-2 text-sm text-muted">
              Loading {noun}…{loading.itemsLoaded > 0 ? ` ${loading.itemsLoaded} loaded` : ''}
            </p>
            <div className="mx-auto h-1.5 w-full max-w-sm animate-pulse rounded-full bg-brand/60" />
          </div>
        )}
        {detailBlock}
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div>
        <EmptyState message={config.emptyState?.message ?? `No ${noun} yet.`} />
        {detailBlock}
      </div>
    );
  }

  return (
    <div>
      <SearchFilterBar
        query={state.query}
        onQueryChange={state.setQuery}
        placeholder={searchPlaceholder ?? `Search ${noun}...`}
        resetNonce={searchResetNonce}
        groups={groups}
        extraFacets={panelExtras.length > 0 ? <>{panelExtras}</> : undefined}
        extraChips={chipExtras.length > 0 ? <>{chipExtras}</> : undefined}
        sort={
          config.sort
            ? {
                options: config.sort.fields,
                value: state.sort,
                defaultSort: resting,
                onChange: state.setSort,
                active: sortActive,
                // Reads `dragLocked` as well as `manualOrder` so the note cannot lie on a
                // `dragPolicy: 'column'` board, where a non-manual sort does NOT pause drag.
                // Under the default 'index' policy `!manualOrder` implies `dragLocked`, so
                // this is byte-identical there.
                note:
                  state.view === 'kanban' && state.dragLocked && !state.manualOrder
                    ? '· drag paused'
                    : undefined,
                ariaLabel: `Sort ${noun}`,
              }
            : undefined
        }
        visibleCount={currentViewItems.length}
        totalCount={items.length}
        countNoun={noun}
        active={state.isFiltering}
        activeFacetCount={state.activeFacetCount}
        onClear={() => {
          // The bar's Clear names EVERYTHING it represents, search box included — the box
          // empties via resetNonce, and the state must follow or the pending debounce would
          // re-apply the old query. (The hook's clearFacets alone deliberately preserves it.)
          state.setQuery('');
          state.clearFacets();
        }}
        trailing={
          <>
            {viewOptions.length > 1 && (
              <ViewSwitcher<CollectionViewKind>
                view={state.view}
                onChange={state.setView}
                options={viewOptions}
              />
            )}
            {(config.toggles ?? []).map(t => (
              <label
                key={t.key}
                className="flex shrink-0 cursor-pointer items-center gap-1.5 text-sm text-charcoal"
              >
                <input
                  type="checkbox"
                  checked={state.toggles[t.key] ?? t.default !== false}
                  onChange={e => state.setToggle(t.key, e.target.checked)}
                  className="accent-brand"
                />
                {t.label}
              </label>
            ))}
            {toolbarExtras}
          </>
        }
      />

      {selection && visibleSelectedIds && visibleSelectedIds.size > 0 && (
        <div className="mb-3">{selection.renderBulkBar(visibleSelectedIds, visibleSelectedIds.size)}</div>
      )}

      {state.view === 'kanban' && kanban && (
        <KanbanView config={config} state={state} kanban={kanban} />
      )}
      {state.view === 'list' && (
        <CollectionListView
          config={config}
          state={state}
          selection={selection}
          onSelect={onSelect}
        />
      )}
      {state.view === 'cards' && (
        <CardsView
          config={config}
          state={state}
          cards={cards}
          selectedId={selectedId}
          onSelect={onSelect}
        />
      )}

      {detailBlock}
    </div>
  );
}
