import type { ReactNode, Ref } from 'react';
import type { DragDisabled } from './dragDisabled';

export interface KanbanItem {
  id: number | string;
}

export interface KanbanColumnDef<TColumn> {
  id: number | string;
  data: TColumn;
}

export interface MoveEvent<TItem extends KanbanItem> {
  item: TItem;
  fromColumnId: number | string;
  toColumnId: number | string;
  newIndex: number;
}

export interface KanbanBoardProps<TItem extends KanbanItem, TColumn> {
  columns: KanbanColumnDef<TColumn>[];
  items: Record<string | number, TItem[]>;
  onMove: (event: MoveEvent<TItem>) => Promise<void>;
  canDrop?: (item: TItem, targetColumnId: number | string) => boolean;
  renderColumn: (column: KanbanColumnDef<TColumn>, children: ReactNode) => ReactNode;
  renderCard: (item: TItem, columnId: string | number, isDragging: boolean) => ReactNode;
  renderEmptyColumn?: (column: KanbanColumnDef<TColumn>) => ReactNode;
  /**
   * Classes for the board's scroll container. `KanbanBoard` marks this element
   * `data-kanban-scroller` and `collision.ts` measures ITS box to decide where the board really
   * shows its cards and lanes — so this className must carry the board's overflow (today both
   * consumers pass `overflow-x-auto`). Without one the element is as wide as its content, the clip
   * measures the whole board rather than the visible part, and hit-testing silently reverts to
   * treating off-screen geometry as real.
   */
  className?: string;
  columnClassName?: string;
  /**
   * Disable drag-and-drop: `true` for the whole board (touch/mobile layouts, a bulk
   * operation in flight), or a predicate consulted per card (issue #83 — an archived deal
   * stays visible so it can be restored, but must not drag). See `./dragDisabled`.
   */
  dragDisabled?: DragDisabled<TItem>;
  /** Ref to the scrolling board container (for scroll-snap sync/jump). */
  scrollerRef?: Ref<HTMLDivElement>;
}
