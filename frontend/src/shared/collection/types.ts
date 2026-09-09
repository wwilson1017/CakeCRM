/**
 * CollectionConfig — the declarative seam of the collection layer.
 *
 * One config describes how an app presents a client-loaded set of records; the layer composes
 * the existing kit — `shared/search` (controlled bar + pure match/sort/persist),
 * `shared/listview` (table + Board⇄List switcher), `shared/dnd` (kanban drag) and
 * `shared/overlay/DetailModal` — behind it. The kit's page-owns-state contract is honored,
 * not replaced: `useCollectionState` IS the page state, and every kit component stays fully
 * controlled beneath it. Nothing here re-implements kit logic.
 *
 * Two channels, copied from `AgentUIConfig` (the one abstraction this repo has proven at
 * scale): **config** is declarative — data accessors, copy, policy — and its REQUIRED
 * fields are guard-enforced (a build-time script checks that a required field
 * nothing under `shared/collection/` consumes fails CI — the blueprint's dead-`subtitle` class).
 * **Props** (`CollectionViewProps`, landing with the view components in part 2 — prose
 * references to it below are forward references) are imperative — runtime data, callbacks,
 * render slots — and deliberately unguarded. Keep required config minimal: required means
 * every consumer must set it AND the layer must consume it. Optional booleans default ON via
 * `!== false` where omission must be the safe default.
 *
 * Configs must be referentially stable — a module-scope constant, or a `makeConfig(deps)`
 * factory memoized on stable deps when facets depend on runtime data (current user, stages).
 * Every memo in `useCollectionState` keys on config identity; an inline object literal in a
 * component body rebuilds the search docs for the whole set on every keystroke.
 */
import type { ReactNode, Ref } from 'react';
import type { FacetOption, SortFieldDef, SortState } from '../search';
import type { ListColumn } from '../listview';
import type { DragDisabled, KanbanColumnDef } from '../dnd';

// ---------------------------------------------------------------------------------------------
// Identity & persistence

/**
 * sessionStorage identity. Three keys derive from it, following the kit's independence rule
 * (`shared/search/persist.ts`: sort under its own key makes adding sort a no-migration
 * change): `collection_{key}_v{version}` (facet selections, voided, toggles — and the query
 * iff `persistSearch`), `collection_{key}_sort_v{version}`, `collection_{key}_view`.
 * Bump `version` on any persisted-shape change; loads coerce junk to defaults, never throw.
 */
export interface CollectionStorage {
  /** Unique per surface — two configs sharing a key share (and cross-contaminate) their
   *  persisted filter/sort/view state. */
  key: string;
  version: number;
}

export type CollectionViewKind = 'kanban' | 'list' | 'cards';

// ---------------------------------------------------------------------------------------------
// Facets — five kinds; selections live in the layer's state, definitions live here.

/** Multi-select facet: OR within the facet, AND across facets. Options are derived from the
 *  loaded data unless `options` supplies them (an enum whose empty values still deserve a
 *  chip). */
export interface MultiFacetDef<T> {
  kind?: 'multi';
  key: string;
  label: string;
  getValue: (item: T) => string | number | (string | number)[] | null | undefined;
  options?: FacetOption[];
  /** Popover checklist search — required for high-cardinality facets (see FacetGroup.display). */
  searchable?: boolean;
 /** Help line under the group heading — pure pass-through to `FacetGroup.hint`
   *  (a sibling surface's waiting facet defuses a naming collision with it). */
  hint?: string;
}

/** Single-select preset facet (CRM close-date / last-activity): at most one value active;
 *  toggling the active value clears it. */
export interface SingleFacetDef<T> {
  kind: 'single';
  key: string;
  label: string;
  options: FacetOption[];
  predicate: (item: T, value: string | number) => boolean;
}

/** Boolean chip facet (e.g. "Priority only"). */
export interface BooleanFacetDef<T> {
  kind: 'boolean';
  key: string;
  label: string;
  predicate: (item: T) => boolean;
}

