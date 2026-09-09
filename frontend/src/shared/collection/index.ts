/**
 * The collection layer: one declarative `CollectionConfig` per app surface,
 * composing `shared/search` + `shared/listview` + `shared/dnd` + `shared/overlay` behind a
 * single state hook and one shell component. Import from this barrel, never from a file
 * inside the directory.
 *
 * The barrel lists what consumers actually import, and nothing else (the blueprint rule — an
 * export no one imports is dead surface that reads as proven API). The individual views
 * (KanbanView/CollectionListView/CardsView) stay internal — `CollectionView` is the surface;
 * `CollectionDetail` is exported because a bespoke page may orchestrate its own views yet
 * still want the shared detail contract. Such a page supplies a `DetailHostConfig` and its own
 * `navOrder`; `state` exists only for the order the layer would otherwise derive itself.
 */
export { default as CollectionView } from './CollectionView';
export { default as CollectionDetail } from './detail/CollectionDetail';
export { default as useCollectionState, KANBAN_COLUMN_CAP, CARDS_SECTION_CAP, restingSort } from './useCollectionState';
export { default as usePageAssembly, PAGE_TIMEOUT_MS, MAX_PAGES } from './usePageAssembly';
export { default as visibleOrder } from './visibleOrder';
// The close POLICY presets. `CollectionDetail`'s default allows every reason except `backdrop`;
// supplying `onRequestClose` replaces that default wholesale, which is why `denyEscapeBackdrop`
// names both refusals. Exported because the surfaces blocked on this layer adopt it directly.
export { CRM_CLOSING_REASONS, denyEscapeBackdrop, confirmDiscardOn } from './closePolicy';
export type { AssemblyPage, PageAssembly } from './usePageAssembly';
export type {
  CollectionConfig,
  CollectionState,
  CollectionStorage,
  CollectionViewKind,
  CollectionSortConfig,
  CollectionSelectionProps,
  ControlledToggleProps,
  FacetDef,
  MultiFacetDef,
  SingleFacetDef,
  BooleanFacetDef,
  RangeFacetDef,
  CustomFacetDef,
  FacetSelections,
  RangeValue,
  VoidedFilter,
  ToggleDef,
  ListViewConfig,
  KanbanViewConfig,
  CardsViewConfig,
  DetailConfig,
  DetailHostConfig,
  CollectionViewProps,
  CollectionKanbanProps,
  CollectionCardsProps,
  CollectionDetailProps,
  CollectionLoadingProps,
  CollectionMoveEvent,
  DetailCloseReason,
  DetailCloseGuard,
  DetailRenderContext,
} from './types';
