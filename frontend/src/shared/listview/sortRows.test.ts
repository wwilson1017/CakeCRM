/**
 * `sortRows` — the column→comparator adapter `ListView` renders through and a
 * page reuses to compute its detail panel's ‹ › order.
 *
 * The point of the extraction is that those two callers cannot order differently, so these
 * cases pin the behaviour ListView's own memo used to own: the unsorted escapes, the
 * absent-value rule, and stability.
 */
import { describe, expect, it } from 'vitest';
import sortRows from './sortRows';
import type { ListColumn } from './types';

interface Row {
  id: number;
  name: string;
  cost: number | null;
}

const rows: Row[] = [
  { id: 1, name: 'beta', cost: 10 },
  { id: 2, name: 'alpha', cost: null },
  { id: 3, name: 'gamma', cost: 5 },
];

const columns: ListColumn<Row>[] = [
  { key: 'name', header: 'Name', render: r => r.name, sortValue: r => r.name },
  { key: 'cost', header: 'Cost', render: r => r.cost, sortValue: r => r.cost },
  // Display-only: no `sortValue`, so it can never claim a sort key.
  { key: 'actions', header: '', render: () => null },
];

const ids = (items: Row[]) => items.map(r => r.id);

describe('sortRows', () => {
  it('orders by the active column in both directions', () => {
    expect(ids(sortRows(columns, rows, { key: 'name', dir: 'asc' }))).toEqual([2, 1, 3]);
    expect(ids(sortRows(columns, rows, { key: 'name', dir: 'desc' }))).toEqual([3, 1, 2]);
  });

  it('returns the rows untouched for natural order', () => {
    expect(sortRows(columns, rows, null)).toBe(rows);
  });

  it('renders unsorted when no column claims the sort key', () => {
    // A search-bar-only field, and a display-only column — neither can order the table, and
    // neither may throw or silently reorder.
    expect(sortRows(columns, rows, { key: 'submitted_by', dir: 'asc' })).toBe(rows);
    expect(sortRows(columns, rows, { key: 'actions', dir: 'asc' })).toBe(rows);
  });

  it('sorts an absent value to the BOTTOM in both directions', () => {
    // The shared getter contract: `null`/`undefined` is "no value", never a low number.
    expect(ids(sortRows(columns, rows, { key: 'cost', dir: 'asc' }))).toEqual([3, 1, 2]);
    expect(ids(sortRows(columns, rows, { key: 'cost', dir: 'desc' }))).toEqual([1, 3, 2]);
  });

  it('is stable across ties, so board order survives as the tie-break', () => {
    const tied: Row[] = [
      { id: 7, name: 'same', cost: 1 },
      { id: 8, name: 'same', cost: 2 },
      { id: 9, name: 'same', cost: 3 },
    ];
    expect(ids(sortRows(columns, tied, { key: 'name', dir: 'asc' }))).toEqual([7, 8, 9]);
  });

  it('does not mutate the input array', () => {
    const input = [...rows];
    sortRows(columns, input, { key: 'name', dir: 'asc' });
    expect(ids(input)).toEqual([1, 2, 3]);
  });
});
