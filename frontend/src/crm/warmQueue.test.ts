// The background warm-up queue (#281): Pipeline → Contacts → Companies, strictly one at a time,
// each written to the warm cache only from a complete sweep, moving on when a sweep completes or
// fails, once per signed-in session, and yielding a list to a page that sweeps it itself.
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { WarmSweepers } from './warmQueue';

const store = vi.hoisted(() => new Map<string, unknown>());
const epoch = vi.hoisted(() => ({ value: 0 }));

vi.mock('./warmStore', () => ({
  warmEpoch: () => epoch.value,
  idbGet: async (k: string) => store.get(k),
  idbPut: async (k: string, v: unknown, since?: number) => {
    if (since === undefined || since === epoch.value) store.set(k, v);
  },
  idbDelete: async (k: string) => void store.delete(k),
  idbKeys: async () => [...store.keys()],
}));

const ANA = 'ana@example.com';

/** Sweepers that each wait for the test to release them, recording order and overlap. */
function controlled() {
  const log: string[] = [];
  let running = 0;
  let maxRunning = 0;
  const gates = new Map<string, { resolve: (v: unknown) => void; reject: (e: Error) => void }>();
  const make = (tab: string) => (signal: AbortSignal) => new Promise((resolve, reject) => {
    log.push(`start:${tab}`);
    running += 1;
    maxRunning = Math.max(maxRunning, running);
    const done = () => { running -= 1; };
    signal.addEventListener('abort', () => { done(); reject(new Error('aborted')); });
    gates.set(tab, { resolve: v => { done(); resolve(v); }, reject: e => { done(); reject(e); } });
  });
  const sweepers: WarmSweepers = { pipeline: make('pipeline'), contacts: make('contacts'), companies: make('companies') };
  const flush = () => new Promise(r => setTimeout(r, 0));
  return { log, sweepers, gates, flush, maxRunning: () => maxRunning };
}

let q: typeof import('./warmQueue');
beforeEach(async () => {
  store.clear();
  epoch.value = 0;
  vi.resetModules();
  q = await import('./warmQueue');
});

describe('warm-up queue (#281)', () => {
  it('sweeps Pipeline, Contacts, Companies in order, one at a time, caching each', async () => {
    const c = controlled();
    const done = q.startWarmUp(ANA, c.sweepers);
    await c.flush();
    expect(c.log).toEqual(['start:pipeline']);
    c.gates.get('pipeline')!.resolve(['d']);
    await c.flush();
    expect(c.log).toEqual(['start:pipeline', 'start:contacts']);
    c.gates.get('contacts')!.resolve(['c']);
    await c.flush();
    c.gates.get('companies')!.resolve(['co']);
    await done;
    expect(c.log).toEqual(['start:pipeline', 'start:contacts', 'start:companies']);
    expect(c.maxRunning()).toBe(1);
    expect([...store.keys()].sort()).toEqual([`${ANA}|companies`, `${ANA}|contacts`, `${ANA}|pipeline`]);
  });

  it('moves on when a sweep fails, and caches nothing for it', async () => {
    const c = controlled();
    const done = q.startWarmUp(ANA, c.sweepers);
    await c.flush();
    c.gates.get('pipeline')!.reject(new Error('500'));
    await c.flush();
    expect(c.log).toEqual(['start:pipeline', 'start:contacts']);
    c.gates.get('contacts')!.resolve(['c']);
    await c.flush();
    c.gates.get('companies')!.resolve(['co']);
    await done;
    expect(store.has(`${ANA}|pipeline`)).toBe(false);
    expect(store.has(`${ANA}|contacts`)).toBe(true);
  });

  it('runs once per signed-in session, and again after a sign-out', async () => {
    const c = controlled();
    const first = q.startWarmUp(ANA, c.sweepers);
    void q.startWarmUp(ANA, c.sweepers);          // a second Dashboard visit mid-run: no-op
    await c.flush();
    for (const tab of ['pipeline', 'contacts', 'companies']) {
      c.gates.get(tab)!.resolve([tab]);
      await c.flush();
    }
    await first;
    await q.startWarmUp(ANA, c.sweepers);          // finished: still a no-op this session
    expect(c.log.length).toBe(3);
    epoch.value += 1;                              // sign-out and back in
    void q.startWarmUp(ANA, c.sweepers);
    await c.flush();
    expect(c.log.length).toBe(4);
  });

  it('does nothing without an email', async () => {
    const c = controlled();
    await q.startWarmUp(null, c.sweepers);
    expect(c.log).toEqual([]);
  });

  it('skips a list a page has claimed, and aborts one claimed mid-sweep without caching it', async () => {
    const c = controlled();
    const done = q.startWarmUp(ANA, c.sweepers);
    await c.flush();
    q.claimWarmTab('contacts');                    // queued: dropped
    q.claimWarmTab('pipeline');                    // in flight: aborted
    await c.flush();
    expect(c.log).toEqual(['start:pipeline', 'start:companies']);
    c.gates.get('companies')!.resolve(['co']);
    await done;
    expect([...store.keys()]).toEqual([`${ANA}|companies`]);
  });

  it('stops and writes nothing once the user signs out mid-sweep', async () => {
    const c = controlled();
    const done = q.startWarmUp(ANA, c.sweepers);
    await c.flush();
    epoch.value += 1;
    c.gates.get('pipeline')!.resolve(['d']);
    await done;
    expect(c.log).toEqual(['start:pipeline']);
    expect(store.size).toBe(0);
  });
});
