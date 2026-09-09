/**
 * The collection layer's board view — a composition over `shared/dnd`'s
 * KanbanBoard, owning exactly the three things the board deliberately does not:
 *
 *  • **Item identity.** `shared/dnd` requires `{id}` items; collection items are arbitrary
 *    `T` with `getItemId`, so every item is wrapped `{id, item}` on the way in and unwrapped
 *    in every slot and the move event on the way out — the app's callbacks never see the
 *    wrapper.
 *  • **Render bounding.** Per-column cap with "Show N more" (`expandColumn`). The slice here
 *    and the hook's `truncatedColumns` computation share one cap constant, so a truncated
 *    column is EXACTLY a capped-rendered column — which is what makes the hook's
 *    truncated-column drag lock honest (a drop against a partially rendered column is
 *    ambiguous).
 *  • **The drag gate.** `dragDisabled = state.dragLocked || props.dragDisabled` — the layer's
 *    central gate OR'd with app extras (isMobile, bulkPending), never replaced by them. The
 *    app extra may be a PER-CARD predicate (issue #83), which is unwrapped here like every
 *    other item-shaped slot; a board-wide `dragLocked` still collapses it to `true`.
 */
import { useMemo, type ReactNode } from 'react';
import { KanbanBoard } from '../../dnd';
import type { KanbanColumnDef, MoveEvent } from '../../dnd';
import { KANBAN_COLUMN_CAP } from '../useCollectionState';
import { groupKanbanItems } from './grouping';
import type { WrappedItem as Wrapped } from './grouping';
import type { CollectionConfig, CollectionKanbanProps, CollectionState } from '../types';

export default function KanbanView<T, C>({
  config,
  state,
  kanban,
}: {
  config: CollectionConfig<T>;
  state: CollectionState<T>;
  kanban: CollectionKanbanProps<T, C>;
}) {
  const kanbanConfig = config.kanban;
  const cap = kanbanConfig?.columnCap ?? KANBAN_COLUMN_CAP;

  const { byColumn, hiddenCounts } = useMemo(() => {
    if (!kanbanConfig) {
      return {
        byColumn: {} as Record<string | number, Wrapped<T>[]>,
        hiddenCounts: new Map<string | number, number>(),
      };
    }
    return groupKanbanItems(
      state.kanbanItems,
      kanbanConfig.getColumnId,
      config.getItemId,
      cap,
      state.expandedColumns,
    );
  }, [kanbanConfig, state.kanbanItems, config, cap, state.expandedColumns]);

  if (!kanbanConfig) return null;

  const appCanDrop = kanban.canDrop;
  const renderColumn = (column: KanbanColumnDef<C>, children: ReactNode) => {
    const hidden = hiddenCounts.get(column.id) ?? 0;
    return kanban.renderColumn(
      column,
      hidden > 0 ? (
        <>
          {children}
          <button
            type="button"
            onClick={() => state.expandColumn(column.id)}
            className="mt-1 w-full rounded-lg border border-dashed border-line py-1.5 text-xs text-muted hover:bg-sand hover:text-charcoal"
          >
            Show {hidden} more
          </button>
        </>
      ) : (
        children
      ),
    );
  };

  return (
    <KanbanBoard<Wrapped<T>, C>
      columns={kanban.columns}
      items={byColumn}
      onMove={(event: MoveEvent<Wrapped<T>>) =>
        kanban.onMove({
          item: event.item.item,
          fromColumnId: event.fromColumnId,
          toColumnId: event.toColumnId,
          newIndex: event.newIndex,
        })
      }
      canDrop={
        appCanDrop ? (wrapped, targetColumnId) => appCanDrop(wrapped.item, targetColumnId) : undefined
      }
      renderColumn={renderColumn}
      renderCard={(wrapped, columnId, isDragging) => kanban.renderCard(wrapped.item, columnId, isDragging)}
      renderEmptyColumn={kanban.renderEmptyColumn}
      // Unwrapped like `canDrop` and `renderCard`: the app's predicate is written against `T`,
      // but `shared/dnd` calls it with the `{id, item}` wrapper. `dragLocked` is checked FIRST
      // and collapses to a literal `true` — it is a board-wide claim, and `||`-ing it into a
      // predicate would produce a function, which `boardDragDisabled` (correctly) does not read
      // as board-wide, leaving the drag overlay mounted for a board that cannot drag at all.
      dragDisabled={
        state.dragLocked || kanban.dragDisabled === true
          ? true
          : typeof kanban.dragDisabled === 'function'
            ? (wrapped: Wrapped<T>) => (kanban.dragDisabled as (item: T) => boolean)(wrapped.item)
            : false
      }
      className={kanban.className}
      columnClassName={kanban.columnClassName}
      scrollerRef={kanban.scrollerRef}
    />
  );
}
