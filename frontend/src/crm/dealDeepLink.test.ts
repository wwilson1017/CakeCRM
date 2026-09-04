import { describe, expect, it } from 'vitest';

import {
  DEAL_DEEP_LINK_PARAM, dealDeepLink, deepLinkVerdict, parseDealDeepLinkId,
  type DeepLinkVerdictInput,
} from './dealDeepLink';

describe('dealDeepLink', () => {
  it('emits the shape PipelinePage parses', () => {
    expect(dealDeepLink(42)).toBe('/crm/pipeline?deal=42');
  });

  it('names the parameter PipelinePage reads', () => {
    // The producer and the reader must agree on the key, and only one of them spells it
    // out — a rename here that misses the constant emits links nothing opens.
    expect(new URL(dealDeepLink(42), 'http://x').searchParams.get(DEAL_DEEP_LINK_PARAM))
      .toBe('42');
  });
});

describe('parseDealDeepLinkId', () => {
  it('reads a plain positive integer', () => {
    expect(parseDealDeepLinkId('42')).toBe(42);
  });

  it.each([
    ['a missing param', null],
    ['an undefined param', undefined],
    ['an empty string', ''],
    // `Number('')` is 0 and `Number(' 42 ')` is 42 — both would sail through a bare
    // `Number()` parse, and 0 is not a deal id.
    ['whitespace padding', ' 42 '],
    ['a zero id', '0'],
    ['a negative id', '-7'],
    ['a decimal', '4.5'],
    ['exponent notation', '1e3'],
    ['hex notation', '0x2a'],
    ['a word', 'abc'],
    // Beyond Number.MAX_SAFE_INTEGER two distinct ids compare equal, so a lookup could
    // match the wrong deal — refuse rather than guess.
    ['an unsafe integer', '9007199254740993'],
  ])('rejects %s', (_label, raw) => {
    expect(parseDealDeepLinkId(raw as string | null | undefined)).toBeNull();
  });
});

describe('deepLinkVerdict', () => {
  const base: DeepLinkVerdictInput = {
    dealId: 42,
    boardLoaded: true,
    dealOnBoard: true,
    boardRefreshedSinceLink: true,
    refreshRequested: false,
  };

  it('does nothing when the URL names no deal', () => {
    expect(deepLinkVerdict({ ...base, dealId: null })).toBe('idle');
  });

  it('does nothing before a board payload has been applied', () => {
    // The gate is "a board is on screen", never `!loading` or `!error`. Deciding a deal
    // is gone on the strength of a failed or in-flight load is an accusation about the
    // network, not about the deal.
    expect(deepLinkVerdict({ ...base, boardLoaded: false, dealOnBoard: false })).toBe('idle');
  });

  it('opens the deal when the loaded board has it', () => {
    expect(deepLinkVerdict(base)).toBe('open');
  });

  it('declares a miss dead once the board was fetched after the link arrived', () => {
    expect(deepLinkVerdict({ ...base, dealOnBoard: false })).toBe('dead');
  });

  it('refreshes rather than accusing when the board predates the link', () => {
    // The assistant hands out links to deals it just created, and the pipeline stays
    // mounted while its drawer is open. A board older than the link is silent about the
    // deal, not evidence against it.
    expect(deepLinkVerdict({ ...base, dealOnBoard: false, boardRefreshedSinceLink: false }))
      .toBe('refresh');
  });

  it('stays silent instead of accusing when the one refresh never landed', () => {
    // A failed refresh leaves the board un-advanced forever. Saying nothing loses a
    // correct notice about a genuinely deleted deal; saying "dead" here would tell a rep
    // their live deal was deleted because the network blipped.
    expect(deepLinkVerdict({
      ...base, dealOnBoard: false, boardRefreshedSinceLink: false, refreshRequested: true,
    })).toBe('idle');
  });

  it('asks for at most one refresh per link', () => {
    const missOnStaleBoard = { ...base, dealOnBoard: false, boardRefreshedSinceLink: false };
    expect(deepLinkVerdict({ ...missOnStaleBoard, refreshRequested: false })).toBe('refresh');
    expect(deepLinkVerdict({ ...missOnStaleBoard, refreshRequested: true })).not.toBe('refresh');
  });

  it('opens a deal the board holds even on a stale board', () => {
    // Presence is proof; only ABSENCE is ambiguous on a board older than the link.
    expect(deepLinkVerdict({ ...base, boardRefreshedSinceLink: false })).toBe('open');
  });
});
