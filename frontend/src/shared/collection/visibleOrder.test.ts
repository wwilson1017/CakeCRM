import { describe, expect, it } from 'vitest';
import visibleOrder from './visibleOrder';
import type { CollectionConfig } from './types';

interface Row {
  id: number;
  stage: number;
  bucket: string;
}

const config: CollectionConfig<Row> = {
  storage: { key: 'vo_test', version: 1 },
  defaultView: 'list',
  getItemId: r => r.id,
  searchText: r => [r.bucket],
  list: { columns: [] },
  kanban: { getColumnId: r => r.stage },
  cards: { getTitle: r => String(r.id), getSection: r => r.bucket },
};

const items: Row[] = [
  { id: 10, stage: 2, bucket: 'B' },
  { id: 11, stage: 1, bucket: 'A' },
  { id: 12, stage: 2, bucket: 'B' },
  { id: 13, stage: 3, bucket: 'A' },
];

describe('visibleOrder', () => {
  it('list navigates in the visible (filtered+sorted) order itself', () => {
    expect(visibleOrder('list', config, items, items)).toEqual([10, 11, 12, 13]);
  });

  it('kanban is column-major in the APP-SUPPLIED column order, not numeric key order', () => {
    // Column order 2,1,3 — deliberately not ascending, to pin that Object-key numeric
    // enumeration can never leak in (ids here are numeric-like, the ListView-banned trap).
    expect(visibleOrder('kanban', config, items, items, [2, 1, 3])).toEqual([10, 12, 11, 13]);
  });

  it('kanban skips columns the app did not declare and items in undeclared columns', () => {
    expect(visibleOrder('kanban', config, items, items, [1])).toEqual([11]);
  });

  it('cards is section-major in first-appearance order', () => {
    expect(visibleOrder('cards', config, items, items)).toEqual([10, 12, 11, 13]);
  });

  it('cards without getSection is the flat visible order', () => {
    const flat: CollectionConfig<Row> = {
      ...config,
      cards: { getTitle: r => String(r.id) },
    };
    expect(visibleOrder('cards', flat, items, items)).toEqual([10, 11, 12, 13]);
  });

  it('kanban with no kanban block yields no order', () => {
    const noKanban: CollectionConfig<Row> = { ...config, kanban: undefined };
    expect(visibleOrder('kanban', noKanban, items, items, [1, 2, 3])).toEqual([]);
  });
});
