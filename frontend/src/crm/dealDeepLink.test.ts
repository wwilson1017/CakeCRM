// The deal deep link's two halves must agree, and the parser must be strict.
//
// The round-trip case is the real contract: `DealDetailBody` writes the link and `PipelinePage`
// reads it, and because the parameter is stripped the instant it is read, nothing else ever
// reconstructs the shape. If the two drifted apart the failure would be a shared link that
// silently opens nothing — no error, no console, just a board.
import { describe, expect, it } from 'vitest';
import { DEAL_PARAM, dealDeepLink, parseDealParam } from './dealDeepLink';

describe('dealDeepLink', () => {
  it('round-trips an id through a real URL', () => {
    const url = new URL(dealDeepLink(42), 'https://crm.example');
    expect(url.pathname).toBe('/crm/pipeline');
    expect(parseDealParam(url.searchParams.get(DEAL_PARAM))).toBe(42);
  });

  it('survives being appended to an origin, which is how the button builds it', () => {
    expect(`https://crm.example${dealDeepLink(7)}`).toBe('https://crm.example/crm/pipeline?deal=7');
  });
});

describe('parseDealParam', () => {
  it('accepts a plain positive integer', () => {
    expect(parseDealParam('1')).toBe(1);
    expect(parseDealParam('2147483647')).toBe(2147483647);
  });

  it.each([
    ['a missing parameter', null],
    ['an empty string', ''],
    ['a word', 'abc'],
    ['zero', '0'],
    ['a negative id', '-3'],
    ['a decimal', '1.5'],
    ['padded whitespace', ' 3 '],
    ['a hex-ish value', '0x10'],
    ['past the int4 ceiling', '2147483648'],
    ['an id with a trailing comma', '3,4'],
  ])('rejects %s', (_label, raw) => {
    // Deliberately NOT `Number()`: it coerces '' to 0, ' 3 ' to 3 and '0x10' to 16, and a URL a
    // stranger can hand you should not get to choose which record opens.
    expect(parseDealParam(raw)).toBeNull();
  });
});
