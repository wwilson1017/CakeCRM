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
 *
 * The move forwarded below is always the full four-field event, and that is deliberate: this
 * component is the event's PRODUCER, so it emits the superset, while
 * `CollectionKanbanProps<T, C, P>` decides what the CONSUMER may name — under
 * `dragPolicy: 'column'` its `onMove` parameter has no `newIndex` member at all (issue #112).
 * The two meet by parameter contravariance and nothing here needs a cast; the prop type below
 * records why it is spelled the way it is, and `KanbanViewConfig.dragPolicy` records what the
 * runtime superset does and does not guarantee.
 */
import { useMemo, type ReactNode } from 'react';
import { KanbanBoard } from '../../dnd';
import type { KanbanColumnDef, MoveEvent } from '../../dnd';
import { KANBAN_COLUMN_CAP } from '../useCollectionState';
import { groupKanbanItems } from './grouping';
import type { WrappedItem as Wrapped } from './grouping';
import type {
  CollectionConfig,
  CollectionKanbanProps,
  CollectionMoveEvent,
  CollectionState,
  DragPolicy,
} from '../types';

/**
 * This component is INTERNAL and deliberately not generic in the drag policy: the policy
 * contract belongs to the consumer-facing seam (`CollectionViewProps`), and this component is
 * the event's PRODUCER, so it holds both props at their widest. `config` reads only
 * `getColumnId`/`columnCap` and so takes either policy; `onMove` is declared at the `'index'`
 * superset this component emits, which any `CollectionKanbanProps<T, C, P>` satisfies by
 * ordinary parameter contravariance.
 *
 * Two shapes were tried first and are recorded so they are not re-tried:
 *  • Generic `<T, C, P>` does not work. `P` occurs only inside
 *    `CollectionMoveEventByPolicy<T>[P]`, and TypeScript cannot infer a type argument back out
 *    of an indexed access, so `P` silently falls back to its constraint and rejects every
 *    well-typed board.
 *  • Naming the whole prop `CollectionKanbanProps<T, C, 'index'>` does not work either, for a
 *    subtler reason: `P` is contravariant-only in that interface, so TypeScript compares two
 *    references to it by VARIANCE rather than structurally, and asks for `'index'` assignable
 *    to a generic `P`. Restating `onMove` beside an `Omit` of the rest forces the structural
 *    comparison, which succeeds — the same assignment a plain function-typed variable accepts.
 */
type KanbanViewOwnProps<T, C> = Omit<CollectionKanbanProps<T, C, DragPolicy>, 'onMove'> & {
  onMove: (event: CollectionMoveEvent<T, 'index'>) => Promise<void>;
};

export default function KanbanView<T, C>({
  config,
  state,
  kanban,
}: {
  config: CollectionConfig<T, DragPolicy>;
  state: CollectionState<T>;
  kanban: KanbanViewOwnProps<T, C>;
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