/** Numeric min/max facet (CRM deal value). Either bound may be null. */
export interface RangeFacetDef<T> {
  kind: 'range';
  key: string;
  label: string;
  getValue: (item: T) => number | null;
  /**
   * Render a bound for the collapsed CHIP only — money, a unit suffix. The panel's two
   * inputs stay raw `<input type="number">`: formatting a value the user is typing into fights
   * the keystroke. Omitted ⇒ `String`, which is every earlier consumer unchanged.
   */
  format?: (value: number) => string;
}

/**
 * Escape-hatch facet with a FULL value lifecycle — storage and evaluation alone are not
 * enough, because the shared checklist cannot edit an arbitrary `V` and `toolbarExtras` is
 * NOT a filtering channel (a filter living outside the bar is invisible to active-count,
 * clear-all and the drag gate — the "filtered but you cannot tell by what" failure).
 * `V` must be JSON-serializable. `coerce` must accept null/undefined/junk and never throw —
 * it runs inside a `useState` initialiser (the `loadPersistedState` contract).
 */
export interface CustomFacetDef<T, V = unknown> {
  kind: 'custom';
  key: string;
  label: string;
  defaultValue: V;
  // Method syntax, not property-style arrows, deliberately: methods are bivariant under
  // strictFunctionTypes, which is what lets a typed `CustomFacetDef<Row, MyValue>` enter the
  // heterogeneous `FacetDef<T>` array (whose custom arm erases V to unknown). Property-style
  // signatures are contravariant in their params and reject every concrete V.
  isActive(value: V): boolean;
  coerce(raw: unknown): V;
  predicate(item: T, value: V): boolean;
  /** The disclosure-panel editor (`SearchFilterBar`'s `extraFacets` slot). */
  renderControl(value: V, setValue: (next: V) => void): ReactNode;
  /** The collapsed chip-row representation (`extraChips`) while active. */
  renderChip(value: V, clear: () => void): ReactNode;
}

export type FacetDef<T> =
  | MultiFacetDef<T>
  | SingleFacetDef<T>
  | BooleanFacetDef<T>
  | RangeFacetDef<T>
  // `any` would erase V entirely; `unknown` keeps the def usable in a heterogeneous array
  // while renderControl/renderChip stay typed at the definition site.
  | CustomFacetDef<T, unknown>;

/** One facet's selection state, keyed by `FacetDef.key`. Shapes per kind:
 *  multi → (string|number)[] · single → string|number|null · boolean → boolean ·
 *  range → {min: number|null, max: number|null} · custom → its own V. */
export type FacetSelections = Record<string, unknown>;

export interface RangeValue {
  min: number | null;
  max: number | null;
}

/** Tri-state voided filter, enabled by `getVoided`: null = show all (struck through where the
 *  view renders voids), 'hide' = operational reads, 'only' = audit view. */
export type VoidedFilter = 'hide' | 'only' | null;

// ---------------------------------------------------------------------------------------------
// Sort / toggles / views

export interface CollectionSortConfig<T> {
  /** Pass STRAIGHT through to `shared/search`'s SortControl/sortItems/coerceSortState.
   *  Include an `arrayOrder` field to enable manual order — it becomes the default and the
   *  only order under which kanban drag unlocks (`isManualSort` is the one predicate). */
  fields: readonly SortFieldDef<T>[];
  /** Default selection; falls back to the first field, `asc`, when omitted. */
  defaultSort?: SortState;
}

/**
 * Column-visibility toggle ("Show parked", "Show closed"). Persisted in the
 * layer's envelope and EXCLUDED from the drag gate — toggles remove whole columns, not cards,
 * so a drop index still maps back. Server-persisted visibility (CRM stage hiding) must NOT
 * ride this: pass it through `CollectionViewProps.toggles` (controlled) so the app keeps its
 * optimistic mutation + rollback.
 */
export interface ToggleDef {
  key: string;
  label: string;
  /** `!== false` → default ON. */
  default?: boolean;
}

