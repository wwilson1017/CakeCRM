/**
 * Shared client-side sorting for board and list surfaces.
 *
 * Pure and framework-free. The comparator is CRM's `sortDeals` generalised over a
 * table of `SortFieldDef<T>` rather than a hard-coded switch on one app's fields. Every one
 * of its rules was reasoned separately and every one is preserved verbatim:
 *
 *   • **Nulls sort to the bottom in BOTH directions**, applied before the direction
 *     multiplier. "No value" is not a small value; flipping to descending must not float the
 *     blanks to the top.
 *   • **Ties fall back to the input array's order, never reversed by direction.** The input
 *     is the canonical order the server returned (and, on a draggable board, the order the
 *     user arranged), so a tie is resolved by that rather than left to the engine.
 *   • **An `arrayOrder` field short-circuits to the array's own order** instead of sorting by
 *     a stored column, because that column goes stale the moment an optimistic drag reorders
 *     the array. The array is the source of truth.
 *   • **Comparison uses `<` / `>`, never `String.localeCompare`.** ICU collation gives
 *     punctuation variable weight, which orders same-second ISO timestamps backwards when the
 *     column emits both the with- and without-fraction shapes that Python's `isoformat()`
 *     produces. The plain operators are correct. Do not "modernise" this.
 *   • **Getters return `null`, never `0`, for an absent or unparseable value** — enforced by
 *     `SortFieldDef`'s docstring, since `0` is a real number that sorts to the top ascending.
 *
 * The result is that the blueprint's bug class cannot arise inside this module. It says nothing about
 * the getters callers write; that rule lives on the type.
 */
import type { SortFieldDef, SortState } from './types';

/**
 * Return a new array of `items` ordered by `sort`.
 *
 * `items` must arrive in its canonical order — that order is both the `arrayOrder` result and
 * the tie-break for every other field.
 *
 * An unrecognised `sort.field` (a stale value restored from storage, or a typo) falls back to
 * the input order in BOTH directions rather than throwing or reversing: a direction toggle on
 * a field that does not exist must not silently reorder the board. Same fail-safe posture as
 * the persisted-state coercion.
 */
export function sortItems<T>(items: T[], fields: readonly SortFieldDef<T>[], sort: SortState): T[] {
  const def = fields.find(f => f.value === sort.field);
  if (!def) return items.slice();

  if (def.arrayOrder) {
    return sort.dir === 'asc' ? items.slice() : items.slice().reverse();
  }

  const get = def.get;
  const mul = sort.dir === 'asc' ? 1 : -1;
  return items
    .map((item, i) => ({ item, i }))
    .sort((a, b) => {
      const va = get(a.item);
      const vb = get(b.item);
      // `== null` catches null and undefined both, so a getter that slips an `undefined`
      // through sorts to the bottom rather than comparing as a value.
      const aNull = va == null;
      const bNull = vb == null;
      if (aNull && bNull) return a.i - b.i;
      if (aNull) return 1;
      if (bNull) return -1;

      let cmp: number;
      if (typeof va === 'number' && typeof vb === 'number') cmp = va - vb;
      else cmp = va < vb ? -1 : va > vb ? 1 : 0;

      return cmp !== 0 ? cmp * mul : a.i - b.i;
    })
    .map(x => x.item);
}

/**
 * Is the current selection true manual order — an `arrayOrder` field, ascending?
 *
 * This is the ONE predicate that may gate drag-to-reorder. A drop index is computed against
 * the rows the user can see, so it only maps back to a real position when those rows are in
 * the underlying array's own order. `arrayOrder` *descending* is a reversal, not manual
 * order, and is deliberately excluded.
 *
 * Pass the same value to a sort control's "is this the default" styling so the control and
 * the drag gate cannot drift apart. CRM used to make that coupling itself, with a local
 * `isManualOrder` predicate its sort bar read as `!isManualOrder(sort)` rather than comparing
 * objects; the blueprint deleted both when CRM moved onto `shared/collection`, and `useCollectionState`
 * now calls this once and publishes the result as `state.manualOrder`, which its drag gate and
 * its bar both read.
 */
export function isManualSort<T>(sort: SortState, fields: readonly SortFieldDef<T>[]): boolean {
  const def = fields.find(f => f.value === sort.field);
  return def?.arrayOrder === true && sort.dir === 'asc';
}

/**
 * Coerce anything out of storage into a valid `SortState`.
 *
 * Validates `field` against the caller's own field table and `dir` against the two literals,
 * falling back to `fallback` for anything else — an absent key, a stored `null`, a malformed
 * shape, or a field that has since been removed.
 *
 * Lives here rather than being written per app because it is fully determined by data this
 * module already owns, and because it runs inside a `useState` initialiser during first render
 * where an escaped exception white-screens the page. One implementation, one place to keep
 * total.
 */
export function coerceSortState<T>(
  raw: unknown,
  fields: readonly SortFieldDef<T>[],
  fallback: SortState,
): SortState {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return fallback;
  const s = raw as Partial<SortState>;
  const known = typeof s.field === 'string' && fields.some(f => f.value === s.field);
  return {
    field: known ? (s.field as string) : fallback.field,
    dir: s.dir === 'desc' || s.dir === 'asc' ? s.dir : fallback.dir,
  };
}
