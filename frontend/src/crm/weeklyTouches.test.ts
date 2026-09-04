// Pure URL helpers for the Weekly Touches drill-down (issue #146) — no DOM, so this runs
// in the default `node` environment.
import { describe, expect, it } from 'vitest';

import {
  UNASSIGNED_OWNER_PARAM,
  ownerParamOf,
  parseOwnerParam,
  touchDetailApiPath,
  touchDetailPath,
} from './weeklyTouches';

const window_ = {
  start: '2026-08-15T14:03:11+00:00',
  end: '2026-08-22T14:03:11+00:00',
  label: 'Last 7 days',
  custom: false,
};

describe('ownerParamOf / parseOwnerParam', () => {
  it('spells the null bucket as a literal, not an absent param', () => {
    // `deals.owner_id` is nullable forever (#60), and /dashboard/today's
    // absent-means-everyone idiom cannot address "nobody".
    expect(ownerParamOf(null)).toBe(UNASSIGNED_OWNER_PARAM);
    expect(ownerParamOf(7)).toBe('7');
  });

  it('round-trips both bucket kinds', () => {
    expect(parseOwnerParam(ownerParamOf(null))).toEqual({ ok: true, owner: 'unassigned' });
    expect(parseOwnerParam(ownerParamOf(7))).toEqual({ ok: true, owner: '7' });
  });

  it('keeps the id as a STRING so a huge one cannot lose precision', () => {
    // The page only forwards this value; the server range-checks it against a 32-bit
    // column. `Number()` here would silently round 9007199254740993 and ask for the
    // wrong rep.
    const huge = '9007199254740993';
    expect(parseOwnerParam(huge)).toEqual({ ok: true, owner: huge });
  });

  it.each([undefined, '', '7a', '-1', '7.0', 'Unassigned', '٣'])(
    'rejects %p rather than building a URL the server will 400',
    raw => {
      expect(parseOwnerParam(raw)).toEqual({ ok: false });
    },
  );
});

describe('touchDetailPath', () => {
  it('carries the exact window bounds and encodes the offset\'s plus sign', () => {
    // Sent raw, the `+` in `+00:00` arrives at the server as a space and fails to parse.
    const path = touchDetailPath(7, window_);
    expect(path).toContain('/crm/touches/7?');
    expect(path).toContain('ws=2026-08-15T14%3A03%3A11%2B00%3A00');
    expect(path).toContain('we=2026-08-22T14%3A03%3A11%2B00%3A00');
  });

  it('uses the literal for the unassigned bucket', () => {
    expect(touchDetailPath(null, window_)).toContain('/crm/touches/unassigned?');
  });
});

describe('touchDetailApiPath', () => {
  it('forwards only the window params actually present, and invents none', () => {
    const search = new URLSearchParams({ ws: 'A', we: 'B', irrelevant: 'x' });
    const path = touchDetailApiPath('7', search);

    expect(path).toBe('/api/crm/dashboard/weekly-touches/detail?owner=7&ws=A&we=B');
    expect(path).not.toContain('irrelevant');
  });

  it('passes calendar days through for direct navigation', () => {
    const path = touchDetailApiPath('unassigned', new URLSearchParams({ start: 'S', end: 'E' }));
    expect(path).toBe(
      '/api/crm/dashboard/weekly-touches/detail?owner=unassigned&start=S&end=E',
    );
  });

  it('asks for the default window when the page URL carries none', () => {
    expect(touchDetailApiPath('7', new URLSearchParams())).toBe(
      '/api/crm/dashboard/weekly-touches/detail?owner=7',
    );
  });
});