export interface ListViewConfig<T> {
  /** Pass-through to `shared/listview`'s ListView. */
  columns: ListColumn<T>[];
  /** Rows rendered before "show all" (ListView's own default applies when omitted). */
  renderCap?: number;
}

export interface KanbanViewConfig<T> {
  /** Which column an item belongs to — matched against `CollectionViewProps.kanban.columns`. */
  getColumnId: (item: T) => string | number;
  /** Cards rendered per column before "Show N more". A truncated column locks drag — a drop
   *  index against a partial column is ambiguous. Default: KANBAN_COLUMN_CAP. */
  columnCap?: number;
  /**
   * Void semantics for the BOARD view. `'hide'` (default): operational board, voided rows
   * excluded regardless of the tri-state facet. `'facet'`: the board obeys the tri-state like
   * the list does. Per-view because the same canonical set legitimately reads differently:
   * a board is an operational surface, a list doubles as history.
   */
  voidedPolicy?: 'hide' | 'facet';
  /**
   * What a drop MEANS, and therefore what can make one ambiguous.
   *  • `'index'` (default) — a drop assigns a column AND a position, and the app persists that
   *    position (a rank column). A filtered subset, a non-array sort and a truncated column each
   *    make the drop index unmappable, so `dragLocked` covers all three.
   *  • `'column'` — a drop assigns ONLY a column; the app discards `newIndex` because no rank
   *    column exists to write it to. No rendered subset can make a column assignment ambiguous,
   *    so the layer contributes NO lock and `dragLocked` is always false — only the app's own
   *    `CollectionKanbanProps.dragDisabled` extras (mobile, a bulk write in flight) apply.
   *    Declaring this while still persisting `newIndex` would silently save a position derived
   *    from a partial list, so it is a claim about the app's `onMove`, not a styling choice.
   *    The type cannot enforce that yet — making `newIndex` structurally unavailable under
   *    `'column'` is tracked in issue #112.
   */
  dragPolicy?: 'index' | 'column';
}

export interface CardsViewConfig<T> {
  getTitle: (item: T) => string;
  getSubtitle?: (item: T) => string | null;
  /** Section header per item (a sibling surface's card-grouping style). Omit for one flat grid. */
  getSection?: (item: T) => string;
  /** Cards rendered per section before "show all". Default: CARDS_SECTION_CAP. */
  sectionCap?: number;
}

export interface DetailConfig<T> {
  getTitle: (item: T) => string;
  getSubtitle?: (item: T) => string | null;
  /**
   * Deep-link seam: when `selectedId` is not in the canonical `items` (a shared URL, partial
   * assembly), the detail renders from this fetch instead of waiting for the row to arrive.
   */
  loadById?: (id: string | number) => Promise<T>;
}

// ---------------------------------------------------------------------------------------------
// The config

export interface CollectionConfig<T> {
  // ── REQUIRED — guard-enforced; each is consumed inside shared/collection ──
  storage: CollectionStorage;
  /** Must name a view whose config block is present — `useCollectionState` fails fast
   *  otherwise, and a stale persisted view falls back here. */
  defaultView: CollectionViewKind;
  getItemId: (item: T) => string | number;
  /** Free-text fields the search box scans — token-AND across all of them via
   *  `shared/search`'s buildDoc/docMatchesTokens (accent-FOLDING: `creme` finds `Crème`). */
  searchText: (item: T) => ReadonlyArray<string | null | undefined>;

