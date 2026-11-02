// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { __resetReadAloudPrefsForTests, READ_ALOUD_KEY, readAloudEnabled, setReadAloudEnabled, subscribeReadAloud } from './readAloudPrefs';
beforeEach(__resetReadAloudPrefsForTests);
afterEach(() => vi.restoreAllMocks());
it('defaults off and persists an enabled preference', () => {
  expect(readAloudEnabled()).toBe(false);
  setReadAloudEnabled(true);
  expect(localStorage.getItem(READ_ALOUD_KEY)).toBe('1');
  expect(readAloudEnabled()).toBe(true);
});
it('survives blocked storage with an in-memory fallback', () => {
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw Error(); });
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw Error(); });
  expect(readAloudEnabled()).toBe(false);
  setReadAloudEnabled(true); expect(readAloudEnabled()).toBe(true);
});
it('notifies same-tab writes and cross-tab storage changes', () => {
  const fn = vi.fn(); const unsubscribe = subscribeReadAloud(fn);
  setReadAloudEnabled(true); expect(fn).toHaveBeenCalledTimes(1);
  window.dispatchEvent(new StorageEvent('storage', { key: READ_ALOUD_KEY }));
  expect(fn).toHaveBeenCalledTimes(2); unsubscribe();
});

it('keeps the preference in memory if only writes are blocked', () => {
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw Error(); });
  setReadAloudEnabled(true);
  expect(readAloudEnabled()).toBe(true);
});
