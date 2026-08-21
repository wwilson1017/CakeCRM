/**
 * The list view's column-header click cycle.
 *
 * This is NOT a comparator — the shared comparator lives once in
 * `shared/search/sort.ts` (`sortItems`), and `ListView` orders its rows through
 * it. What remains here is the small piece that is genuinely the list view's
 * own: how a header click advances the `{key, dir}` selection.
 *
 * It stays separate from `shared/search`'s `coerceSortState` / `isManualSort`
 * because those answer different questions (restore-from-storage, drag-gate)
 * over the bar's `{field, dir}` shape; this is a three-state UI toggle over the
 * column-header `{key, dir}` shape. Neither is a substitute for the other.
 */
import type { SortState } from './types';

/**
 * The header-click cycle: inactive → ascending → descending → cleared.
 *
 * Returning to `null` (natural board order) is a deliberate third state rather
 * than a two-way asc/desc toggle: for a board-backed list, "no sort" is a real
 * and reachable order the user should be able to get back to without hunting
 * for a reset control.
 */
export function nextSortState(current: SortState | null, key: string): SortState | null {
  if (!current || current.key !== key) return { key, dir: 'asc' };
  if (current.dir === 'asc') return { key, dir: 'desc' };
  return null;
}
