// The warm cache's rules (#281). These are the whole safety case for keeping CRM rows in a
// browser — whose rows may be read, for how long, and under which schema — so each rule is
// pinned on its own. `warmStore` (the IndexedDB transport) is replaced by a Map here; its own
// file tests the transport, including the sign-out race.
import { beforeEach, describe, expect, it, vi } from 'vitest';

const store = vi.hoisted(() => new Map<string, unknown>());

vi.mock('./warmStore', () => ({
  warmEpoch: () => 0,
  idbGet: async (k: string) => store.get(k),
  idbPut: async (k: string, v: unknown) => void store.set(k, v),
  idbDelete: async (k: string) => void store.delete(k),
  idbKeys: async () => [...store.keys()],
}));

const { readWarm, writeWarm, purgeForeignWarm, formatSavedAt, WARM_MAX_AGE_MS, WARM_SCHEMA_VERSION } = await import('./warmCache');

const ANA = 'ana@example.com';
const BEN = 'ben@example.com';
const T0 = 1_800_000_000_000;

describe('CRM warm cache rules (#281)', () => {
  beforeEach(() => store.clear());

  it('round-trips a list for the user who saved it, with its save time', async () => {
    await writeWarm(ANA, 'contacts', [{ id: 1 }], T0);
    expect(await readWarm(ANA, 'contacts', T0 + 1000)).toEqual({ data: [{ id: 1 }], savedAt: T0 });
  });

  it('keeps one entry per list and per user', async () => {
    await writeWarm(ANA, 'contacts', ['ana-contacts'], T0);
    await writeWarm(ANA, 'companies', ['ana-companies'], T0);
    await writeWarm(BEN, 'contacts', ['ben-contacts'], T0);
    expect(store.size).toBe(3);
    expect((await readWarm(ANA, 'companies', T0))?.data).toEqual(['ana-companies']);
    expect((await readWarm(BEN, 'contacts', T0))?.data).toEqual(['ben-contacts']);
  });

  it("never returns another user's rows, even one filed under the wrong key", async () => {
    await writeWarm(ANA, 'pipeline', ['ana-deals'], T0);
    expect(await readWarm(BEN, 'pipeline', T0)).toBeNull();
    store.set(`${BEN}|pipeline`, { v: WARM_SCHEMA_VERSION, email: ANA, tab: 'pipeline', savedAt: T0, data: ['ana-deals'] });
    expect(await readWarm(BEN, 'pipeline', T0)).toBeNull();
    expect(store.has(`${BEN}|pipeline`)).toBe(false);
  });

  it('persists and reads nothing without an email', async () => {
    await writeWarm(null, 'contacts', ['rows'], T0);
    expect(store.size).toBe(0);
    await writeWarm(ANA, 'contacts', ['rows'], T0);
    expect(await readWarm(null, 'contacts', T0)).toBeNull();
  });

  it('drops an entry saved under another schema version without reading it', async () => {
    store.set(`${ANA}|contacts`, { v: WARM_SCHEMA_VERSION - 1, email: ANA, tab: 'contacts', savedAt: T0, data: ['old-shape'] });
    expect(await readWarm(ANA, 'contacts', T0)).toBeNull();
    expect(store.size).toBe(0);
  });

  it('serves a cache up to seven days old and discards one older than that', async () => {
    await writeWarm(ANA, 'companies', ['rows'], T0);
    expect(await readWarm(ANA, 'companies', T0 + WARM_MAX_AGE_MS)).not.toBeNull();
    expect(await readWarm(ANA, 'companies', T0 + WARM_MAX_AGE_MS + 1)).toBeNull();
    expect(store.size).toBe(0);
  });

  it('discards an entry whose save time is in the future or not a time at all', async () => {
    await writeWarm(ANA, 'companies', ['rows'], T0 + 60_000);
    expect(await readWarm(ANA, 'companies', T0)).toBeNull();
    store.set(`${ANA}|companies`, { v: WARM_SCHEMA_VERSION, email: ANA, tab: 'companies', data: ['rows'] });
    expect(await readWarm(ANA, 'companies', T0)).toBeNull();
  });

  it("purges every other user's entries and keeps the viewer's own", async () => {
    await writeWarm(ANA, 'contacts', ['ana'], T0);
    await writeWarm(ANA, 'pipeline', ['ana'], T0);
    await writeWarm(BEN, 'contacts', ['ben'], T0);
    await purgeForeignWarm(BEN);
    expect([...store.keys()]).toEqual([`${BEN}|contacts`]);
    await purgeForeignWarm(null);
    expect(store.size).toBe(0);
  });

  it('labels a save time with its date unless it is from today', () => {
    const today = new Date(2026, 9, 10, 15, 42).getTime();
    expect(formatSavedAt(today, today + 60_000)).not.toMatch(/Oct/);
    expect(formatSavedAt(today - 2 * 86_400_000, today)).toMatch(/Oct 8/);
  });
});
