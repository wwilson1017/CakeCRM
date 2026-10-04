// Todo-GTD — shared meta (status counts, contexts, tags, projects).
//
// The app uses flat sibling routes, so the shell unmounts on every tab switch. A
// module-level stale-while-revalidate cache keeps the tab badges and dropdowns from
// flashing empty on each switch: cached meta renders instantly, a background refetch
// follows, and every mutation calls refreshMeta() to keep the inbox badge honest.
//
// The blueprint keys this cache by signed-in user because CAKE is multi-user.
// CakeCRM is single-user (and the public todo surface has no user at all), so there
// is nothing to key on and the owner machinery is deliberately not ported.
import { useCallback, useEffect, useState, useSyncExternalStore } from 'react';
import { getFilters, listProjects } from './api';
import type { TodoFilters, TodoProject } from './types';

export interface TodoMeta {
  filters: TodoFilters | null;
  projects: TodoProject[];
  loaded: boolean;
}

const EMPTY: TodoMeta = { filters: null, projects: [], loaded: false };
let cache: TodoMeta = EMPTY;
const listeners = new Set<(m: TodoMeta) => void>();
let inflight: Promise<void> | null = null;
// At most one follow-up fetch chained behind `inflight` — see refreshMeta.
let queued: Promise<void> | null = null;

async function fetchMeta(): Promise<void> {
  try {
    const [filters, projects] = await Promise.all([getFilters(), listProjects()]);
    cache = { filters, projects, loaded: true };
    listeners.forEach(l => l(cache));
  } catch {
    // stale-while-revalidate: keep the last good snapshot on a failed refresh
  }
}

function startFetch(): Promise<void> {
  const p: Promise<void> = fetchMeta().then(() => {
    if (inflight === p) inflight = null;
  });
  inflight = p;
  return p;
}

/**
 * Module-level so mutation helpers (hooks.ts) can revalidate without a component
 * handle.
 *
 * A refresh asked for WHILE one is in flight does NOT join it. Every caller is a
 * mutation saying "something changed just now", and the running fetch may already
 * have read its half of the data before that write committed — `getFilters()` and
 * `listProjects()` resolve independently, so the slower one decides when the pair
 * commits. Joining it can therefore install a snapshot that predates the change: a
 * project created mid-fetch stays missing from the dropdown that TriageCard tells the
 * user to pick from, and the select can hold a project_id with no matching option. So
 * chain exactly ONE follow-up — a burst of mutations collapses into a single extra
 * fetch rather than one per call.
 */
export function refreshMeta(): Promise<void> {
  if (inflight) {
    if (!queued) {
      queued = inflight.then(() => { queued = null; return startFetch(); });
    }
    return queued;
  }
  return startFetch();
}

/** Test seam: reset the module cache between cases. */
export function __resetTodoMeta(): void {
  cache = EMPTY;
  inflight = null;
  queued = null;
}

export function useTodoMeta(): TodoMeta & { refreshMeta: () => Promise<void> } {
  const [meta, setMeta] = useState<TodoMeta>(cache);

  useEffect(() => {
    const listener = (m: TodoMeta) => setMeta(m);
    listeners.add(listener);
    void refreshMeta(); // render cached instantly, revalidate in background
    return () => { listeners.delete(listener); };
  }, []);

  const refresh = useCallback(() => refreshMeta(), []);
  return { ...meta, refreshMeta: refresh };
}

function subscribeMeta(onChange: () => void): () => void {
  const listener = () => onChange();
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

/**
 * The install's timezone from the cached filters payload (#259), for components that
 * derive "today" but have no reason to hold the whole meta (TodoRow, QuickAdd). It does
 * not fetch: every GTD page renders inside TodoShell, whose `useTodoMeta` does. Undefined
 * until the payload lands, which `todayStr`/`zonedNow` treat as the browser's day.
 */
export function useListTz(): string | undefined {
  return useSyncExternalStore(subscribeMeta, () => cache.filters?.tz, () => undefined);
}
