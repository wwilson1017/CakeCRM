/**
 * The one state hook behind a CollectionView.
 *
 * From the kit's perspective this hook IS "the page": `shared/search`'s bar and
 * `shared/listview`'s table stay fully controlled beneath it, and every rule those modules
 * state about their owner (coercions run in useState initialisers and never throw; sort lives
 * under its own storage key; the view mode defaults to the board on a fresh session) is
 * honored here once instead of re-derived per app.
 *
 * Invariants owned here, in one place:
 *  • **View validity** — `defaultView` must name a view whose config block is present (fail
 *    fast: a config error, not a runtime condition), and a stale persisted view falls back to
 *    `defaultView` rather than rendering a view the config no longer declares.
 *  • **The drag gate** — `dragLocked = isFiltering || !manualOrder || hasTruncatedColumn`.
 *    Filtering makes a drop index unmappable (subset), a non-manual sort makes it a lie,
 *    and a truncated column makes it ambiguous (the drop lands relative to rows that
 *    are not all rendered). Toggles are EXCLUDED — they remove columns, not cards.
 *    All three are about the drop INDEX, so a board that declares
 *    `KanbanViewConfig.dragPolicy: 'column'` (a drop assigns a column and nothing else,
 *    because no rank column exists to persist a position into) opts out of the gate
 *    entirely and keeps only its own `kanban.dragDisabled` extras.
 *  • **Resets live in handlers, never effects** — every filter/sort/view mutation also clears
 *    the expansion sets in its own handler, because deriving that reset in an effect is a
 *    setState-in-effect cascade and a build-blocking React Compiler lint error (a sibling
 *    surface's lesson, verbatim).
 *  • **Config identity** — every memo keys on accessors from `config`, so the config must be
 *    referentially stable (module constant or memoized factory). DEV warns when it churns.
 *  • **Items identity** — the docs/visibleItems memos key on the `items` array reference, so
 *    `items` must be a NEW array whenever any item's `searchText` inputs change. In-place
 *    mutation with the same reference leaves search docs and filtering stale (and is a React
 *    Compiler immutability violation anyway).
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import {
  buildDoc,
  coerceSortState,
  docMatchesTokens,
  isManualSort,
  loadPersistedState,
  savePersistedState,
  sortItems,
  tokenize,
} from '../search';
import type { SortState } from '../search';
import {
  activeFacetCount,
  applyFacets,
  coerceSelections,
  defaultSelections,
} from './facets';
import type {
  CollectionConfig,
  CollectionSortConfig,
  DragPolicy,
  CollectionState,
  CollectionViewKind,
  ControlledToggleProps,
  FacetSelections,
  VoidedFilter,
} from './types';

export const KANBAN_COLUMN_CAP = 50;
export const CARDS_SECTION_CAP = 24;

/** The surface's resting sort — what a fresh session gets, what Reset restores, and the
 *  baseline the toolbar's "active" styling compares against. ONE definition so the hook's
 *  coercion fallback and the SortControl wiring cannot drift. */
export function restingSort<T>(sortConfig: CollectionSortConfig<T> | undefined): SortState {
  if (sortConfig?.defaultSort) return sortConfig.defaultSort;
  const first = sortConfig?.fields[0];
  return { field: first ? first.value : '', dir: 'asc' };
}

interface EnvelopeShape {
  query: string;
  facets: FacetSelections;
  voided: VoidedFilter;
  toggles: Record<string, boolean>;
}

