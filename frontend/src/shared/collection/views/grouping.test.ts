import { describe, expect, it } from 'vitest';
import { groupCardSections, groupKanbanItems } from './grouping';

interface Row {
  id: number;
  col: string;
  section: string;
}

const rows: Row[] = [
  { id: 1, col: 'a', section: 'S1' },
  { id: 2, col: 'a', section: 'S2' },
  { id: 3, col: 'b', section: 'S1' },
  { id: 4, col: 'a', section: 'S2' },
];

describe('groupKanbanItems', () => {
  it('groups per column in item order, wrapping ids via getItemId', () => {
    const { byColumn, hiddenCounts } = groupKanbanItems(
      rows,
      r => r.col,
      r => r.id,
      50,
      new Set(),
    );
    expect(byColumn.a.map(w => w.id)).toEqual([1, 2, 4]);
    expect(byColumn.a[0].item).toBe(rows[0]);
    expect(byColumn.b.map(w => w.id)).toEqual([3]);
    expect(hiddenCounts.size).toBe(0);
  });

  it('caps an over-cap column and reports the hidden count', () => {
    const { byColumn, hiddenCounts } = groupKanbanItems(
      rows,
      r => r.col,
      r => r.id,
      2,
      new Set(),
    );
    expect(byColumn.a.map(w => w.id)).toEqual([1, 2]);
    expect(hiddenCounts.get('a')).toBe(1);
    expect(hiddenCounts.has('b')).toBe(false);
  });

  it('an expanded column renders fully — the hook truncation rule, mirrored exactly', () => {
    const { byColumn, hiddenCounts } = groupKanbanItems(
      rows,
      r => r.col,
      r => r.id,
      2,
      new Set(['a']),
    );
    expect(byColumn.a.map(w => w.id)).toEqual([1, 2, 4]);
    expect(hiddenCounts.size).toBe(0);
  });

  it('a column exactly AT the cap is not truncated (strictly-greater, like the hook)', () => {
    const two = rows.filter(r => r.id !== 4);
    const { byColumn, hiddenCounts } = groupKanbanItems(
      two,
      r => r.col,
      r => r.id,
      2,
      new Set(),
    );
    expect(byColumn.a.map(w => w.id)).toEqual([1, 2]);
    expect(hiddenCounts.size).toBe(0);
  });
});

describe('groupCardSections', () => {
  it('sections in first-appearance order, items in visible order within each', () => {
    const grouped = groupCardSections(rows, r => r.section);
    expect(grouped.map(g => g.section)).toEqual(['S1', 'S2']);
    expect(grouped[0].items.map(r => r.id)).toEqual([1, 3]);
    expect(grouped[1].items.map(r => r.id)).toEqual([2, 4]);
  });

  it('no getSection → one null section holding everything, order preserved', () => {
    const grouped = groupCardSections(rows, undefined);
    expect(grouped).toHaveLength(1);
    expect(grouped[0].section).toBeNull();
    expect(grouped[0].items.map(r => r.id)).toEqual([1, 2, 3, 4]);
  });
});
