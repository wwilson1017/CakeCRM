// @vitest-environment jsdom
import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { loadPersistedState, savePersistedState, toggleValue } from './persist';

interface State { q: string }
const EMPTY: State = { q: '' };

/** The shape every consumer's coercion has: total, never throws, accepts junk. */
const coerce = (raw: unknown): State => {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return { ...EMPTY };
  const o = raw as Record<string, unknown>;
  return { q: typeof o.q === 'string' ? o.q : '' };
};

describe('loadPersistedState', () => {
  beforeEach(() => sessionStorage.clear());
  afterEach(() => vi.restoreAllMocks());

  it('round-trips a saved value', () => {
    savePersistedState('k', { q: 'hello' });
    expect(loadPersistedState('k', coerce)).toEqual({ q: 'hello' });
  });

  it('reaches coerce with null for an absent key', () => {
    const seen: unknown[] = [];
    loadPersistedState('missing', raw => { seen.push(raw); return coerce(raw); });
    expect(seen).toEqual([null]);
  });

  it('funnels every junk payload through coerce instead of throwing', () => {
    for (const stored of ['null', '"x"', '[]', '{not json', '42']) {
      sessionStorage.setItem('k', stored);
      expect(loadPersistedState('k', coerce), `stored ${stored}`).toEqual({ q: '' });
    }
  });

  it('degrades to coerce(null) when sessionStorage itself throws', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked'); });
    expect(loadPersistedState('k', coerce)).toEqual({ q: '' });
  });
});

describe('savePersistedState', () => {
  beforeEach(() => sessionStorage.clear());
  afterEach(() => vi.restoreAllMocks());

  it('swallows a throwing store rather than breaking the surface', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota'); });
    expect(() => savePersistedState('k', { q: 'x' })).not.toThrow();
  });
});

describe('toggleValue', () => {
  it('adds a missing value and removes a present one, returning a new array', () => {
    const list = ['a', 'b'];
    expect(toggleValue(list, 'c')).toEqual(['a', 'b', 'c']);
    expect(toggleValue(list, 'a')).toEqual(['b']);
    expect(toggleValue(list, 'c')).not.toBe(list);
  });

  it('works for numeric facet values', () => {
    expect(toggleValue([1, 2], 3)).toEqual([1, 2, 3]);
    expect(toggleValue([1, 2], 2)).toEqual([1]);
  });
});
