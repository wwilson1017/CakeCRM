// The warm cache's IndexedDB transport (#281), run against a minimal in-memory IndexedDB — the
// vitest environment has none, and the repo carries no fake. Pinned here: sign-out wipes the
// store, and a sweep that STARTED before a sign-out can never write its rows back after it.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/** Just enough IndexedDB for warmStore: one database, one object store, async completion. */
function installFakeIndexedDb() {
  const data = new Map<string, unknown>();
  let created = false;
  const later = (fn: () => void) => setTimeout(fn, 0);
  const objectStore = {
    get: (k: string) => ({ result: data.get(k) }),
    put: (v: unknown, k: string) => { data.set(k, v); return { result: k }; },
    delete: (k: string) => { data.delete(k); return { result: undefined }; },
    clear: () => { data.clear(); return { result: undefined }; },
    getAllKeys: () => ({ result: [...data.keys()] }),
  };
  const db = {
    objectStoreNames: { contains: () => created },
    createObjectStore: () => { created = true; },
    close: () => {},
    transaction: () => {
      const t: { objectStore: () => typeof objectStore; oncomplete?: () => void } = { objectStore: () => objectStore };
      later(() => t.oncomplete?.());
      return t;
    },
  };
  vi.stubGlobal('indexedDB', {
    open: () => {
      const req: { result: typeof db; onupgradeneeded?: () => void; onsuccess?: () => void } = { result: db };
      later(() => { if (!created) req.onupgradeneeded?.(); req.onsuccess?.(); });
      return req;
    },
  });
  return data;
}

describe('warm cache transport (#281)', () => {
  let data: Map<string, unknown>;
  beforeEach(() => { vi.resetModules(); data = installFakeIndexedDb(); });
  afterEach(() => vi.unstubAllGlobals());

  it('round-trips, lists keys, deletes', async () => {
    const s = await import('./warmStore');
    await s.idbPut('a|contacts', { rows: 1 });
    expect(await s.idbGet('a|contacts')).toEqual({ rows: 1 });
    expect(await s.idbKeys()).toEqual(['a|contacts']);
    await s.idbDelete('a|contacts');
    expect(await s.idbKeys()).toEqual([]);
  });

  it('sign-out wipes every entry, whoever it belonged to', async () => {
    const s = await import('./warmStore');
    await s.idbPut('a|contacts', 1);
    await s.idbPut('b|pipeline', 2);
    await s.wipeWarm();
    expect(data.size).toBe(0);
  });

  it('drops a write from a sweep that started before the sign-out', async () => {
    const s = await import('./warmStore');
    const startedAt = s.warmEpoch();
    await s.wipeWarm();
    await s.idbPut('a|contacts', ['rows'], startedAt);
    expect(data.size).toBe(0);
    // A sweep started after the wipe writes normally.
    await s.idbPut('a|contacts', ['rows'], s.warmEpoch());
    expect(data.size).toBe(1);
  });

  it('drops a write already opening the database when the wipe lands', async () => {
    const s = await import('./warmStore');
    const pending = s.idbPut('a|contacts', ['rows']);
    void s.wipeWarm();
    await pending;
    expect(data.has('a|contacts')).toBe(false);
  });

  it('resolves as "no cache" when IndexedDB is missing', async () => {
    vi.stubGlobal('indexedDB', undefined);
    const s = await import('./warmStore');
    await s.idbPut('a|contacts', 1);
    expect(await s.idbGet('a|contacts')).toBeUndefined();
    expect(await s.idbKeys()).toEqual([]);
  });
});
