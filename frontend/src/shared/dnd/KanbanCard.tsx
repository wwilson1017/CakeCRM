import { useSortable } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import type { ReactNode } from 'react';
import type { KanbanItem } from './types';

interface KanbanCardProps<TItem extends KanbanItem> {
  item: TItem;
  columnId: string | number;
  renderCard: (item: TItem, columnId: string | number, isDragging: boolean) => ReactNode;
  dragDisabled?: boolean;
}

export default function KanbanCard<TItem extends KanbanItem>({ item, columnId, renderCard, dragDisabled }: KanbanCardProps<TItem>) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({
    id: `card-${item.id}`,
    data: { type: 'card', itemId: item.id, columnId },
    disabled: dragDisabled,
  });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.4 : 1,
  };

  // When drag is disabled, skip the sortable attributes entirely — dnd-kit
  // still emits aria-disabled="true", which blocks clicks for AT and Playwright.
  const dragProps = dragDisabled ? {} : { ...attributes, ...listeners };

  return (
    <div ref={setNodeRef} style={style} {...dragProps}>
      {renderCard(item, columnId, false)}
    </div>
  );
}
