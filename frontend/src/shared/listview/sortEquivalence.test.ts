/**
 * Regression guard for an earlier deduplication.
 *
 * The blueprint deleted `listview/sort.ts`'s `sortRows` and re-pointed `ListView` at the
 * ONE shared comparator, `shared/search/sort.ts`'s `sortItems`. The whole safety
 * of that change rests on one claim: for the shape `ListView` calls it with — a
 * single non-`arrayOrder` field whose getter is the column's `sortValue` — the
 * new path orders EXACTLY as the deleted `sortRows` did.
 *
 * This pins that claim so a future edit to either comparator that breaks the
 * equivalence fails CI, instead of silently reintroducing the two-copies drift
 * the deduplication removed. The oracle below is the deleted `sortRows` body,
 * reproduced verbatim; the four load-bearing semantics it encodes (nulls to the
 * bottom in both directions, ties by input order never reversed, `0` a real
 * value, `<`/`>` never `localeCompare` — the blueprint trap) are the same ones
 * `shared/search/sort.test.ts` pins on the surviving side.
 */
import { describe, expect, it } from 'vitest';
import { sortItems, type SortFieldDef } from '../search';
import type { SortDir, SortValue } from './types';

// The deleted `listview/sort.ts` comparator, kept here as the oracle.
function sortRows<T>(rows: readonly T[], getValue: (row: T) => SortValue, dir: SortDir): T[] {
  const mul = dir === 'asc' ? 1 : -1;
  return rows
    .map((row, i) => ({ row, i }))
    .sort((a, b) => {
      const va = getValue(a.row);
      const vb = getValue(b.row);
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
    .map(x => x.row);
}

// The new path exactly as `ListView`'s sort useMemo applies it: one field,
// getter = the column's `sortValue`, `undefined` normalised to `null`.
function listViewSort<T>(items: T[], getValue: (row: T) => SortValue, dir: SortDir): T[] {
  const field: SortFieldDef<T> = {
    value: 'k',
    label: 'k',
    get: (item: T) => getValue(item) ?? null,
  };
  return sortItems(items, [field], { field: 'k', dir });
}

interface Row {
  id: number;
  n?: number | null;
  s?: string | null;
}
const ids = (rows: Row[]) => rows.map(r => r.id).join(',');
const byN = (r: Row): SortValue => (typeof r.n === 'number' ? r.n : undefined);
const byS = (r: Row): SortValue => r.s || null;

const numberCases: Row[][] = [
  [{ id: 1, n: 5 }, { id: 2, n: null }, { id: 3, n: 0 }], // nulls bottom + 0 is real
  [{ id: 1 }, { id: 2, n: 4 }], // undefined behaves like null
  [{ id: 1, n: 10 }, { id: 2, n: 10 }, { id: 3, n: 5 }], // ties by input order
];
const stringCases: Row[][] = [
  // The same-second ISO-timestamp trap: `localeCompare` would invert these.
  [
    { id: 1, s: '2026-07-30T12:00:00.500000+00:00' },
    { id: 2, s: '2026-07-30T12:00:00+00:00' },
  ],
  [{ id: 1, s: 'banana' }, { id: 2, s: '' }, { id: 3, s: 'apple' }], // '' → null → bottom
];

describe('ListView sort path is identical to the deleted sortRows', () => {
  for (const dir of ['asc', 'desc'] as const) {
    numberCases.forEach((rows, i) => {
      it(`number case ${i} (${dir})`, () => {
        expect(ids(listViewSort(rows.slice(), byN, dir))).toBe(ids(sortRows(rows.slice(), byN, dir)));
      });
    });
    stringCases.forEach((rows, i) => {
      it(`string case ${i} (${dir})`, () => {
        expect(ids(listViewSort(rows.slice(), byS, dir))).toBe(ids(sortRows(rows.slice(), byS, dir)));
      });
    });
  }

  it('does not mutate its input (as sortRows did not)', () => {
    const rows: Row[] = [{ id: 1, n: 3 }, { id: 2, n: 1 }];
    listViewSort(rows, byN, 'asc');
    expect(ids(rows)).toBe('1,2');
  });
});