  // ── OPTIONAL — presence enables ──
  facets?: FacetDef<T>[];
  sort?: CollectionSortConfig<T>;
  toggles?: ToggleDef[];
  /**
   * Opt into the sibling-surface search rules, off by default:
   *  • `stopwords` — dropped from a multi-word query (a lone stopword stays a real query).
   *  • `anchorShortTokens` — a 1-char / short-numeric token matches a whole word, so `1`
   *    doesn't light up every card containing a 1.
   * Omit for the shared defaults (substring-only, no stopwords), which every other consumer uses.
   */
  searchTuning?: { stopwords?: readonly string[]; anchorShortTokens?: boolean };
  /** Persist the query text in the envelope. Default OFF — a silently-narrowed board reads as
   *  data loss (a sibling surface's rationale); CRM opts in to keep its persisted search. */
  persistSearch?: boolean;
  list?: ListViewConfig<T>;
  kanban?: KanbanViewConfig<T>;
  cards?: CardsViewConfig<T>;
  detail?: DetailConfig<T>;
  /** item => voided. Presence enables the tri-state facet + strikethrough rendering. */
  getVoided?: (item: T) => boolean;
  emptyState?: { message: string };
  /** "3 of 40 cards" count line. */
  itemNoun?: { singular: string; plural: string };
}

// ---------------------------------------------------------------------------------------------
// State (returned by useCollectionState — the app may read it; mutation only via handlers)

export interface CollectionState<T> {
  // filter state
  /** As reported by the bar — `shared/search`'s SearchInput debounces internally, so this is
   *  already the settled value; the layer applies it directly. */
  query: string;
  facetSelections: FacetSelections;
  voided: VoidedFilter;
  toggles: Record<string, boolean>;
  sort: SortState;
  view: CollectionViewKind;

  // derived
  /** Filtered + sorted, canonical order preserved under manual sort. Kanban applies its own
   *  voidedPolicy on top (see visibleItemsForKanban). */
  visibleItems: T[];
  /** visibleItems with the kanban `voidedPolicy: 'hide'` default applied. */
  kanbanItems: T[];
  isFiltering: boolean;
  activeFacetCount: number;
  manualOrder: boolean;
  /** THE central drag gate: isFiltering || !manualOrder || hasTruncatedColumn — or a constant
   *  false under `KanbanViewConfig.dragPolicy: 'column'`, where a drop carries no index to be
   *  made ambiguous. App extras (isMobile, bulkPending) OR into
   *  `CollectionViewProps.kanban.dragDisabled`. */
  dragLocked: boolean;
  /** Per-column truncation under `columnCap` — also what "Show N more" expands. */
  truncatedColumns: ReadonlySet<string | number>;

  // handlers — the ONLY mutation paths; every one also resets caps/expansions so the
  // reset can never be a derived effect (a setState-in-effect is a build-blocking React
  // Compiler lint error — a sibling surface's lesson).
  setQuery: (value: string) => void;
  setFacet: (key: string, value: unknown) => void;
  clearFacets: () => void;
  setVoided: (value: VoidedFilter) => void;
  setToggle: (key: string, value: boolean) => void;
  setSort: (next: SortState) => void;
  setView: (view: CollectionViewKind) => void;
  expandColumn: (columnId: string | number) => void;
  expandedColumns: ReadonlySet<string | number>;
  expandSection: (section: string) => void;
  expandedSections: ReadonlySet<string>;
}

// ---------------------------------------------------------------------------------------------
// Props (the imperative channel — unguarded by design)

export interface CollectionSelectionProps {
  selectedIds: ReadonlySet<string | number>;
  onChange: (next: Set<string | number>) => void;
  /**
   * The bulk bar receives ONLY the ids that are both selected AND currently visible —
   * select → filter → bulk must never mutate hidden records (CRM's visible-intersection
   * invariant). `count` is that safe set's size.
   */
  renderBulkBar: (visibleSelectedIds: ReadonlySet<string | number>, count: number) => ReactNode;
}

/** Controlled column-visibility for server-persisted state (CRM stage hiding). When present
 *  for a key it overrides the layer's persisted toggle; the app owns the optimistic overlay
 *  and rollback. */
export interface ControlledToggleProps {
  values: Record<string, boolean>;
  onToggle: (key: string, next: boolean) => void | Promise<void>;
}

// ---------------------------------------------------------------------------------------------
// Detail close contract

export type DetailCloseReason = 'escape' | 'backdrop' | 'button' | 'nav';

/** Permission, not action: return whether the leave may proceed. The PRIMITIVE then performs
 *  it (close ⇒ `onSelect(null)`, nav ⇒ `onSelect(nextId)`) — separating the two is what stops
 *  an app handler and the layer both closing, or neither. */
