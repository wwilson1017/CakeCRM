/**
 * The collection layer's list view — a thin adapter over `shared/listview`'s
 * ListView, NOT a table of its own.
 *
 * Three jobs live here and nowhere else:
 *
 *  • **The SortState bridge.** The kit has two sort shapes by design — `shared/search` says
 *    `{field, dir}` (the bar's dropdown), `shared/listview` says `{key, dir}` (the column
 *    headers) — and this adapter is the ONE place they meet: header clicks map to the layer's
 *    `setSort`, and `nextSortState`'s `null` ("natural order") maps to the config's resting
 *    sort. Column keys and sort-field values share a namespace on purpose: a `ListColumn.key`
 *    equal to a `SortFieldDef.value` IS that field's header.
 *
 *  • **The sortValue overlay.** Columns never declare their own `sortValue` under this layer —
 *    each is derived from the matching sort field's `get`, so the header sort and the hook's
 *    `sortItems` order by the SAME getter and cannot disagree about row order (which the
 *    detail's ‹ › navigation depends on). ListView re-sorting the hook's already-sorted rows
 *    with the same getter is idempotent — same nulls-to-bottom, same stable ties.
 *
 *  • **Selection.** The optional checkbox column (leading), select-all over the VISIBLE
 *    (filtered, not merely rendered) set, and per-row toggles that stop propagation so a
 *    checkbox click is never also a row-open.
 */
import { useMemo } from 'react';
import { ListView } from '../../listview';
import type { ListColumn, SortState as ListSortState } from '../../listview';
import { voidedRowClass } from '../voidedRowClass';
import { restingSort } from '../useCollectionState';
import type {
  CollectionConfig,
  CollectionSelectionProps,
  CollectionState,
} from '../types';

interface Row<T> {
  id: string | number;
  item: T;
}

export default function CollectionListView<T>({
  config,
  state,
  selection,
  onSelect,
}: {
  config: CollectionConfig<T>;
  state: CollectionState<T>;
  selection?: CollectionSelectionProps;
  onSelect?: (id: string | number | null) => void;
}) {
  const list = config.list;
  const rows = useMemo<Row<T>[]>(
    () => state.visibleItems.map(item => ({ id: config.getItemId(item), item })),
    [state.visibleItems, config],
  );
  const visibleIds = useMemo(() => new Set(rows.map(r => r.id)), [rows]);

  // Destructured so the `columns` memo below depends on the two FIELDS it reads, never on the
  // `selection` object. Every consumer passes that object as an inline literal (it carries a
  // render prop, so hoisting it is awkward), which as an identity dependency made `columns` a
  // fresh array every render — and `ListView`'s `sorted` memo keys on `columns`, so a checkbox
  // click or a detail open re-sorted the ENTIRE client corpus. Invisible at 50 server-paginated
  // rows; not at CRM's assembled thousands.
  const selectedIds = selection?.selectedIds;
  const onSelectionChange = selection?.onChange;

  const columns = useMemo<ListColumn<Row<T>>[]>(() => {
    const fieldByKey = new Map(
      (config.sort?.fields ?? [])
        .filter(f => !f.arrayOrder)
        .map(f => [f.value, f] as const),
    );
    const wrapped: ListColumn<Row<T>>[] = (list?.columns ?? []).map(col => {
      const field = fieldByKey.get(col.key);
      return {
        key: col.key,
        header: col.header,
        className: col.className,
        align: col.align,
        render: row => col.render(row.item),
        ...(field ? { sortValue: (row: Row<T>) => field.get(row.item) } : {}),
      };
    });
    if (!selectedIds || !onSelectionChange) return wrapped;

    const onChange = onSelectionChange;
    const allSelected = rows.length > 0 && rows.every(r => selectedIds.has(r.id));
    return [
      {
        key: '__select',
        header: (
          <input
            type="checkbox"
            aria-label="Select all visible"
            checked={allSelected}
            onChange={() => {
              const next = new Set(selectedIds);
              if (allSelected) for (const id of visibleIds) next.delete(id);
              else for (const id of visibleIds) next.add(id);
              onChange(next);
            }}
            className="accent-brand"
          />
        ),
        className: 'w-8',
        render: row => (
          <input
            type="checkbox"
            aria-label="Select row"
            checked={selectedIds.has(row.id)}
            // Stop the ROW click (open detail) — a checkbox toggle is not a row-open.
            onClick={e => e.stopPropagation()}
            onChange={() => {
              const next = new Set(selectedIds);
              if (next.has(row.id)) next.delete(row.id);
              else next.add(row.id);
              onChange(next);
            }}
            className="accent-brand"
          />
        ),
      },
      ...wrapped,
    ];
  }, [list, config, selectedIds, onSelectionChange, rows, visibleIds]);

  if (!list) return null;

  const getVoided = config.getVoided;
  const resting = restingSort(config.sort);
  // Collapse the resting order to "natural" (no header indicator) ONLY when the resting field is
  // the `arrayOrder` one — a board-backed surface, where the hook's array order genuinely IS the
  // order and no column claims that field.
  //
 // The `arrayOrder` test is load-bearing, not decoration. `nextSortState(null, key)`
  // always returns `asc`, so on a LIST-ONLY surface — whose resting sort is a real field like
  // `name asc` — reporting `null` made every click on that header ask for `name asc` again:
  // descending was unreachable and the column looked unsorted while the rows were sorted. CRM
  // Contacts/Companies are the first consumers this bites, which is how it surfaced.
  //
  // No shipped adopter regresses on the change, but NOT because they all rest on `arrayOrder` —
  // three of the four do (a pipeline board, the blueprint `catalog`, a sibling surface `board`) and
  // Auto Issues does not: it is list-only and rests on `action asc`, a plain getter. It is
  // unaffected for a different reason — `action` is not one of its `list.columns` keys, so no
  // header binds to it, `ListView` finds no column claiming the key and renders the rows
  // unsorted exactly as `null` did, and no header shows an indicator. Check BOTH conditions
  // before assuming the next consumer is safe; "they all use arrayOrder" is not true.
  const restingIsArrayOrder =
    (config.sort?.fields ?? []).some(f => f.value === resting.field && f.arrayOrder === true);
  const listSort: ListSortState | null =
    restingIsArrayOrder && state.sort.field === resting.field && state.sort.dir === resting.dir
      ? null
      : { key: state.sort.field, dir: state.sort.dir };

  return (
    <ListView<Row<T>>
      columns={columns}
      items={rows}
      sort={listSort}
      onSortChange={next =>
        state.setSort(next ? { field: next.key, dir: next.dir } : resting)
      }
      onRowClick={row => onSelect?.(row.id)}
      emptyMessage={config.emptyState?.message}
      renderCap={list.renderCap}
      rowClassName={getVoided ? row => voidedRowClass(getVoided(row.item)) : undefined}
    />
  );
}
