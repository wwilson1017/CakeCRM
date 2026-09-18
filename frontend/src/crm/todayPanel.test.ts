import { describe, expect, it } from 'vitest';

import type { CrmTodayDealItem, CrmTodayTaskItem } from '../core/types';
import {
  TODAY_COLLAPSED, coerceTodayScope, collapseToday, dealEvidence, msUntilRefresh, whyBadge,
} from './todayPanel';

function task(id: number, why: 'starred' | 'overdue' | 'due_today' = 'due_today'): CrmTodayTaskItem {
  return { kind: 'task', id, rank: why === 'starred' ? 1 : why === 'overdue' ? 3 : 4, why,
           title: `t${id}`, due_date: '2026-06-05', owner_id: null };
}

/** An unranked hot deal — the row the issue says belongs behind the expander and nowhere
 *  else. `rank: 2` is the stale variant, which behaves like any other ranked row. */
function hotDeal(id: number, rank: 2 | null = null): CrmTodayDealItem {
  return { kind: 'deal', id, rank, why: rank === 2 ? 'hot_stale' : 'hot',
           title: `deal ${id}`, value: 1000, days_since_touch: 3, owner_id: null };
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

  it('reveals everything when expanded, and still reports what the expander holds', () => {
    const items = Array.from({ length: 7 }, (_, i) => task(i));
    // hiddenCount answers "what would the expander reveal", not "what is hidden right
    // now" — it is what tells the panel whether to offer Show less.
    expect(collapseToday(items, true)).toEqual({ visible: items, hiddenCount: 2 });
  });

  it('derives both halves from the SAME array, so the count cannot disagree', () => {
    const items = Array.from({ length: 9 }, (_, i) => task(i));
    const { visible, hiddenCount } = collapseToday(items, false);
    expect(visible.length + hiddenCount).toBe(items.length);
  });

  it('never shows an unranked hot deal in the collapsed card', () => {
    // The issue's rule, and the case a sixth rank could not have covered: one commitment
    // beside one recently-touched hot deal, well under the five-row cap.
    const items = [task(1, 'overdue'), hotDeal(9)];
    expect(collapseToday(items, false)).toEqual({ visible: [items[0]], hiddenCount: 1 });
  });

  it('reveals the unranked rows once expanded', () => {
    const items = [task(1, 'overdue'), hotDeal(9)];
    expect(collapseToday(items, true)).toEqual({ visible: items, hiddenCount: 1 });
  });

  it('shows a hot+stale deal in the collapsed card like any other ranked row', () => {
    const items = [hotDeal(9, 2), task(1, 'overdue')];
    expect(collapseToday(items, false)).toEqual({ visible: items, hiddenCount: 0 });
  });

  it('fills all five slots from ranked rows even when unranked ones sit among them', () => {
    // Slicing the first five ITEMS would spend a slot on the deal and drop task 5.
    const items = [...Array.from({ length: 5 }, (_, i) => task(i)), hotDeal(9)];
    const { visible, hiddenCount } = collapseToday(items, false);
    expect(visible.map(i => i.id)).toEqual([0, 1, 2, 3, 4]);
    expect(hiddenCount).toBe(1);
  });

  it('collapses to nothing when every row is unranked', () => {
    const items = [hotDeal(8), hotDeal(9)];
    expect(collapseToday(items, false)).toEqual({ visible: [], hiddenCount: 2 });
  });
});

describe('dealEvidence', () => {
  it('reads as the issue spells it: idle days, then value', () => {
    expect(dealEvidence(12, 30000)).toBe('idle 12d · $30K');
  });

  it('uses the app-wide compact form for large and small numbers alike', () => {
    expect(dealEvidence(1, 1_200_000)).toBe('idle 1d · $1.2M');
    expect(dealEvidence(0, 750)).toBe('idle 0d · $750');
  });

  it('never renders a negative idle count', () => {
    expect(dealEvidence(-3, 0)).toBe('idle 0d · $0');
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
