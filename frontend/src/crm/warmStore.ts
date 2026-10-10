/**
 * The CRM warm cache's IndexedDB adapter (#281) — transport only, no policy (`warmCache.ts`).
 *
 * A LEAF on purpose: it imports nothing, so `core/auth/AuthContext` can call `wipeWarm()` on
 * sign-out without pulling a CRM page graph into the shell chunk, and the wipe works in a page
 * session that never opened a list page at all.
 *
 * Every call opens the database, runs ONE transaction and closes the connection. Nothing holds a
 * connection open, so a wipe from another tab is never blocked behind this one.
 *
 * Nothing here rejects. Private mode, a missing `indexedDB` (the vitest `node` environment), a
 * quota error, an aborted transaction — each resolves as "no value" / "not written". A cache that
 * cannot be used is simply no cache; it must never break the page it was meant to speed up.
 */
const DB_NAME = 'cakecrm-warm';
const STORE = 'lists';

/**
 * Bumped synchronously by `wipeWarm()`. A writer captures it when its sweep STARTS (`warmEpoch()`)
 * and the put re-checks it once the database is open, so a sweep that was already running when
 * the user signed out can never put its rows back after the wipe — whether it completes a moment
 * before the wipe or a minute after it.
 */
let epoch = 0;

export function warmEpoch(): number {
  return epoch;
}

function openDb(): Promise<IDBDatabase | null> {
  return new Promise(resolve => {
    try {
      const req = indexedDB.open(DB_NAME, 1);
      req.onupgradeneeded = () => {
        if (!req.result.objectStoreNames.contains(STORE)) req.result.createObjectStore(STORE);
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => resolve(null);
      req.onblocked = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
}

/** One transaction, resolved only when it has COMMITTED — a request's own `success` fires before
 *  the commit, and a write that later aborts must not be reported as written. */
async function tx<R>(
  mode: IDBTransactionMode,
  run: (store: IDBObjectStore) => IDBRequest<R>,
  stillWanted: () => boolean = () => true,
): Promise<R | undefined> {
  const db = await openDb();
  if (!db) return undefined;
  try {
    if (!stillWanted()) return undefined;
    return await new Promise<R | undefined>(resolve => {
      const t = db.transaction(STORE, mode);
      const req = run(t.objectStore(STORE));
      t.oncomplete = () => resolve(req.result);
      t.onerror = () => resolve(undefined);
      t.onabort = () => resolve(undefined);
    });
  } catch {
    return undefined;
  } finally {
    db.close();
  }
}

export function idbGet(key: string): Promise<unknown> {
  return tx('readonly', s => s.get(key));
}

/** `since` is the epoch the writer's sweep started under; the put is dropped if a wipe came after. */
export async function idbPut(key: string, value: unknown, since: number = epoch): Promise<void> {
  await tx('readwrite', s => s.put(value, key), () => since === epoch);
}

export async function idbDelete(key: string): Promise<void> {
  await tx('readwrite', s => s.delete(key));
}

export async function idbKeys(): Promise<string[]> {
  return ((await tx('readonly', s => s.getAllKeys())) ?? []).map(String);
}

/** Sign-out: drop EVERY CRM warm entry in this browser, whoever it belonged to. */
export async function wipeWarm(): Promise<void> {
  epoch += 1;
  await tx('readwrite', s => s.clear());
}