function envelopeKey(c: CollectionConfig<never>['storage']): string {
  return `collection_${c.key}_v${c.version}`;
}
function sortKey(c: CollectionConfig<never>['storage']): string {
  return `collection_${c.key}_sort_v${c.version}`;
}
function viewKey(c: CollectionConfig<never>['storage']): string {
  return `collection_${c.key}_view`;
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

function enabledViews<T>(config: CollectionConfig<T, DragPolicy>): CollectionViewKind[] {
  const views: CollectionViewKind[] = [];
  if (config.kanban) views.push('kanban');
  if (config.list) views.push('list');
  if (config.cards) views.push('cards');
  return views;
}

function defaultToggles<T>(config: CollectionConfig<T, DragPolicy>): Record<string, boolean> {
  const out: Record<string, boolean> = {};
  for (const t of config.toggles ?? []) out[t.key] = t.default !== false;
  return out;
}

export interface UseCollectionStateOptions {
  /** Server-persisted column visibility (CRM stage hiding): overrides the layer's persisted
   *  toggle per key; the app owns the optimistic overlay + rollback. */
  controlledToggles?: ControlledToggleProps;
}

export default function useCollectionState<T>(
  config: CollectionConfig<T, DragPolicy>,
  items: readonly T[],
  options: UseCollectionStateOptions = {},
): CollectionState<T> {
  const views = enabledViews(config);
  if (views.length === 0 || !views.includes(config.defaultView)) {
    // A config error, not a runtime condition — fail fast in every environment rather than
    // silently rendering nothing (the view-validity invariant from the plan review).
    throw new Error(
      `CollectionConfig "${config.storage.key}": defaultView "${config.defaultView}" has no ` +
        `matching view block (enabled: ${views.join(', ') || 'none'})`,
    );
  }

  // DEV-only referential-stability warning: memos below key on config identity, so a config
  // rebuilt per render re-filters and re-docs the whole set every keystroke. Lives in an
  // effect (not render) — the React Compiler ruleset bans ref access during render, and an
  // effect keyed on [config] fires exactly when the identity changes.
  const churnRef = useRef(0);
  useEffect(() => {
    churnRef.current += 1;
    // Threshold well above legitimate churn (StrictMode mount alone counts 2, and memoized
    // factories rebuild when their deps genuinely change), repeating so a scrolled-past
    // console line isn't the only chance to notice a per-render rebuild.
    if (import.meta.env.DEV && churnRef.current % 15 === 0) {
      console.warn(
        `CollectionConfig "${config.storage.key}" changed identity repeatedly — build it at ` +
          'module scope or memoize the factory, or every keystroke re-derives the whole set.',
      );
    }
  }, [config]);

  const facets = useMemo(() => config.facets ?? [], [config]);

  // ── State, restored via never-throw coercions (loadPersistedState's contract) ──
  const [envelope, setEnvelope] = useState<EnvelopeShape>(() =>
    loadPersistedState(envelopeKey(config.storage), raw => {
      const source = isRecord(raw) ? raw : {};
      return {
        query:
          config.persistSearch === true && typeof source.query === 'string' ? source.query : '',
        facets: coerceSelections(facets, source.facets),
        voided:
          source.voided === 'hide' || source.voided === 'only'
            ? (source.voided as VoidedFilter)
            : null,
        toggles: (() => {
          const merged = defaultToggles(config);
          if (isRecord(source.toggles)) {
            for (const t of config.toggles ?? []) {
              const v = source.toggles[t.key];
              if (typeof v === 'boolean') merged[t.key] = v;
            }
          }
          return merged;
        })(),
      };
    }),
  );

  const sortFields = config.sort?.fields;
  const fallbackSort: SortState = useMemo(() => restingSort(config.sort), [config]);

  const [sort, setSortState] = useState<SortState>(() =>
    sortFields
      ? loadPersistedState(sortKey(config.storage), raw =>
          coerceSortState(raw, sortFields, fallbackSort),
        )
      : fallbackSort,
  );

  const [view, setViewState] = useState<CollectionViewKind>(() =>
    loadPersistedState(viewKey(config.storage), raw =>
      typeof raw === 'string' && (views as string[]).includes(raw)
        ? (raw as CollectionViewKind)
        : config.defaultView,
    ),
  );

  const [expandedColumns, setExpandedColumns] = useState<ReadonlySet<string | number>>(
    () => new Set(),
  );
  const [expandedSections, setExpandedSections] = useState<ReadonlySet<string>>(() => new Set());

  // ── Persistence (skip-unchanged is free: React bails identical state; the serialized write
  //     itself is cheap and savePersistedState swallows quota failures) ──
  useEffect(() => {
    const { query, ...rest } = envelope;
    savePersistedState(envelopeKey(config.storage), {
      ...rest,
      ...(config.persistSearch === true ? { query } : {}),
    });
  }, [envelope, config]);
  useEffect(() => {
    if (sortFields) savePersistedState(sortKey(config.storage), sort);
  }, [sort, config, sortFields]);
  useEffect(() => {
    savePersistedState(viewKey(config.storage), view);
  }, [view, config]);

  // ── Derived ──
  const effectiveToggles = useMemo(
    () => ({ ...envelope.toggles, ...(options.controlledToggles?.values ?? {}) }),
    [envelope.toggles, options.controlledToggles?.values],
  );

  // Search docs, built once per data change (the searchFilters discipline).
  const docs = useMemo(() => {
    const map = new Map<T, string>();
    for (const item of items) map.set(item, buildDoc([...config.searchText(item)]));
    return map;
  }, [items, config]);

  // Optional per-config search tuning: a sibling surface's stopword removal + short-token
  // whole-word anchoring. Absent ⇒ the shared defaults, so every other consumer is untouched.
  const stopwordSet = useMemo(
    () =>
      config.searchTuning?.stopwords ? new Set(config.searchTuning.stopwords) : undefined,
    [config],
  );
  const matchOpts = useMemo(
    () =>
      config.searchTuning?.anchorShortTokens ? { anchorShortTokens: true } : undefined,
    [config],
  );

  const tokens = useMemo(
    () => tokenize(envelope.query, stopwordSet ? { stopwords: stopwordSet } : undefined),
    [envelope.query, stopwordSet],
  );

  const visibleItems = useMemo(() => {
    const faceted = applyFacets(items, facets, envelope.facets, envelope.voided, config.getVoided);
    const searched =
      tokens.length === 0
        ? faceted
        : faceted.filter(item => docMatchesTokens(docs.get(item) ?? '', tokens, matchOpts));
    return sortFields ? sortItems(searched, sortFields, sort) : searched.slice();
  }, [items, facets, envelope.facets, envelope.voided, config, tokens, docs, matchOpts, sortFields, sort]);

  // Kanban's operational default: voided rows are off the board unless the config opts the
  // board into the tri-state ('facet') — the list keeps them struck-through instead.
  const kanbanItems = useMemo(() => {
    if (!config.kanban || !config.getVoided) return visibleItems;
    if (config.kanban.voidedPolicy === 'facet') return visibleItems;
    const getVoided = config.getVoided;
    return visibleItems.filter(item => !getVoided(item));
  }, [visibleItems, config]);

  const isFiltering =
    tokens.length > 0 ||
    envelope.voided !== null ||
    activeFacetCount(facets, envelope.facets, null) > 0;

  const manualOrder = sortFields ? isManualSort(sort, sortFields) : true;

  const truncatedColumns = useMemo(() => {
    const truncated = new Set<string | number>();
    if (!config.kanban) return truncated;
    const cap = config.kanban.columnCap ?? KANBAN_COLUMN_CAP;
    const counts = new Map<string | number, number>();
    for (const item of kanbanItems) {
      const col = config.kanban.getColumnId(item);
      counts.set(col, (counts.get(col) ?? 0) + 1);
    }
    for (const [col, count] of counts) {
      if (count > cap && !expandedColumns.has(col)) truncated.add(col);
    }
    return truncated;
  }, [kanbanItems, config, expandedColumns]);

  // Under `dragPolicy: 'column'` a drop assigns only a column and the app discards `newIndex`,
  // so none of the three ambiguities below can arise — the layer contributes no lock and the
  // app's own `kanban.dragDisabled` extras become the whole gate. See KanbanViewConfig.
  const dragPolicy = config.kanban?.dragPolicy ?? 'index';
  const dragLocked =
    dragPolicy === 'column' ? false : isFiltering || !manualOrder || truncatedColumns.size > 0;

  // ── Handlers — the only mutation paths; each carries its own expansion reset ──
  const resetExpansions = () => {
    setExpandedColumns(prev => (prev.size === 0 ? prev : new Set()));
    setExpandedSections(prev => (prev.size === 0 ? prev : new Set()));
  };

  return {
    query: envelope.query,
    facetSelections: envelope.facets,
    voided: envelope.voided,
    toggles: effectiveToggles,
    sort,
    view,

    visibleItems,
    kanbanItems,
    isFiltering,
    activeFacetCount: activeFacetCount(facets, envelope.facets, envelope.voided),
    manualOrder,
    dragLocked,
    truncatedColumns,

    setQuery: value => {
      setEnvelope(prev => ({ ...prev, query: value }));
      resetExpansions();
    },
    setFacet: (key, value) => {
      setEnvelope(prev => ({ ...prev, facets: { ...prev.facets, [key]: value } }));
      resetExpansions();
    },
    clearFacets: () => {
      // Clears facets + voided but PRESERVES the query — the search bar's contract the kit
      // carried forward: "Clear filters" names the chips, not the search box.
      setEnvelope(prev => ({
        ...prev,
        facets: defaultSelections(facets),
        voided: null,
      }));
      resetExpansions();
    },
    setVoided: value => {
      setEnvelope(prev => ({ ...prev, voided: value }));
      resetExpansions();
    },
    setToggle: (key, value) => {
      const controlled = options.controlledToggles;
      if (controlled && key in controlled.values) {
        // Server-persisted visibility: the app owns the write and its rollback.
        void controlled.onToggle(key, value);
        return;
      }
      setEnvelope(prev => ({ ...prev, toggles: { ...prev.toggles, [key]: value } }));
      // Deliberately NOT resetting expansions: a toggle removes whole columns, it does not
      // change which rows are visible inside a surviving column.
    },
    setSort: next => {
      setSortState(next);
      resetExpansions();
    },
    setView: next => {
      if (!(views as string[]).includes(next)) return;
      setViewState(next);
      resetExpansions();
    },
    expandColumn: columnId => {
      setExpandedColumns(prev => new Set(prev).add(columnId));
    },
    expandedColumns,
    expandSection: section => {
      setExpandedSections(prev => new Set(prev).add(section));
    },
    expandedSections,
  } satisfies CollectionState<T>;
}

export type { EnvelopeShape as CollectionEnvelopeShape };
