/**
 * The background warm-up queue (#281, port of the blueprint's #3635).
 *
 * Once the Dashboard's own read has settled, Pipeline → Contacts → Companies are swept in the
 * background STRICTLY ONE AT A TIME — each starts only when the one before it has completed or
 * failed — and each complete set is written to the warm cache (`warmCache.ts`), so the first click
 * on each page opens on rows instead of a spinner. Never widen this to parallel sweeps: three
 * whole-corpus walks at once is exactly the load the pages' gates exist to stop.
 *
 * Divergences from the blueprint, both forced by CakeCRM's pages being ROUTES that remount rather
 * than hidden tabs that stay mounted:
 *
 *  • The queue cannot open a page's own gate (the page is not mounted), so it sweeps with its own
 *    imperative fetchers — the SAME wire format and endpoints the pages use (the list fetchers
 *    below are the pages' own), so the two can never cache different shapes.
 *  • A page that mounts runs its own sweep regardless (a remount re-sweeps; that is unchanged),
 *    so it CLAIMS its list here: a queued list is dropped and an in-flight one aborted, because
 *    two sweeps of one corpus is pure waste. The page's sweep writes the cache itself.
 *
 * Once per signed-in session, not on every Dashboard visit: the pages already re-sweep on every
 * entry, so the queue's only job is to fill a cold or week-old cache — re-running it on each
 * return to the Dashboard would add three whole-corpus sweeps per visit and buy nothing. A sign-out
 * (which bumps `warmEpoch`) or a different user re-arms it. Todos stays out: its corpus is per-user
 * and small, and the todo page has no wait worth hiding.
 */
import { api } from '../core/api/client';
import type { CrmCompany, CrmContact, CrmDeal } from '../core/types';
import { MAX_PAGES, PAGE_TIMEOUT_MS } from '../shared/collection/usePageAssembly';
import { assemblyPageParams, nextCursor, splitAssemblyPage } from './assemblyPage';
import { sweepPipelineDeals } from './pipelineAssembly';
import { writeWarm, type WarmTab } from './warmCache';
import { warmEpoch } from './warmStore';

export const WARM_ORDER: readonly WarmTab[] = ['pipeline', 'contacts', 'companies'];

/** One page of the Contacts sweep — shared by `ContactsPage` and the queue. */
export async function fetchContactRows(params: URLSearchParams, signal: AbortSignal): Promise<CrmContact[]> {
  return (await api<{ contacts: CrmContact[] }>(`/api/crm/contacts?${params}`, { signal })).contacts;
}

/** One page of the Companies sweep — shared by `CompaniesPage` and the queue. */
export async function fetchCompanyRows(params: URLSearchParams, signal: AbortSignal): Promise<CrmCompany[]> {
  return (await api<{ companies: CrmCompany[] }>(`/api/crm/companies?${params}`, { signal })).companies;
}

/** The imperative twin of `useCrmCorpus`'s sweep: keyset pages until the last, or throw. */
async function sweepList<T extends { id: number }>(
  fetchRows: (params: URLSearchParams, signal: AbortSignal) => Promise<T[]>,
  signal: AbortSignal,
): Promise<T[]> {
  const all: T[] = [];
  let afterId: number | null = null;
  for (let page = 0; page < MAX_PAGES; page++) {
    const controller = new AbortController();
    const onAbort = () => controller.abort();
    signal.addEventListener('abort', onAbort);
    const timer = setTimeout(onAbort, PAGE_TIMEOUT_MS);
    let rows: T[];
    try {
      rows = await fetchRows(assemblyPageParams(afterId), controller.signal);
    } finally {
      clearTimeout(timer);
      signal.removeEventListener('abort', onAbort);
    }
    if (signal.aborted) throw new Error('aborted');
    const { items, hasMore } = splitAssemblyPage(rows);
    all.push(...items);
    if (!hasMore) return all;
    afterId = nextCursor(items, r => r.id);
  }
  throw new Error('Loading did not terminate.');
}

export type WarmSweepers = Record<WarmTab, (signal: AbortSignal) => Promise<unknown>>;

const SWEEPERS: WarmSweepers = {
  // Live deals only — the board's default content set, and the only one it seeds from cache.
  pipeline: signal => sweepPipelineDeals(false, () => !signal.aborted, signal) as Promise<CrmDeal[]>,
  contacts: signal => sweepList(fetchContactRows, signal),
  companies: signal => sweepList(fetchCompanyRows, signal),
};

interface Run {
  key: string;
  /** The list being swept right now, and the controller that stops just that sweep. */
  tab: WarmTab | null;
  tabAbort: AbortController | null;
  /** Lists a mounted page has claimed — the queue skips them. */
  claimed: Set<WarmTab>;
}

let active: Run | null = null;
let doneKey: string | null = null;

/**
 * Start the warm-up for `email`, once per signed-in session. Resolves when the queue has finished
 * (or was superseded); never rejects — a failed sweep just moves the queue on.
 */
export async function startWarmUp(email: string | null, sweepers: WarmSweepers = SWEEPERS): Promise<void> {
  if (!email) return;
  const epoch = warmEpoch();
  const key = `${email}|${epoch}`;
  if (doneKey === key || active?.key === key) return;
  // A different user (a cross-tab sign-in swaps the account in place) supersedes the old run.
  active?.tabAbort?.abort();
  const run: Run = { key, tab: null, tabAbort: null, claimed: new Set() };
  active = run;
  const current = () => active === run && warmEpoch() === epoch;

  for (const tab of WARM_ORDER) {
    if (!current()) return;
    if (run.claimed.has(tab)) continue;
    run.tab = tab;
    run.tabAbort = new AbortController();
    try {
      const data = await sweepers[tab](run.tabAbort.signal);
      // Written only from a COMPLETE sweep that is still wanted: not superseded, not claimed
      // mid-flight by a page, and not overtaken by a sign-out (`writeWarm` re-checks the epoch).
      if (current() && !run.tabAbort.signal.aborted) await writeWarm(email, tab, data, Date.now(), epoch);
    } catch {
      // Failed or aborted: move on, no retry. The page's own sweep is the retry.
    }
  }
  if (active === run) {
    active = null;
    doneKey = key;
  }
}

/** A page is about to sweep `tab` itself: drop it from the queue, or stop it if it is in flight. */
export function claimWarmTab(tab: WarmTab): void {
  if (!active) return;
  active.claimed.add(tab);
  if (active.tab === tab) active.tabAbort?.abort();
}
