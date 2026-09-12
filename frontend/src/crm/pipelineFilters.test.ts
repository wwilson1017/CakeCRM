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
import { EMPTY_ADVANCED, dealMatchesAdvanced, isArchivedDeal, localDayOf, matchesActivityPreset, matchesCreatedPreset, ymd, type AdvancedFilters } from './pipelineFilters';

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

describe('localDayOf — the exported TIMESTAMPTZ → local calendar day helper (#181)', () => {
  it('reads the VIEWER\'s local day, where a slice(0,10) would take the UTC one', () => {
    // 03:30 UTC on the 16th is 21:30 on the 15th in America/Chicago.
    expect(localDayOf('2026-01-16T03:30:00Z')).toBe('2026-01-15');
  });

  it('parses the backend\'s six fractional digits, which bare Date does not guarantee', () => {
    expect(localDayOf('2026-01-15T18:00:00.123456+00:00')).toBe('2026-01-15');
  });

  it('returns an empty day for absent or unparseable input', () => {
    expect(localDayOf(null)).toBe('');
    expect(localDayOf(undefined)).toBe('');
    expect(localDayOf('')).toBe('');
    expect(localDayOf('not a date')).toBe('');
  });
});

describe('matchesCreatedPreset (#181)', () => {
  // Noon avoids any question of which day a boundary lands on.
  const may15 = new Date(2026, 4, 15, 12, 0, 0);
  const jan15 = new Date(2026, 0, 15, 12, 0, 0);
  const at = (day: string) => `${day}T18:00:00Z`;

  it('last7 spans today and the seven preceding dates — the same boundary as le7', () => {
    expect(matchesCreatedPreset(at('2026-05-15'), 'last7', may15)).toBe(true);
    expect(matchesCreatedPreset(at('2026-05-08'), 'last7', may15)).toBe(true);
    expect(matchesCreatedPreset(at('2026-05-07'), 'last7', may15)).toBe(false);
  });

  it('last30 spans today and the thirty preceding dates', () => {
    expect(matchesCreatedPreset(at('2026-04-15'), 'last30', may15)).toBe(true);
    expect(matchesCreatedPreset(at('2026-04-14'), 'last30', may15)).toBe(false);
  });

  it('thisMonth keeps the calendar month only', () => {
    expect(matchesCreatedPreset(at('2026-05-01'), 'thisMonth', may15)).toBe(true);
    expect(matchesCreatedPreset(at('2026-05-31'), 'thisMonth', may15)).toBe(true);
    expect(matchesCreatedPreset(at('2026-04-30'), 'thisMonth', may15)).toBe(false);
  });

  it('thisQuarter starts at the first day of the containing quarter', () => {
    expect(matchesCreatedPreset(at('2026-04-01'), 'thisQuarter', may15)).toBe(true);
    expect(matchesCreatedPreset(at('2026-03-31'), 'thisQuarter', may15)).toBe(false);
  });

  it('lastQuarter is the whole previous quarter and excludes the current one', () => {
    expect(matchesCreatedPreset(at('2026-01-01'), 'lastQuarter', may15)).toBe(true);
    expect(matchesCreatedPreset(at('2026-03-31'), 'lastQuarter', may15)).toBe(true);
    expect(matchesCreatedPreset(at('2026-04-01'), 'lastQuarter', may15)).toBe(false);
    expect(matchesCreatedPreset(at('2025-12-31'), 'lastQuarter', may15)).toBe(false);
  });

  it('normalises lastQuarter across the year boundary without a branch', () => {
    // Q1 of 2026 → Q4 of 2025, via a negative month the Date constructor rolls over.
    expect(matchesCreatedPreset(at('2025-10-01'), 'lastQuarter', jan15)).toBe(true);
    expect(matchesCreatedPreset(at('2025-12-31'), 'lastQuarter', jan15)).toBe(true);
    expect(matchesCreatedPreset(at('2025-09-30'), 'lastQuarter', jan15)).toBe(false);
    expect(matchesCreatedPreset(at('2026-01-01'), 'lastQuarter', jan15)).toBe(false);
    expect(matchesCreatedPreset(at('2026-01-01'), 'thisQuarter', jan15)).toBe(true);
  });

  it('never matches an absent or unparseable creation timestamp', () => {
    expect(matchesCreatedPreset(null, 'last7', may15)).toBe(false);
    expect(matchesCreatedPreset('rubbish', 'thisQuarter', may15)).toBe(false);
  });
});

describe('dealMatchesAdvanced with the created bucket', () => {
  const may15 = new Date(2026, 4, 15, 12, 0, 0);

  it('still matches everything under EMPTY_ADVANCED, now that a third key exists', () => {
    expect(dealMatchesAdvanced(deal(), EMPTY_ADVANCED, may15)).toBe(true);
  });

  it('ANDs the created bucket with the other two', () => {
    const d = deal({
      created_at: '2026-05-10T12:00:00Z',
      expected_close_date: '2026-05-20',
      last_activity_at: '2026-05-14T12:00:00Z',
    });
    expect(dealMatchesAdvanced(d, adv({ createdDate: 'last7', closeDate: 'thisMonth' }), may15)).toBe(true);
    // Same deal, a created bucket it falls outside — the AND fails even though close still matches.
    expect(dealMatchesAdvanced(d, adv({ createdDate: 'lastQuarter', closeDate: 'thisMonth' }), may15)).toBe(false);
  });

  it('leaves the close-date and activity rules untouched', () => {
    const won = deal({ stage: 'won', expected_close_date: '2020-01-01' });
    expect(dealMatchesAdvanced(won, adv({ closeDate: 'overdue' }), may15)).toBe(false);
  });
});
