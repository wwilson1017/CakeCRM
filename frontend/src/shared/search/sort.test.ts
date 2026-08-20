import { describe, it, expect } from 'vitest';
import { sortItems, isManualSort } from './sort';
import type { SortFieldDef } from './types';

/**
 * These cover the comparator half of the dev-only `import.meta.env.DEV` self-check that used to
 * live in `apps/crm/pipelineSort.ts`, written when the frontend had no test runner. It has one
 * now, so the block is gone and every one of its checks is a real test — the comparator
 * rules here and the CRM getters in `pipelineSort.test.ts`. (That file's persistence half went
 * away in the blueprint: CRM's sort now persists through `shared/collection`, which coerces a restored
 * value with `coerceSortState` — covered by `persist.test.ts`.)
 */

interface Row { id: number; touches: number | null; value: number; date: string }

const row = (id: number, over: Partial<Row> = {}): Row =>
  ({ id, touches: 0, value: 0, date: '', ...over });

const FIELDS: readonly SortFieldDef<Row>[] = [
  { value: 'manual', label: 'Manual order', arrayOrder: true },
  { value: 'touches', label: 'Touches', get: r => (typeof r.touches === 'number' ? r.touches : null) },
  { value: 'value', label: 'Value', get: r => r.value },
  { value: 'date', label: 'Date', get: r => r.date || null },
];

const ids = (rows: Row[]) => rows.map(r => r.id).join(',');

describe('sortItems', () => {
  it('sorts nulls to the bottom in BOTH directions, and treats 0 as a real value', () => {
    const rows = [row(1, { touches: 5 }), row(2, { touches: null }), row(3, { touches: 0 })];
    expect(ids(sortItems(rows, FIELDS, { field: 'touches', dir: 'asc' }))).toBe('3,1,2');
    expect(ids(sortItems(rows, FIELDS, { field: 'touches', dir: 'desc' }))).toBe('1,3,2');
  });

  it('breaks ties by input order, never reversed by direction', () => {
    const rows = [row(1, { value: 10 }), row(2, { value: 10 }), row(3, { value: 5 })];
    expect(ids(sortItems(rows, FIELDS, { field: 'value', dir: 'asc' }))).toBe('3,1,2');
    expect(ids(sortItems(rows, FIELDS, { field: 'value', dir: 'desc' }))).toBe('1,2,3');
  });

  it('compares date strings with < / > so same-second ISO shapes order chronologically', () => {
 // The defect: `localeCompare` gives punctuation variable weight and orders these
    // two backwards, because Python's isoformat() omits the fraction at exactly .000000.
    const a = '2026-07-30T12:00:00+00:00';
    const b = '2026-07-30T12:00:00.500000+00:00';
    expect(a.localeCompare(b)).toBe(1);          // what the wrong comparator would say
    const rows = [row(1, { date: b }), row(2, { date: a })];
    expect(ids(sortItems(rows, FIELDS, { field: 'date', dir: 'asc' }))).toBe('2,1');
  });

  it('sorts an empty date to the bottom, not to the top', () => {
    const rows = [row(1, { date: '2026-01-01' }), row(2, { date: '' }), row(3, { date: '2025-06-01' })];
    expect(ids(sortItems(rows, FIELDS, { field: 'date', dir: 'asc' }))).toBe('3,1,2');
  });

  it('short-circuits an arrayOrder field to the input order, reversing only for desc', () => {
    const rows = [row(7), row(3), row(9)];
    expect(ids(sortItems(rows, FIELDS, { field: 'manual', dir: 'asc' }))).toBe('7,3,9');
    expect(ids(sortItems(rows, FIELDS, { field: 'manual', dir: 'desc' }))).toBe('9,3,7');
  });

  it('falls back to input order for an unknown field in BOTH directions', () => {
    // A stale field name restored from sessionStorage must not reorder or reverse anything.
    const rows = [row(7), row(3), row(9)];
    expect(ids(sortItems(rows, FIELDS, { field: 'gone', dir: 'asc' }))).toBe('7,3,9');
    expect(ids(sortItems(rows, FIELDS, { field: 'gone', dir: 'desc' }))).toBe('7,3,9');
  });

  it('returns a new array and never mutates the input', () => {
    const rows = [row(1, { value: 2 }), row(2, { value: 1 })];
    const out = sortItems(rows, FIELDS, { field: 'value', dir: 'asc' });
    expect(out).not.toBe(rows);
    expect(ids(rows)).toBe('1,2');
  });
});

describe('isManualSort', () => {
  it('is true only for an arrayOrder field ascending — the one drag-safe state', () => {
    expect(isManualSort({ field: 'manual', dir: 'asc' }, FIELDS)).toBe(true);
    expect(isManualSort({ field: 'manual', dir: 'desc' }, FIELDS)).toBe(false);
    expect(isManualSort({ field: 'value', dir: 'asc' }, FIELDS)).toBe(false);
  });

  it('is false for an unknown field, so a stale value cannot re-enable drag', () => {
    expect(isManualSort({ field: 'gone', dir: 'asc' }, FIELDS)).toBe(false);
  });
});