export type DetailCloseGuard = (reason: DetailCloseReason) => boolean | Promise<boolean>;

export interface DetailRenderContext {
  /**
   * Carry child-local facts a page-level handler cannot see (signature ink, in-flight
   * uploads, an edit mode). REPLACES any current guard; returns an unregister function.
   * Bound to the record it was registered under — a guard from record A is never consulted
   * for record B (the ‹ › remount makes B a fresh mount that registers its own).
   */
  registerCloseGuard: (guard: DetailCloseGuard) => () => void;
}

/**
 * What `CollectionDetail` actually reads out of a `CollectionConfig` — nothing else.
 *
 * A page that renders its OWN views (the pipeline board, which keeps issue #21's filter bar and
 * its own kanban) still wants the shared detail contract, but it runs no `useCollectionState`,
 * so it has no `CollectionConfig` to hand over: minting one would mean inventing a `storage` key
 * (a second filter store shadowing the page's real one) and a `defaultView` for a hook that never
 * runs. Narrowing the prop to exactly the fields the component consumes lets such a page pass a
 * four-field literal, while a full `CollectionConfig` still satisfies it structurally — so
 * `CollectionView` and every existing caller are unaffected.
 */
export type DetailHostConfig<T> = Pick<
  CollectionConfig<T>,
  'getItemId' | 'detail' | 'kanban' | 'cards'
>;

export interface CollectionDetailProps<T> {
  /** The modal body. Remounted (keyed by `getItemId`) on ‹ › nav — load-bearing, per
   *  the blueprint's `key={uuid}` lesson: per-record draft state must not leak across records. */
  render: (item: T, ctx: DetailRenderContext) => ReactNode;
  /**
   * Asked before EVERY leave path — Escape, Back, ×, backdrop, ‹ › nav. Default policy when
   * omitted: allow everything except backdrop (most detail surfaces carry an edit form; a
   * stray backdrop tap dismissing one is the data-loss vector `CardOverlayShell` existed to
   * prevent).
   */
  onRequestClose?: DetailCloseGuard;
}

// ---------------------------------------------------------------------------------------------
// View props

export interface CollectionMoveEvent<T> {
  item: T;
  fromColumnId: string | number;
  toColumnId: string | number;
  newIndex: number;
}

export interface CollectionKanbanProps<T, C = unknown> {
  /** Ordered column defs — the board's column order AND the ‹ › nav's column-major order. */
  columns: KanbanColumnDef<C>[];
  /**
   * Resolve the move server-side, then patch the canonical `items` array (full-array patch).
   * Do NOT patch before this resolves — `shared/dnd` shows the optimistic move and rolls back
   * on reject, so a pre-resolve canonical write double-applies on failure.
   *
   * ONE sanctioned exception, and it is load-bearing rather than a loophole: a board whose
   * `onMove` can never reject (it persists in the background and reverts through its own
   * canonical data, as the CRM pipeline does) may patch first and resolve immediately, because
   * the rollback branch that would double-apply is then unreachable — `useKanbanState.commitMove`
   * records the same exemption from the other side. Such an `onMove` must return a resolved
   * promise on EVERY path, including failure; one that can reject must obey the rule above.
   */
  onMove: (event: CollectionMoveEvent<T>) => Promise<void>;
  canDrop?: (item: T, targetColumnId: string | number) => boolean;
  renderColumn: (column: KanbanColumnDef<C>, children: ReactNode) => ReactNode;
  renderCard: (item: T, columnId: string | number, isDragging: boolean) => ReactNode;
  renderEmptyColumn?: (column: KanbanColumnDef<C>) => ReactNode;
  /**
   * App extras (isMobile, bulkPending) — OR'd with the layer's `dragLocked`.
   *
   * `true` disables the whole board; a PREDICATE answers per card, which is what a board
   * carrying rows that are visible but not workable needs (issue #83's archived deals: on the
   * board so they can be found and restored, but the server refuses a stage change on one).
   * The layer's own `dragLocked` still wins — it is a board-wide claim, so it collapses a
   * predicate to `true` rather than being OR'd into it.
   */
  dragDisabled?: DragDisabled<T>;
  /** Pass-throughs to `shared/dnd`'s board/column containers (scroller layout, column
   *  spacing) — presentation the app owns, like its renderColumn chrome. */
  className?: string;
  columnClassName?: string;
  /**
   * Ref to the board's horizontal SCROLLER element — a pass-through to `shared/dnd`'s
   * `KanbanBoard`, which has always accepted one (`dnd/types.ts`) while this layer dropped it
   *. An app needs it when something outside the board must observe or drive that scroll
   * position: CRM's mobile stage-chip bar roots an IntersectionObserver on it to track which
   * column is snapped into view, and calls `scrollIntoView` on the column nodes to move it.
   * The type matches `KanbanBoardProps.scrollerRef` exactly (`Ref`, not `RefObject`) so the
   * pass-through narrows nothing.
   */
  scrollerRef?: Ref<HTMLDivElement>;
}

