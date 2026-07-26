import type { ReactNode, Ref } from 'react';

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
  className?: string;
  columnClassName?: string;
  /** Disable drag-and-drop entirely (e.g. on touch/mobile layouts). */
  dragDisabled?: boolean;
  /** Ref to the scrolling board container (for scroll-snap sync/jump). */
  scrollerRef?: Ref<HTMLDivElement>;
}
