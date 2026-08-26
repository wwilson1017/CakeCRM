/**
 * The #21 facet predicate had no test suite. #74 rewrote everything around it — the filter
 * bar, the persistence, the page — so this pins the rules that survived, before the port can
 * quietly change one.
 *
 * The timezone matters here: `vitest.config.ts` pins `TZ: 'America/Chicago'` precisely so
 * `ymd`'s local-date getters are distinguishable from `toISOString()`. Under a UTC runner the
 * first test below could not fail.
 */
import { describe, expect, it } from 'vitest';
import type { CrmDeal } from '../core/types';
import { EMPTY_ADVANCED, dealMatchesAdvanced, ymd, type AdvancedFilters } from './pipelineFilters';

function deal(over: Partial<CrmDeal> = {}): CrmDeal {
  return {
    id: 1,
    contact_id: null,
    company_id: null,
    title: 'Deal',
    stage: 'lead',
    value: 1000,
    notes: '',
    expected_close_date: '',
    probability: 50,
    currency: 'USD',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...over,
  };
}

const adv = (over: Partial<AdvancedFilters>): AdvancedFilters => ({ ...EMPTY_ADVANCED, ...over });

describe('ymd — local dates, not UTC', () => {
  it('uses the LOCAL calendar day, where toISOString() would already be tomorrow', () => {
    // 22:00 in America/Chicago is 04:00 UTC the next day.
    const evening = new Date(2026, 0, 15, 22, 0, 0);
    expect(ymd(evening)).toBe('2026-01-15');
    expect(evening.toISOString().slice(0, 10)).toBe('2026-01-16');
  });

  it('offsets by whole calendar days across a DST spring-forward', () => {
    // 2026-03-08 is the US DST transition; a naive +24h would land on the 8th at 01:00.
    expect(ymd(new Date(2026, 2, 7, 23, 30, 0), 1)).toBe('2026-03-08');
  });

  it('zero-pads month and day', () => {
    expect(ymd(new Date(2026, 1, 3))).toBe('2026-02-03');
  });
});

describe('dealMatchesAdvanced — no active facet matches everything', () => {
  it('matches with EMPTY_ADVANCED', () => {
    expect(dealMatchesAdvanced(deal(), EMPTY_ADVANCED, new Date())).toBe(true);
  });
});

describe('stage facet', () => {
  it('includes a listed stage and excludes an unlisted one', () => {
    const f = adv({ stages: ['qualified', 'proposal'] });
    expect(dealMatchesAdvanced(deal({ stage: 'qualified' }), f, new Date())).toBe(true);
    expect(dealMatchesAdvanced(deal({ stage: 'lead' }), f, new Date())).toBe(false);
  });
});

describe('owner facet', () => {
  it("'unassigned' matches BOTH a null owner_id and an absent one", () => {
    const f = adv({ owners: ['unassigned'] });
    expect(dealMatchesAdvanced(deal({ owner_id: null }), f, new Date())).toBe(true);
    expect(dealMatchesAdvanced(deal(), f, new Date())).toBe(true);
    expect(dealMatchesAdvanced(deal({ owner_id: 7 }), f, new Date())).toBe(false);
  });

  it('a numeric owner matches only that owner, and not the unassigned bucket', () => {
    const f = adv({ owners: [7] });
    expect(dealMatchesAdvanced(deal({ owner_id: 7 }), f, new Date())).toBe(true);
    expect(dealMatchesAdvanced(deal({ owner_id: 8 }), f, new Date())).toBe(false);
    expect(dealMatchesAdvanced(deal({ owner_id: null }), f, new Date())).toBe(false);
  });
});

describe('value range', () => {
  it('is inclusive at both bounds', () => {
    const f = adv({ valueMin: 100, valueMax: 200 });
    expect(dealMatchesAdvanced(deal({ value: 100 }), f, new Date())).toBe(true);
    expect(dealMatchesAdvanced(deal({ value: 200 }), f, new Date())).toBe(true);
    expect(dealMatchesAdvanced(deal({ value: 99 }), f, new Date())).toBe(false);
    expect(dealMatchesAdvanced(deal({ value: 201 }), f, new Date())).toBe(false);
  });

  it('a one-sided bound leaves the other end open', () => {
    expect(dealMatchesAdvanced(deal({ value: 10_000 }), adv({ valueMin: 500 }), new Date())).toBe(true);
    expect(dealMatchesAdvanced(deal({ value: 10 }), adv({ valueMax: 500 }), new Date())).toBe(true);
  });
});