export interface CollectionCardsProps<T> {
  /** Fixed-size thumbnail slot at the top of the card (lazy loading is the slot's business —
   *  list/card rows must not mint signed URLs eagerly; no batch endpoint exists). */
  renderThumb?: (item: T) => ReactNode;
  /** Status badge slot beside the title (a sibling surface's `StageBadge` pattern). */
  renderBadge?: (item: T) => ReactNode;
  /**
   * Full-cell override for a grid whose cell needs its own interactive children — a sibling surface
   * renders a photo-enlarge button BESIDE the open-detail button, which the
   * built-in single-`<button>` cell cannot legally contain (nested buttons are invalid HTML).
   * When present, the app owns the WHOLE cell: selection styling, click-to-open, void
   * strikethrough. Section grouping, per-section caps and ordering stay the layer's; `getTitle`
   * / `renderThumb` / `renderBadge` are ignored for that item.
   */
  renderCard?: (item: T) => ReactNode;
}

/** Mirrors `usePageAssembly`'s return — pass the assembly straight in and the shell renders
 *  the progress line / retryable error instead of the views until the set is complete. */
export interface CollectionLoadingProps {
  loading: boolean;
  error: string | null;
  itemsLoaded: number;
  retry: () => void;
}

export interface CollectionViewProps<T, C = unknown> {
  config: CollectionConfig<T>;
  state: CollectionState<T>;
  /** The canonical array — the app owns mutation; the layer never mutates. */
  items: readonly T[];
  selectedId?: string | number | null;
  onSelect?: (id: string | number | null) => void;
  selection?: CollectionSelectionProps;
  kanban?: CollectionKanbanProps<T, C>;
  cards?: CollectionCardsProps<T>;
  detail?: CollectionDetailProps<T>;
  /** Actions ONLY, never filters — a filter outside the bar is invisible to active-count,
   *  clear-all and the drag gate. */
  toolbarExtras?: ReactNode;
  searchPlaceholder?: string;
  /**
   * Bump to empty the search box on a PROGRAMMATIC clear. Needed because `SearchInput` adopts
   * an external value only when it CHANGES: a page that clears while `state.query` is already
   * `''` leaves locally-typed text whose debounce has not settled, which then re-filters a
   * moment after the clear. The bar owns the same mechanism for its own Clear button; this
   * routes a page-initiated clear (a deep link, an app-level "reset filters") to it, instead
   * of the caller re-keying the whole subtree and losing the disclosure panel, the list's
   * show-all and the board's scroll position with it.
   *
   * MUST only ever INCREASE. It is summed with the bar's own internal counter, and both are
   * compared with `!==`, so a monotone value can never be cancelled out by the other source;
   * a caller that decremented could land on a sum the box has already seen and swallow a
   * reset. Bump it (`n => n + 1`), never assign it.
   */
  searchResetNonce?: number;
  loading?: CollectionLoadingProps;
}
