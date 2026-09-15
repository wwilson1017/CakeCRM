// The pure date-range rules (#181): a coercion that must never throw on a payload off the
// wire, and an inclusive lexicographic predicate that reads the viewer's local calendar day.
import { describe, expect, it } from 'vitest';
import {
  EMPTY_DATE_RANGE,
  coerceDateRange,
  dateRangeActive,
  dateRangeChipLabel,
  dayInRange,
} from './dateRange';

describe('coerceDateRange', () => {
  it.each([null, undefined, 'x', 5, [], [1, 2], true])('coerces %o to no bounds', raw => {
    expect(coerceDateRange(raw)).toEqual(EMPTY_DATE_RANGE);
  });

  it('keeps two well-formed bounds', () => {
    expect(coerceDateRange({ from: '2026-01-01', to: '2026-03-31' })).toEqual({
      from: '2026-01-01',
      to: '2026-03-31',
    });
  });

  it('validates each bound independently', () => {
    expect(coerceDateRange({ from: '2026-01-01', to: 'later' })).toEqual({
      from: '2026-01-01',
      to: null,
    });
    expect(coerceDateRange({ from: 7, to: '2026-03-31' })).toEqual({
      from: null,
      to: '2026-03-31',
    });
  });

  it.each(['2026-1-5', '26-01-05', '2026/01/05', '2026-01-05T00:00:00Z', ''])(
    'rejects the malformed day %s',
    raw => {
      expect(coerceDateRange({ from: raw })).toEqual(EMPTY_DATE_RANGE);
    },
  );

  it.each(['2026-02-30', '2026-02-31', '2026-13-01', '2026-00-10', '2026-04-31'])(
    'rejects the impossible calendar day %s',
    raw => {
      // A native date input cannot emit these, but a hand-edited saved-view payload can, and
      // they would otherwise sit in the bar as an active filter nothing can ever match.
      expect(coerceDateRange({ from: raw })).toEqual(EMPTY_DATE_RANGE);
    },
  );

  it('rejects 29 February in a common year and keeps it in a leap year', () => {
    expect(coerceDateRange({ from: '2026-02-29' })).toEqual(EMPTY_DATE_RANGE);
    expect(coerceDateRange({ from: '2024-02-29' })).toEqual({ from: '2024-02-29', to: null });
  });
});

describe('dateRangeActive', () => {
  it('is inactive only when both bounds are absent', () => {
    expect(dateRangeActive(EMPTY_DATE_RANGE)).toBe(false);
    expect(dateRangeActive({ from: '2026-01-01', to: null })).toBe(true);
    expect(dateRangeActive({ from: null, to: '2026-01-01' })).toBe(true);
  });
});

describe('dayInRange', () => {
  const q1 = { from: '2026-01-01', to: '2026-03-31' };

  it('includes both endpoints', () => {
    expect(dayInRange('2026-01-01', q1)).toBe(true);
    expect(dayInRange('2026-03-31', q1)).toBe(true);
  });

  it('excludes the days either side', () => {
    expect(dayInRange('2025-12-31', q1)).toBe(false);
    expect(dayInRange('2026-04-01', q1)).toBe(false);
  });

  it('treats a single bound as open-ended on the other side', () => {
    expect(dayInRange('2030-01-01', { from: '2026-01-01', to: null })).toBe(true);
    expect(dayInRange('2000-01-01', { from: '2026-01-01', to: null })).toBe(false);
    expect(dayInRange('2000-01-01', { from: null, to: '2026-01-01' })).toBe(true);
    expect(dayInRange('2030-01-01', { from: null, to: '2026-01-01' })).toBe(false);
  });

  it('never matches an item with no day, even against an open-ended range', () => {
    // The same choice the numeric range facet makes for a null value; "no date" is what the
    // preset facet's own bucket is for.
    expect(dayInRange('', q1)).toBe(false);
    expect(dayInRange('', { from: null, to: '2030-01-01' })).toBe(false);
  });

  it('matches everything when neither bound is set', () => {
    expect(dayInRange('2026-02-02', EMPTY_DATE_RANGE)).toBe(true);
  });

  it('matches nothing for an inverted range rather than swapping the bounds', () => {
    // The chip shows what was typed, so an empty board is legible instead of mysterious.
    const inverted = { from: '2026-03-31', to: '2026-01-01' };
    expect(dayInRange('2026-02-15', inverted)).toBe(false);
    expect(dayInRange('2026-03-31', inverted)).toBe(false);
  });

  it('compares in calendar order across a year boundary', () => {
    expect(dayInRange('2026-01-01', { from: '2025-12-31', to: null })).toBe(true);
    expect(dayInRange('2025-12-31', { from: '2026-01-01', to: null })).toBe(false);
  });
});

describe('dateRangeChipLabel', () => {
  it('renders each of the three shapes', () => {
    expect(dateRangeChipLabel('Close date range', { from: '2026-01-01', to: '2026-03-31' }))
      .toBe('Close date range: 2026-01-01 – 2026-03-31');
    expect(dateRangeChipLabel('Close date range', { from: '2026-01-01', to: null }))
      .toBe('Close date range: ≥ 2026-01-01');
    expect(dateRangeChipLabel('Close date range', { from: null, to: '2026-03-31' }))
      .toBe('Close date range: ≤ 2026-03-31');
  });
});
