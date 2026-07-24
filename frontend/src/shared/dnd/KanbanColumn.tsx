import { useDroppable } from '@dnd-kit/core';
import { SortableContext, verticalListSortingStrategy } from '@dnd-kit/sortable';
import { useMemo } from 'react';
import type { ReactNode } from 'react';
import type { KanbanItem, KanbanColumnDef } from './types';
import KanbanCard from './KanbanCard';

interface KanbanColumnProps<TItem extends KanbanItem, TColumn> {
  column: KanbanColumnDef<TColumn>;
  items: TItem[];
  renderCard: (item: TItem, columnId: string | number, isDragging: boolean) => ReactNode;
  renderEmptyColumn?: (column: KanbanColumnDef<TColumn>) => ReactNode;
  className?: string;
  dragDisabled?: boolean;
}

export default function KanbanColumn<TItem extends KanbanItem, TColumn>({
  column, items, renderCard, renderEmptyColumn, className, dragDisabled,
}: KanbanColumnProps<TItem, TColumn>) {
  const { setNodeRef } = useDroppable({
    id: `column-${column.id}`,
    data: { type: 'column', columnId: column.id },
  });

  const itemIds = useMemo(() => items.map(item => `card-${item.id}`), [items]);

  return (
    <div ref={setNodeRef} className={className}>
      <SortableContext items={itemIds} strategy={verticalListSortingStrategy}>
        {items.length === 0 && renderEmptyColumn?.(column)}
        {items.map(item => (
          <KanbanCard key={item.id} item={item} columnId={column.id} renderCard={renderCard} dragDisabled={dragDisabled} />
        ))}
      </SortableContext>
    </div>
  );
}
