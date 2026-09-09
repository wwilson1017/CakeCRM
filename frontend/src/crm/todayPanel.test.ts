import { describe, expect, it } from 'vitest';

import type { CrmTodayItem } from '../core/types';
import {
  TODAY_COLLAPSED, coerceTodayScope, collapseToday, msUntilRefresh, whyBadge,
} from './todayPanel';

function task(id: number, why: 'starred' | 'overdue' | 'due_today' = 'due_today'): CrmTodayItem {
  return { kind: 'task', id, rank: why === 'starred' ? 1 : why === 'overdue' ? 3 : 5, why,
           title: `t${id}`, due_date: '2026-06-05', owner_id: null };
}

describe('collapseToday', () => {
  it('shows every row and hides nothing when the list fits', () => {
    const items = [task(1), task(2)];
    expect(collapseToday(items, false)).toEqual({ visible: items, hiddenCount: 0 });
  });

  it('caps at five and reports the remainder', () => {
    const items = Array.from({ length: 7 }, (_, i) => task(i));
    const { visible, hiddenCount } = collapseToday(items, false);
    expect(visible).toHaveLength(TODAY_COLLAPSED);
    expect(hiddenCount).toBe(2);
  });

  it('reveals everything when expanded', () => {
    const items = Array.from({ length: 7 }, (_, i) => task(i));
    expect(collapseToday(items, true)).toEqual({ visible: items, hiddenCount: 0 });
  });

  it('derives both halves from the SAME array, so the count cannot disagree', () => {
    const items = Array.from({ length: 9 }, (_, i) => task(i));
    const { visible, hiddenCount } = collapseToday(items, false);
    expect(visible.length + hiddenCount).toBe(items.length);
  });
});

describe('coerceTodayScope', () => {
  it('defaults to mine for absent or junk values', () => {
    expect(coerceTodayScope(null)).toBe('mine');
    expect(coerceTodayScope('nonsense')).toBe('mine');
    expect(coerceTodayScope({ scope: 'everyone' })).toBe('mine');
  });

  it('round-trips the two real values', () => {
    expect(coerceTodayScope('mine')).toBe('mine');
    expect(coerceTodayScope('everyone')).toBe('everyone');
  });
});

describe('whyBadge', () => {
  it('labels each rung of the ladder distinctly', () => {
    const labels = (['starred', 'overdue', 'due_today'] as const).map(w => whyBadge(task(1, w)).label);
    expect(labels).toEqual(['STARRED', 'OVERDUE', 'DUE TODAY']);
    expect(whyBadge({ kind: 'reminder', id: 'r', rank: 4, why: 'reminder',
                      title: 'x', due_at: '' }).label).toBe('REMINDER');
  });
});

describe('msUntilRefresh', () => {
  const now = Date.parse('2026-06-05T12:00:00Z');

  it('waits until the server boundary', () => {
    expect(msUntilRefresh('2026-06-05T17:00:00Z', now)).toBe(5 * 60 * 60 * 1000);
  });

  it('never arms a negative delay for a boundary already passed', () => {
    // A payload that sat in a backgrounded tab. A negative delay fires immediately and
    // would spin; the floor makes it refetch promptly instead.
    expect(msUntilRefresh('2026-06-05T09:00:00Z', now)).toBe(30 * 1000);
  });

  it('clamps an absurd delay below the setTimeout 32-bit overflow', () => {
    // Past ~24.8 days setTimeout wraps and fires at once, forever.
    expect(msUntilRefresh('2099-01-01T00:00:00Z', now)).toBe(25 * 60 * 60 * 1000);
  });

  it('falls back to an hour when the timestamp is unparseable', () => {
    expect(msUntilRefresh('not-a-date', now)).toBe(60 * 60 * 1000);
  });
});
