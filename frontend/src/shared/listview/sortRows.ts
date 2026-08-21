/**
 * Order a list view's rows for a `SortState`.
 *
 * This is NOT a comparator — the one comparator lives in `shared/search/sort.ts`
 * (`sortItems`), and the blueprint deleted the duplicate that used to sit in this folder. What
 * remains here is the piece that is genuinely the list view's own: adapting a `ListColumn`
 * table into that comparator's field table, plus the two "render unsorted" escapes (no sort
 * at all, and a sort key no column claims).
 *
 * It is a module rather than a `useMemo` body because `ListView` is no longer the only
 * caller. A page whose detail panel navigates with ‹ › must walk the SAME order its table is
 * rendering (`shared/collection`'s `CollectionDetail` takes it as `navOrder`), and
 * re-deriving that at the call site is exactly the drift this repo keeps paying for. A sibling
 * surface's dashboard-filtered list is the first such caller.
 *
 * Render caps are deliberately NOT applied: `ListView` slices AFTER sorting, and ‹ › walks
 * the whole sorted set — the same rule `shared/collection/visibleOrder` states for the
 * views it flattens.
 */
import { sortItems, type SortFieldDef } from '../search';
import type { ListColumn, SortState } from './types';

export default function sortRows<TItem>(
  columns: ListColumn<TItem>[],
  items: TItem[],
  sort: SortState | null,
): TItem[] {
  if (!sort) return items;
  const active = columns.find(c => c.key === sort.key);
  // A sort key no column claims (a search-bar-only field, or natural order) simply renders
  // unsorted with no header indicator — by design.
  if (!active?.sortValue) return items;
  // Adapt the active column into the one-field table the shared comparator takes.
  // `sortValue` may yield `undefined`; the shared getter contract is `null` for absent, and
  // `sortItems` already treats `== null` as bottom, so normalise `undefined` → `null` to
  // satisfy the type without changing order.
  const getValue = active.sortValue;
  const field: SortFieldDef<TItem> = {
    value: sort.key,
    label: sort.key,
    get: (item: TItem) => getValue(item) ?? null,
  };
  return sortItems(items, [field], { field: sort.key, dir: sort.dir });
}
