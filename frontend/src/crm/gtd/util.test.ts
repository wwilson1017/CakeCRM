import { describe, expect, it } from 'vitest';
import {
  bringBackState, daysSince, dueLabel, formatAge, formatDay, parseTags, parseUTC, repeatLabel,
  todayStr,
} from './util';

// vitest pins TZ=America/Chicago, which is what makes the local-vs-UTC cases below
// meaningful rather than accidentally identical.

describe('todayStr', () => {
  it('uses LOCAL date parts, not the UTC calendar day', () => {
    // 2026-08-21 21:00 Chicago is already 2026-08-22 in UTC. A due date carries the
    // user's local intent, so toISOString() here would mark today's work overdue.
    expect(todayStr(new Date(2026, 7, 21, 21, 0, 0))).toBe('2026-08-21');
  });

  it('pads single-digit months and days', () => {
    expect(todayStr(new Date(2026, 0, 5, 12))).toBe('2026-01-05');
  });
});

describe('parseUTC', () => {
  it('parses a Postgres timestamp with six fraction digits', () => {
    // Three digits is all ECMAScript guarantees; Safari need not accept six, and an
    // Invalid Date would make `NaN >= STALE_DAYS` false — silently dropping a stale
    // todo from the Review page instead of showing it.
    const d = parseUTC('2026-08-21T12:00:00.123456+00:00');
    expect(Number.isNaN(d.getTime())).toBe(false);
    expect(d.toISOString()).toBe('2026-08-21T12:00:00.123Z');
  });

  it('treats a zone-less timestamp as UTC', () => {
    expect(parseUTC('2026-08-21 12:00:00').toISOString()).toBe('2026-08-21T12:00:00.000Z');
  });
});

describe('formatAge', () => {
  const now = new Date(2026, 7, 21, 12);
  const ago = (days: number) =>
    new Date(now.getTime() - days * 86_400_000).toISOString();

  it.each([
    [0, 'today'],
    [3, '3d'],
    [14, '2w'],
    [90, '3mo'],
  ])('renders %i days ago as %s', (days, expected) => {
    expect(formatAge(ago(days), now)).toBe(expected);
  });

  it('never renders a negative age for a future timestamp', () => {
    expect(formatAge(new Date(now.getTime() + 86_400_000).toISOString(), now)).toBe('today');
  });
});

describe('daysSince', () => {
  it('counts whole days elapsed', () => {
    const now = new Date(2026, 7, 21, 12);
    const then = new Date(now.getTime() - 20 * 86_400_000).toISOString();
    expect(daysSince(then, now)).toBe(20);
  });
});

describe('dueLabel', () => {
  it('names today and tomorrow', () => {
    expect(dueLabel('2026-08-21', '2026-08-21')).toEqual({ text: 'Today', overdue: false });
    expect(dueLabel('2026-08-22', '2026-08-21')).toEqual({ text: 'Tomorrow', overdue: false });
  });

  it('flags a past date as overdue', () => {
    expect(dueLabel('2026-08-20', '2026-08-21').overdue).toBe(true);
  });

  it('renders a further date from LOCAL parts', () => {
    // Built from parts, not new Date('YYYY-MM-DD'): the latter parses as UTC midnight
    // and renders the previous day west of Greenwich.
    expect(dueLabel('2026-08-25', '2026-08-21').text).toBe('Tue, Aug 25');
  });

  it('returns an empty label for no due date', () => {
    expect(dueLabel('', '2026-08-21')).toEqual({ text: '', overdue: false });
  });
});

describe('parseTags', () => {
  it('splits, trims and drops blanks', () => {
    expect(parseTags(' a , ,b,  ')).toEqual(['a', 'b']);
  });
});

describe('repeatLabel', () => {
  it.each([
    ['', ''],
    ['daily', 'Daily'],
    ['weekdays', 'Weekdays'],
    ['every:3', 'Every 3 days'],
    ['every:1', 'Daily'],
  ])('renders %s as %s', (value, expected) => {
    expect(repeatLabel(value)).toBe(expected);
  });
});

describe('install-timezone helpers (#259)', () => {
  it('dueLabel calls the day after a DST change "Tomorrow" (a 23-hour day)', () => {
    // Runner is America/Chicago; clocks spring forward on 2026-03-08.
    expect(dueLabel('2026-03-09', '2026-03-08').text).toBe('Tomorrow');
    expect(dueLabel('2026-11-02', '2026-11-01').text).toBe('Tomorrow');
  });

  it('formatDay renders a timestamp on the install’s calendar day, falling back safely', () => {
    const iso = '2026-08-02T04:00:00.123456+00:00'; // 23:00 Aug 1 in Chicago, Aug 2 in Tokyo
    const at = new Date('2026-08-02T04:00:00Z');
    expect(formatDay(iso, 'Asia/Tokyo')).toBe(at.toLocaleDateString(undefined, { timeZone: 'Asia/Tokyo' }));
    expect(formatDay(iso, 'Asia/Tokyo')).not.toBe(formatDay(iso, 'America/Chicago'));
    expect(formatDay(iso, 'Not/AZone')).toBe(at.toLocaleDateString());
    expect(formatDay(iso)).toBe(at.toLocaleDateString());
  });
});

describe('bringBackState (#261)', () => {
  it('is waiting before the day, back on and after it, and null with no date', () => {
    expect(bringBackState('2026-10-10', '2026-10-09')).toBe('waiting');
    expect(bringBackState('2026-10-10', '2026-10-10')).toBe('back');
    expect(bringBackState('2026-10-10', '2026-12-01')).toBe('back');
    expect(bringBackState(null, '2026-10-10')).toBeNull();
    expect(bringBackState(undefined, '2026-10-10')).toBeNull();
    expect(bringBackState('', '2026-10-10')).toBeNull();
  });
});
