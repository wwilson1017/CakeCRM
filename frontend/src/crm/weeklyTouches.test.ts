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

  it('carries the id as a STRING, never a re-serialized Number', () => {
    // The page only forwards this value onward, so there is nothing to gain by converting
    // it — and the bound check above is what keeps `Number()` exact where it IS used.
    expect(parseOwnerParam('2147483647').ok && parseOwnerParam('2147483647')).toEqual({
      ok: true, owner: '2147483647',
    });
  });

  it.each([undefined, '', '7a', '-1', '7.0', 'Unassigned', '٣'])(
    'rejects %p rather than building a URL the server will 400',
    raw => {
      expect(parseOwnerParam(raw)).toEqual({ ok: false });
    },
  );

  it.each(['0', '2147483648', '99999999999', '9'.repeat(5000)])(
    'rejects %p, mirroring the server\'s positive 32-bit bound',
    raw => {
      // Not a security check — the server validates regardless. It is a MESSAGE check:
      // an id the server 400s reaches the page as a generic 4xx and would otherwise be
      // reported as a bad date range, sending the user to the wrong half of the URL.
      expect(parseOwnerParam(raw)).toEqual({ ok: false });
    },
  );

  it('accepts the largest id the schema can hold', () => {
    expect(parseOwnerParam('2147483647')).toEqual({ ok: true, owner: '2147483647' });
  });
});

describe('touchDetailPath', () => {
  it('carries no window at all on the rolling default', () => {
    // The page re-resolves "last 7 days" at open time. Forwarding the card's exact
    // instants instead would freeze the upper bound, and membership is "this deal's
    // CURRENT most recent touch falls in the window" — so a deal touched since the card
    // rendered would fall past that bound and vanish from the page it was clicked from.
    expect(touchDetailPath(7, null)).toBe('/crm/touches/7');
  });

  it('carries the card\'s custom range as the same calendar days the card sends', () => {
    const path = touchDetailPath(7, { start: '2026-06-16', end: '2026-06-20' });
    expect(path).toBe('/crm/touches/7?start=2026-06-16&end=2026-06-20');
  });

  it('uses the literal for the unassigned bucket', () => {
    expect(touchDetailPath(null, null)).toBe('/crm/touches/unassigned');
  });
});

describe('touchDetailApiPath', () => {
  it('forwards only the window params actually present, and invents none', () => {
    const search = new URLSearchParams({ start: 'S', end: 'E', irrelevant: 'x' });
    const path = touchDetailApiPath('7', search);

    expect(path).toBe('/api/crm/dashboard/weekly-touches/detail?owner=7&start=S&end=E');
    expect(path).not.toContain('irrelevant');
  });

  it('ignores a stale instant-pair URL rather than passing it to the server', () => {
    // Links minted before the window was re-resolved rather than frozen carry ws/we.
    // They are not a supported param any more, and the server would 422 on nothing —
    // dropping them degrades such a link to the rolling default, which is what it meant.
    const path = touchDetailApiPath('7', new URLSearchParams({ ws: 'A', we: 'B' }));
    expect(path).toBe('/api/crm/dashboard/weekly-touches/detail?owner=7');
  });

  it('asks for the default window when the page URL carries none', () => {
    expect(touchDetailApiPath('unassigned', new URLSearchParams())).toBe(
      '/api/crm/dashboard/weekly-touches/detail?owner=unassigned',
    );
  });
});
