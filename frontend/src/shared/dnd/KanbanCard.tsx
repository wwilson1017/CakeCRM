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

  // Only spread the pointer/touch drag `listeners`, never useSortable's
  // `attributes`: this app registers no KeyboardSensor, so those attributes
  // (role="button", tabIndex, aria-roledescription) would add a non-functional
  // keyboard/AT tab stop that also nests inside the card's own interactive
  // element. Consumers make the rendered card keyboard-operable themselves.
  // (Keyboard drag-and-drop is future work.) When drag is disabled we drop the
  // listeners too — dnd-kit's aria-disabled would otherwise block clicks.
  const dragProps = dragDisabled ? {} : { ...listeners };

  return (
    <div ref={setNodeRef} style={style} {...dragProps}>
      {renderCard(item, columnId, false)}
    </div>
  );
}
