/**
 * The CRM warm cache's rules (#281, port of the blueprint's #3637): what may be persisted, for
 * whom, and for how long.
 *
 * Pipeline, Contacts and Companies each sweep their whole corpus before anything is shown
 * (`assemblyPage.ts`), and those pages are ROUTES, so every visit re-sweeps from zero. The last
 * COMPLETE set per list is kept in IndexedDB so a page shows rows the instant it opens while the
 * fresh sweep runs behind it. This is CRM data sitting in a browser, so the rules are the feature:
 *
 *  • keyed by the signed-in user's email, and an entry is never read under a different one;
 *  • no email (signed out, or the account not yet known) ⇒ nothing is read and nothing written;
 *  • an entry from another schema version, or older than seven days, is deleted, never read;
 *  • sign-out wipes the store (`warmStore.wipeWarm`, called from `AuthContext`, both for this
 *    tab's logout and for the cross-tab one);
 *  • opening the CRM purges every entry that is not the current user's — the backstop for a user
 *    switch that never passed through sign-out (an expired session, then a different login).
 *
 * Callers write only from a COMPLETE sweep, replaced wholesale; a partial one, or the local write
 * overlay on top of a sweep, must never reach the cache.
 */
import { createContext } from 'react';
import { idbDelete, idbGet, idbKeys, idbPut, warmEpoch } from './warmStore';

export type WarmTab = 'pipeline' | 'contacts' | 'companies';

/**
 * Bump this in the same PR as any change to the shape of a list `CrmContact`, `CrmCompany` or the
 * board's `CrmDeal` — an entry saved under another version is dropped unread, or yesterday's
 * cached rows would render in today's UI. Deliberately NOT the build id: a per-build version would
 * empty every cache on each deploy. The rule is recorded in `docs/agents/list-pages.md`.
 */
export const WARM_SCHEMA_VERSION = 1;
export const WARM_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;

/**
 * The signed-in email, provided by `CrmLayout` around the routed pages. Its default is `null`, so
 * a page rendered outside the layout (every page test) touches no cache at all.
 */
export const WarmViewerContext = createContext<string | null>(null);

interface WarmEntry {
  v: number;
  email: string;
  tab: WarmTab;
  savedAt: number;
  data: unknown;
}

export interface WarmHit<T> {
  data: T;
  savedAt: number;
}

const keyFor = (email: string, tab: WarmTab) => `${email}|${tab}`;

export async function readWarm<T>(
  email: string | null,
  tab: WarmTab,
  now: number = Date.now(),
): Promise<WarmHit<T> | null> {
  if (!email) return null;
  const key = keyFor(email, tab);
  const entry = (await idbGet(key)) as WarmEntry | undefined;
  if (!entry) return null;
  const age = now - entry.savedAt;
  // `age >= 0` also rejects a `savedAt` from the future or one that is not a number at all.
  const usable = entry.v === WARM_SCHEMA_VERSION
    && entry.email === email
    && entry.tab === tab
    && age >= 0 && age <= WARM_MAX_AGE_MS;
  if (!usable) {
    await idbDelete(key);
    return null;
  }
  return { data: entry.data as T, savedAt: entry.savedAt };
}

/** `since`: the `warmEpoch()` the sweep started under — a sign-out after it drops the write. */
export async function writeWarm(
  email: string | null,
  tab: WarmTab,
  data: unknown,
  now: number = Date.now(),
  since: number = warmEpoch(),
): Promise<void> {
  if (!email) return;
  const entry: WarmEntry = { v: WARM_SCHEMA_VERSION, email, tab, savedAt: now, data };
  await idbPut(keyFor(email, tab), entry, since);
}

/** Delete every entry that does not belong to `email` — all of them when there is no email. */
export async function purgeForeignWarm(email: string | null): Promise<void> {
  const prefix = email ? `${email}|` : null;
  const keys = await idbKeys();
  await Promise.all(keys.filter(k => prefix === null || !k.startsWith(prefix)).map(idbDelete));
}

/** When a cached set was fetched: "3:42 PM" for today, "Oct 8, 3:42 PM" otherwise — a cache can
 *  be days old, and a bare clock time would pass it off as this morning's. */
export function formatSavedAt(savedAt: number, now: number = Date.now()): string {
  const then = new Date(savedAt);
  const time = then.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  if (then.toDateString() === new Date(now).toDateString()) return time;
  return `${then.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}, ${time}`;
}