describe('close-date buckets', () => {
  const now = new Date(2026, 4, 15, 12, 0, 0); // Fri 2026-05-15, local

  it('overdue counts OPEN deals only — a won deal with a past date is not overdue', () => {
    const f = adv({ closeDate: 'overdue' });
    expect(dealMatchesAdvanced(deal({ expected_close_date: '2026-05-01', stage: 'proposal' }), f, now)).toBe(true);
    expect(dealMatchesAdvanced(deal({ expected_close_date: '2026-05-01', stage: 'won' }), f, now)).toBe(false);
    expect(dealMatchesAdvanced(deal({ expected_close_date: '2026-05-01', stage: 'lost' }), f, now)).toBe(false);
  });

  it('overdue excludes today and the future', () => {
    const f = adv({ closeDate: 'overdue' });
    expect(dealMatchesAdvanced(deal({ expected_close_date: '2026-05-15' }), f, now)).toBe(false);
    expect(dealMatchesAdvanced(deal({ expected_close_date: '2026-05-20' }), f, now)).toBe(false);
  });

  it('next7 spans today through today+7 inclusive', () => {
    const f = adv({ closeDate: 'next7' });
    expect(dealMatchesAdvanced(deal({ expected_close_date: '2026-05-15' }), f, now)).toBe(true);
    expect(dealMatchesAdvanced(deal({ expected_close_date: '2026-05-22' }), f, now)).toBe(true);
    expect(dealMatchesAdvanced(deal({ expected_close_date: '2026-05-23' }), f, now)).toBe(false);
    expect(dealMatchesAdvanced(deal({ expected_close_date: '2026-05-14' }), f, now)).toBe(false);
  });

  it('thisMonth keeps the calendar month and rejects the next one', () => {
    const f = adv({ closeDate: 'thisMonth' });
    expect(dealMatchesAdvanced(deal({ expected_close_date: '2026-05-31' }), f, now)).toBe(true);
    expect(dealMatchesAdvanced(deal({ expected_close_date: '2026-06-01' }), f, now)).toBe(false);
  });

  it('noDate matches an empty close date only', () => {
    const f = adv({ closeDate: 'noDate' });
    expect(dealMatchesAdvanced(deal({ expected_close_date: '' }), f, now)).toBe(true);
    expect(dealMatchesAdvanced(deal({ expected_close_date: '2026-05-20' }), f, now)).toBe(false);
  });
});

describe('last-activity buckets', () => {
  const now = new Date(2026, 4, 15, 12, 0, 0);
  const daysAgo = (n: number) => new Date(2026, 4, 15 - n, 12, 0, 0).toISOString();

  it('le7 and le30 are recency windows', () => {
    expect(dealMatchesAdvanced(deal({ last_activity_at: daysAgo(3) }), adv({ lastActivity: 'le7' }), now)).toBe(true);
    expect(dealMatchesAdvanced(deal({ last_activity_at: daysAgo(20) }), adv({ lastActivity: 'le7' }), now)).toBe(false);
    expect(dealMatchesAdvanced(deal({ last_activity_at: daysAgo(20) }), adv({ lastActivity: 'le30' }), now)).toBe(true);
  });

  it('stale30 INCLUDES never-contacted deals, so le30 and stale30 partition the set', () => {
    const stale = adv({ lastActivity: 'stale30' });
    expect(dealMatchesAdvanced(deal({ last_activity_at: daysAgo(40) }), stale, now)).toBe(true);
    // The subtle one: a deal with no activity at all is stale, not excluded.
    expect(dealMatchesAdvanced(deal({ last_activity_at: null }), stale, now)).toBe(true);
    expect(dealMatchesAdvanced(deal({ last_activity_at: daysAgo(3) }), stale, now)).toBe(false);

    // Partition check: every deal is in exactly one of le30 / stale30.
    const le30 = adv({ lastActivity: 'le30' });
    for (const d of [deal({ last_activity_at: daysAgo(3) }), deal({ last_activity_at: daysAgo(40) }), deal({ last_activity_at: null })]) {
      expect(dealMatchesAdvanced(d, le30, now) !== dealMatchesAdvanced(d, stale, now)).toBe(true);
    }
  });

  it("'none' matches only a deal with no logged activity", () => {
    const f = adv({ lastActivity: 'none' });
    expect(dealMatchesAdvanced(deal({ last_activity_at: null }), f, now)).toBe(true);
    expect(dealMatchesAdvanced(deal(), f, now)).toBe(true);
    expect(dealMatchesAdvanced(deal({ last_activity_at: daysAgo(3) }), f, now)).toBe(false);
  });
});

describe('facets combine with AND', () => {
  const now = new Date(2026, 4, 15, 12, 0, 0);

  it('a deal must satisfy every active facet', () => {
    const f = adv({ stages: ['proposal'], valueMin: 500, closeDate: 'overdue' });
    const match = deal({ stage: 'proposal', value: 900, expected_close_date: '2026-05-01' });
    expect(dealMatchesAdvanced(match, f, now)).toBe(true);
    // Each of the three, broken one at a time.
    expect(dealMatchesAdvanced({ ...match, stage: 'lead' }, f, now)).toBe(false);
    expect(dealMatchesAdvanced({ ...match, value: 100 }, f, now)).toBe(false);
    expect(dealMatchesAdvanced({ ...match, expected_close_date: '2026-06-30' }, f, now)).toBe(false);
  });
});
