import { describe, expect, it } from 'vitest';

import { bannerCopy, coverageNote, stateLabel, summaryLine } from './touchEvidence';

describe('stateLabel', () => {
  it('names each verdict state', () => {
    expect(stateLabel('touch', true)).toBe('Touch');
    expect(stateLabel('not_touch', true)).toBe('Not a touch');
    expect(stateLabel('excluded_empty', true)).toBe('Not sent to AI — empty note');
    expect(stateLabel('edited_since', true)).toBe('Edited since it was judged');
    expect(stateLabel('stage_move', true)).toBe('Not counted');
  });

  it('never promises a closed deal an AI pass that will not come', () => {
    expect(stateLabel('not_evaluated', true)).toBe('Awaiting next AI pass');
    expect(stateLabel('not_evaluated', false)).toBe('Never evaluated');
  });
});

describe('bannerCopy', () => {
  it('stays silent when the explanation is current on a live deal', () => {
    expect(bannerCopy('current', true)).toBeNull();
  });

  it('explains that a closed deal has stopped recalculating', () => {
    expect(bannerCopy('current', false)).toContain('no longer recalculates');
  });

  it('states the missing-explanation fact without inventing a cause', () => {
    // Reached by pre-#56 counts AND by the count-only fallback — we cannot tell which,
    // so the copy must not claim one, and must not promise they will appear.
    const open = bannerCopy('none', true) as string;
    expect(open).toBe('No per-event explanations are stored for this count.');
    expect(open).not.toMatch(/next|will appear|soon/i);
    expect(bannerCopy('none', false)).toContain('none will be added');
  });

  it('names the observable mismatch when superseded, for either deal state', () => {
    for (const open of [true, false]) {
      expect(bannerCopy('superseded', open)).toContain("don't fully explain it");
    }
  });

  it('offers a refresh path only when one actually exists', () => {
    expect(bannerCopy('stale', true)).toContain('next note or logged activity');
    const closed = bannerCopy('stale', false) as string;
    expect(closed).toContain('no longer updates');
    expect(closed).not.toMatch(/will be re-evaluated/);
  });
});

describe('coverageNote', () => {
  it('admits when the list is only a recent window', () => {
    expect(coverageNote(true)).toContain("older history isn't part of this count");
    expect(coverageNote(false)).toBeNull();
  });
});

describe('summaryLine', () => {
  it('reports counted-of-evaluated with the evaluation date', () => {
    expect(summaryLine(3, 12, '2026-08-19T10:00:00+00:00'))
      .toBe('3 of 12 events counted as touches · evaluated Aug 19');
  });

  it('drops the date rather than rendering an invalid one', () => {
    expect(summaryLine(1, 2, 'not-a-date')).toBe('1 of 2 events counted as touches');
    expect(summaryLine(1, 2, null)).toBe('1 of 2 events counted as touches');
  });

  it('falls back when nothing has been evaluated', () => {
    expect(summaryLine(null, 5, null)).toBe('No per-event explanations stored yet.');
    expect(summaryLine(0, 0, null)).toBe('No per-event explanations stored yet.');
  });
});
