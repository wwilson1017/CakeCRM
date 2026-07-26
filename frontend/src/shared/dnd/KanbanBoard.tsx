import { useState, useCallback, useMemo } from 'react';
import {
  DndContext,
  DragOverlay,
  closestCorners,
  type DragStartEvent,
  type DragOverEvent,
  type DragEndEvent,
} from '@dnd-kit/core';
import type { KanbanItem, KanbanBoardProps } from './types';
import KanbanColumn from './KanbanColumn';
import useKanbanState from './useKanbanState';
import { useDndSensors } from './sensors';

export default function KanbanBoard<TItem extends KanbanItem, TColumn>({
  columns,
  items: externalItems,
  onMove,
  canDrop,
  renderColumn,
  renderCard,
  renderEmptyColumn,
  className,
  columnClassName,
  dragDisabled,
  scrollerRef,
}: KanbanBoardProps<TItem, TColumn>) {
  const { items, startDrag, moveItem, commitMove, rollback, findItem, findColumnForItem } = useKanbanState(externalItems);
  const [activeId, setActiveId] = useState<string | number | null>(null);
  const [sourceColumn, setSourceColumn] = useState<string | null>(null);
  // Drag disabling happens per-card (useSortable disabled + no listeners), not
  // by swapping the sensors array — DndContext uses it as an effect dependency
  // array, so its size must stay constant across renders.
  const sensors = useDndSensors();

  const resolveColumnId = useCallback((overId: string | undefined, overData: Record<string, unknown> | undefined): string | null => {
    if (!overId) return null;
    if (overData?.type === 'column') return String(overData.columnId);
    if (overData?.type === 'card') return String(overData.columnId);
    if (String(overId).startsWith('column-')) return String(overId).slice(7);
    return findColumnForItem(String(overId).replace('card-', '')) ?? null;
  }, [findColumnForItem]);

  const handleDragStart = useCallback((event: DragStartEvent) => {
    const data = event.active.data.current;
    setActiveId(data?.itemId ?? null);
    setSourceColumn(data?.columnId != null ? String(data.columnId) : null);
    startDrag();
  }, [startDrag]);

  const handleDragOver = useCallback((event: DragOverEvent) => {
    const { active, over } = event;
    if (!over || !active.data.current) return;

    const activeItemId = active.data.current.itemId;
    const fromCol = findColumnForItem(activeItemId);
    const toCol = resolveColumnId(String(over.id), over.data.current as Record<string, unknown> | undefined);
    if (!fromCol || !toCol) return;

    if (canDrop) {
      const item = findItem(activeItemId);
      if (item && !canDrop(item, toCol)) return;
    }

    if (fromCol === toCol) {
      const colItems = items[toCol] || [];
      const overData = over.data.current as Record<string, unknown> | undefined;
      if (overData?.type === 'card') {
        const overIndex = colItems.findIndex(i => String(i.id) === String(overData.itemId));
        if (overIndex !== -1) {
          moveItem(activeItemId, fromCol, toCol, overIndex);
        }
      }
    } else {
      const overData = over.data.current as Record<string, unknown> | undefined;
      let newIndex = (items[toCol] || []).length;
      if (overData?.type === 'card') {
        const overIndex = (items[toCol] || []).findIndex(i => String(i.id) === String(overData.itemId));
        if (overIndex !== -1) newIndex = overIndex;
      }
      moveItem(activeItemId, fromCol, toCol, newIndex);
    }
  }, [items, findColumnForItem, findItem, resolveColumnId, canDrop, moveItem]);

  const handleDragEnd = useCallback(async (event: DragEndEvent) => {
    const { active, over } = event;
    setActiveId(null);

    if (!over || !active.data.current) {
      rollback();
      return;
    }

    const activeItemId = active.data.current.itemId;
    const toCol = resolveColumnId(String(over.id), over.data.current as Record<string, unknown> | undefined);

    if (!toCol) {
      rollback();
      return;
    }

    if (canDrop) {
      const item = findItem(activeItemId);
      if (item && !canDrop(item, toCol)) {
        rollback();
        return;
      }
    }

    const toItems = items[toCol] || [];
    const newIndex = toItems.findIndex(i => String(i.id) === String(activeItemId));
    const finalIndex = newIndex === -1 ? toItems.length : newIndex;
    const item = findItem(activeItemId);

    if (!item) {
      rollback();
      return;
    }

    const fromCol = sourceColumn || '';
    await commitMove(onMove, { item, fromColumnId: fromCol, toColumnId: toCol, newIndex: finalIndex });
    setSourceColumn(null);
  }, [items, sourceColumn, resolveColumnId, canDrop, findItem, rollback, commitMove, onMove]);

  const handleDragCancel = useCallback(() => {
    setActiveId(null);
    setSourceColumn(null);
    rollback();
  }, [rollback]);

  const activeItem = useMemo(() => activeId != null ? findItem(activeId) : undefined, [activeId, findItem]);

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCorners}
      onDragStart={handleDragStart}
      onDragOver={handleDragOver}
      onDragEnd={handleDragEnd}
      onDragCancel={handleDragCancel}
      autoScroll={{ enabled: true }}
    >
      <div className={className} ref={scrollerRef}>
        {columns.map(col => {
          const colItems = items[String(col.id)] || [];
          return renderColumn(col, (
            <KanbanColumn
              column={col}
              items={colItems}
              renderCard={renderCard}
              renderEmptyColumn={renderEmptyColumn}
              className={columnClassName}
              dragDisabled={dragDisabled}
            />
          ));
        })}
      </div>

      {!dragDisabled && (
        <DragOverlay dropAnimation={{ duration: 200, easing: 'ease' }}>
          {activeItem ? (
            <div className="shadow-lg rotate-[2deg] scale-105">
              {renderCard(activeItem, sourceColumn || '', true)}
            </div>
          ) : null}
        </DragOverlay>
      )}
    </DndContext>
  );
}
