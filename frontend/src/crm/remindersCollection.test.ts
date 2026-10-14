import { describe, expect, it } from 'vitest';

import type { CrmReminder } from '../core/types';
import type { CustomFacetDef, SingleFacetDef } from '../shared/collection';
import {
  REMINDER_SORT_FIELDS, changedFields, isMine, makeRemindersCollectionConfig, reminderWhen,
} from './remindersCollection';

function reminder(over: Partial<CrmReminder> = {}): CrmReminder {
  return {
    id: 1, owner_id: 3, created_by: 3, note: 'Call back', people: [3], due_date: '2026-10-14',
    due_time: null, due_at: '2026-10-14T14:00:00+00:00', repeat: null, anchor_date: '2026-10-14',
    series_id: null, status: 'upcoming', void_reason: '', fired_at: null, voided_at: null,
    created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z', ...over,
  };
}

const config = makeRemindersCollectionConfig({ columns: [], meId: 3 });
const facets = config.facets ?? [];

describe('reminders collection config', () => {
  it('keeps its storage identity and facet keys stable (saved views depend on them)', () => {
    expect(config.storage).toEqual({ key: 'crm_reminders', version: 1 });
    expect(facets.map(f => f.key)).toEqual(['scope', 'status', 'dueRange']);
  });

  it('Mine means I set it or I am one of its people', () => {
    expect(isMine(reminder({ owner_id: 3, people: [4] }), 3)).toBe(true);
    expect(isMine(reminder({ owner_id: 4, people: [3, 5] }), 3)).toBe(true);
    expect(isMine(reminder({ owner_id: 4, people: [5] }), 3)).toBe(false);
    expect(isMine(reminder(), null)).toBe(false);
    const scope = facets[0] as SingleFacetDef<CrmReminder>;
    expect(scope.predicate(reminder({ owner_id: 4, people: [5] }), 'mine')).toBe(false);
  });

  it('filters the due date range inclusively on the date-only string', () => {
    const range = facets[2] as CustomFacetDef<CrmReminder, unknown>;
    const v = { from: '2026-10-14', to: '2026-10-15' };
    expect(range.predicate(reminder({ due_date: '2026-10-14' }), v)).toBe(true);
    expect(range.predicate(reminder({ due_date: '2026-10-16' }), v)).toBe(false);
  });

  it('sorts by the due INSTANT, not the string', () => {
    const due = REMINDER_SORT_FIELDS[0];
    // 09:00-05:00 is 14:00Z, later than 13:30Z although it sorts first as a string.
    const a = reminder({ due_at: '2026-10-14T09:00:00-05:00' });
    const b = reminder({ due_at: '2026-10-14T13:30:00+00:00' });
    expect(due.get(a)).toBeGreaterThan(due.get(b) as number);
  });
});

describe('reminderWhen', () => {
  it('names the day and the time, or the morning for a date-only reminder', () => {
    expect(reminderWhen({ due_date: '2026-10-14', due_time: '15:00' }, '2026-10-14')).toBe('Today · 15:00');
    expect(reminderWhen({ due_date: '2026-10-15', due_time: null }, '2026-10-14')).toBe('Tomorrow · morning');
  });
});

describe('changedFields', () => {
  it('sends only what changed, and the date and time together', () => {
    const before = reminder({ people: [3, 4] });
    const same = { note: before.note, people: [4, 3], repeat: null, due_date: before.due_date, due_time: null };
    expect(changedFields(before, same)).toEqual({});
    expect(changedFields(before, { ...same, note: 'New' })).toEqual({ note: 'New' });
    expect(changedFields(before, { ...same, due_time: '16:00' }))
      .toEqual({ due_date: '2026-10-14', due_time: '16:00' });
  });
});
