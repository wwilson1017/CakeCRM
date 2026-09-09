/**
 * The #21 facet predicate had no test suite. #74 rewrote everything around it — the filter
 * bar, the persistence, the page — so this pins the rules that survived, before the port can
 * quietly change one. Stage, owner and value are NOT here: since #74 they are plain
 * `FacetDef`s the collection layer evaluates, and `pipelineCollection.test.ts` pins their
 * semantics at that new home. What remains is the pair whose rules are genuinely non-obvious.
 *
 * The timezone matters here: `vitest.config.ts` pins `TZ: 'America/Chicago'` precisely so
 * `ymd`'s local-date getters are distinguishable from `toISOString()`. Under a UTC runner the
 * first test below could not fail.
 */
import { describe, expect, it } from 'vitest';
import type { CrmDeal } from '../core/types';
import { EMPTY_ADVANCED, dealMatchesAdvanced, isArchivedDeal, matchesActivityPreset, ymd, type AdvancedFilters } from './pipelineFilters';

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

describe('the two buckets combine with AND', () => {
  const now = new Date(2026, 4, 15, 12, 0, 0);
  const daysAgo = (n: number) => new Date(2026, 4, 15 - n, 12, 0, 0).toISOString();

  it('a deal must satisfy both active buckets, not either', () => {
    const f = adv({ closeDate: 'overdue', lastActivity: 'stale30' });
    const match = deal({ stage: 'proposal', expected_close_date: '2026-05-01', last_activity_at: daysAgo(40) });
    expect(dealMatchesAdvanced(match, f, now)).toBe(true);
    // Break each half in turn.
    expect(dealMatchesAdvanced({ ...match, expected_close_date: '2026-06-30' }, f, now)).toBe(false);
    expect(dealMatchesAdvanced({ ...match, last_activity_at: daysAgo(2) }, f, now)).toBe(false);
  });
});

describe('activity timestamps parse through parseUTC, not bare Date', () => {
  const now = new Date(2026, 4, 15, 12, 0, 0); // Fri 2026-05-15 12:00 local (CDT, UTC-5)

  it('reads a zone-less timestamp as UTC, which can move it across a bucket boundary', () => {
    // The falsifiable half of the fix. `le7`'s boundary here is 2026-05-08. A Postgres
    // TIMESTAMPTZ rendered without a zone suffix MEANS UTC — but `new Date()` reads it as
    // LOCAL, which lands it on the 8th (inside the window) instead of the 7th (outside).
    // parseUTC appends the Z, so the deal is correctly outside `le7`.
    const naive = '2026-05-08T02:00:00';
    expect(dealMatchesAdvanced(
      deal({ last_activity_at: naive }), adv({ lastActivity: 'le7' }), now,
    )).toBe(false);
    expect(dealMatchesAdvanced(
      deal({ last_activity_at: naive }), adv({ lastActivity: 'stale30' }), now,
    )).toBe(false); // still inside 30 days — only the 7-day edge moved
  });

  it('handles the SIX-fractional-digit form the backend actually emits', () => {
    // Honest note: this one CANNOT fail on Node — V8 parses microseconds happily. It is
    // Safari that only guarantees three digits, and the build targets Safari, which is why
    // parseUTC truncates. Kept as a statement of the input shape, not as a regression guard.
    const sixDigits = '2026-05-13T09:15:30.123456+00:00';
    expect(dealMatchesAdvanced(
      deal({ last_activity_at: sixDigits }), adv({ lastActivity: 'le7' }), now,
    )).toBe(true);
  });

  it('exposes the bucket rule for other surfaces without duplicating it', () => {
    // #77's Contacts list filters `last_contact_at` through this same export, so the two
    // surfaces cannot drift about where "stale" begins.
    expect(matchesActivityPreset('2026-05-13T09:15:30.123456+00:00', 'le7', now)).toBe(true);
    expect(matchesActivityPreset(null, 'none', now)).toBe(true);
    expect(matchesActivityPreset(null, 'stale30', now)).toBe(true);
  });
});

// ── Archived deals (issue #83) ───────────────────────────────────────────────
// The THREE-STATE facet itself moved to `pipelineCollection.ts` with #74 and is pinned in
// that file's suite; what stays here is the one predicate the board reads directly —
// the money aggregates, the bulk payload, the select-all ids and the per-card drag gate
// all call `isArchivedDeal`, none of them through a facet.
describe('isArchivedDeal', () => {
  it('reads a timestamp as archived and null/absent as live', () => {
    expect(isArchivedDeal(deal({ archived_at: '2026-08-20T00:00:00+00:00' }))).toBe(true);
    expect(isArchivedDeal(deal({ archived_at: null }))).toBe(false);
    expect(isArchivedDeal(deal())).toBe(false);
  });
});
