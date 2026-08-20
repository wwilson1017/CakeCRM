/**
 * Pure grouping/bounding helpers for the collection views. Their own module —
 * not exported from the component files — because react-refresh requires component files to
 * export only components, and because these are the render-bounding rules worth unit-testing
 * without a DOM.
 */

export interface WrappedItem<T> {
  id: string | number;
  item: T;
}

/**
 * Group + cap the board's items per column. The cap semantics MUST match
 * `useCollectionState`'s `truncatedColumns` (count > cap && !expanded ⇒ slice to cap) —
 * a truncated column is EXACTLY a capped-rendered column, which is what makes the hook's
 * truncated-column drag lock honest.
 */
export function groupKanbanItems<T>(
  items: readonly T[],
  getColumnId: (item: T) => string | number,
  getItemId: (item: T) => string | number,
  cap: number,
  expandedColumns: ReadonlySet<string | number>,
): {
  byColumn: Record<string | number, WrappedItem<T>[]>;
  hiddenCounts: Map<string | number, number>;
} {
  const full = new Map<string | number, WrappedItem<T>[]>();
  for (const item of items) {
    const col = getColumnId(item);
    const list = full.get(col);
    const wrapped = { id: getItemId(item), item };
    if (list) list.push(wrapped);
    else full.set(col, [wrapped]);
  }
  const byColumn: Record<string | number, WrappedItem<T>[]> = {};
  const hiddenCounts = new Map<string | number, number>();
  for (const [col, list] of full) {
    if (list.length > cap && !expandedColumns.has(col)) {
      byColumn[col] = list.slice(0, cap);
      hiddenCounts.set(col, list.length - cap);
    } else {
      byColumn[col] = list;
    }
  }
  return { byColumn, hiddenCounts };
}

/** Group visible items by section in first-appearance order — the same order `visibleOrder`
 *  flattens for ‹ › nav. `section: null` means "no sections configured" (one flat grid). */
export function groupCardSections<T>(
  items: readonly T[],
  getSection: ((item: T) => string) | undefined,
): { section: string | null; items: T[] }[] {
  if (!getSection) return [{ section: null, items: [...items] }];
  const bySection = new Map<string, T[]>();
  for (const item of items) {
    const section = getSection(item);
    const list = bySection.get(section);
    if (list) list.push(item);
    else bySection.set(section, [item]);
  }
  return [...bySection.entries()].map(([section, sectionItems]) => ({
    section,
    items: sectionItems,
  }));
}
