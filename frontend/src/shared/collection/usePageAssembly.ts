/**
 * Paged-fetch accumulator for client-loaded working sets.
 *
 * The collection layer's scope is surfaces that hold their WHOLE dataset client-side; a
 * server-paged endpoint joins that scope by assembling every page into one array BEFORE the
 * set is presented as filterable — rendering
 * a facet bar over one server page silently filters a subset, the exact failure the CRM
 * Contacts/Orders adoptions must avoid.
 *
 * Fetch discipline is a sibling surface's, generalized: AbortController tied to unmount, a per-page
 * timeout (flaky Wi-Fi HANGS rather than fails), stale-run rejection via a monotonic
 * token, and a retry that restarts the whole assembly (partial sets are never surfaced —
 * progressive rendering would make facet counts drift as pages land).
 */
import { useCallback, useEffect, useRef, useState } from 'react';

export interface AssemblyPage<T> {
  items: T[];
  hasMore: boolean;
}

export interface PageAssembly<T> {
  /** The COMPLETE set — null until every page has landed. Never a partial set. */
  items: T[] | null;
  loading: boolean;
  /** Human-readable failure; retry() clears it and restarts from page 0. */
  error: string | null;
  /** Pages fetched so far — drive a "Loading… page N" progress line, never a bare spinner. */
  pagesLoaded: number;
  itemsLoaded: number;
  retry: () => void;
}

/** One assembly's output, tagged with the run it belongs to. */
interface RunRecord<T> {
  key: number;
  items: T[] | null;
  error: string | null;
  pages: number;
  count: number;
}

const BLANK_RUN = { items: null, error: null, pages: 0, count: 0 } as const;

export const PAGE_TIMEOUT_MS = 30_000;
/** Runaway-loop backstop, far above any real working set (a server that always says
 *  hasMore=true must terminate the assembly loudly, not spin forever). */
export const MAX_PAGES = 200;

export default function usePageAssembly<T>(
  /** Fetch ONE page. Must forward `signal` to the transport (`api()` accepts RequestInit) and
   *  order pages by an immutable key server-side so a concurrent insert cannot skip rows.
   *  An inline arrow is fine: identity changes never restart the assembly (each page call
   *  reads the latest fetcher); `retry()` is the one way to reassemble. */
  fetchPage: (page: number, signal: AbortSignal) => Promise<AssemblyPage<T>>,
  /**
   * Gate for a surface that is MOUNTED but not yet shown. While false the hook fetches
   * nothing and reports `loading: true` — deliberately NOT `items: []`, which would let a facet
   * bar and its counts render over an empty set, the exact failure this module exists to prevent.
   *
   * It is needed because "mounted" and "visible" are not the same thing: the blueprint's CRM
   * page shell renders all four tabs and hides the inactive ones with a `hidden` class, never unmounting
   * them, so an ungated assembly swept the entire Companies AND Contacts corpora the moment
   * anyone opened CRM on the Dashboard tab.
   *
   * The gate is LATCHING — once it has been true, flipping it back to false never discards the
   * assembled set or re-runs the sweep, so switching away from a tab and back is free. `retry()`
   * remains the one way to reassemble. Omitted ⇒ always enabled, so every existing call site is
   * unchanged.
   */
  enabled = true,
): PageAssembly<T> {
  // The latch is React's documented ADJUST-STATE-DURING-RENDER pattern: set state during render,
  // guarded so it runs only on the transition, and React re-renders this component immediately
  // without committing or painting the discarded pass.
  //
  // It was a render-phase ref write first, and that was wrong: a ref mutation escapes the render
  // it happened in, so a render React abandons (concurrent rendering may throw one away) would
  // still have armed the latch permanently — a surface that was never shown would sweep its whole
  // corpus. State adjustment cannot leak that way, and it needs no effect (a setState-in-effect
  // would arm the latch a commit late, one wasted render after the gate opened).
  const [armed, setArmed] = useState(enabled);
  if (enabled && !armed) setArmed(true);

  const [runKey, setRunKey] = useState(0);
  // Everything an assembly produces, STAMPED with the run it describes. Keeping it in one
  // run-keyed record is what lets the effect start a run without first writing "empty" three
  // times: a bumped `runKey` stops the previous run's record from matching, so the hook reports
  // loading again on the very same render that schedules the new assembly. Resetting instead
  // would mean a synchronous setState in the effect body — a cascading render on every retry.
  const [record, setRecord] = useState<RunRecord<T>>({ ...BLANK_RUN, key: 0 });
  const current: RunRecord<T> = record.key === runKey ? record : { ...BLANK_RUN, key: runKey };
  // The ref is the authority on staleness — a late resolution from a superseded run must not
  // clobber fresher state (the useCrmList bug class this module exists to not repeat).
  const runRef = useRef(0);
  // Latest-ref: the assembly effect must NOT depend on fetchPage identity. The natural
  // consumer shape is an inline arrow — a fresh identity every render — and depending on it
  // would abort and restart the assembly from page 0 on every parent render, self-sustained
  // by this hook's own progress writes (each restart triggers a render, which mints another
  // arrow, which restarts again). The effect reads the ref at each page call instead.
  const fetchPageRef = useRef(fetchPage);
  useEffect(() => {
    fetchPageRef.current = fetchPage;
  });

  useEffect(() => {
    // Not yet shown: no fetch, and no state write either — `items: null` + `error: null` already
    // reads as `loading: true`, which is the honest report for "the set is not here yet".
    if (!armed) return;
    const run = ++runRef.current;
    const controller = new AbortController();
    // The timer records itself: `signal.aborted` alone cannot distinguish the page timeout
    // from any other abort reaching the shared controller (e.g. a fetcher that swallows the
    // abort and lets the NEXT page fail on the already-aborted signal).
    let timedOut = false;
    /** Merge into THIS run's record, replacing any older run's outright. */
    const commit = (patch: Partial<Omit<RunRecord<T>, 'key'>>) =>
      setRecord(prev => ({ ...(prev.key === runKey ? prev : BLANK_RUN), key: runKey, ...patch }));

    (async () => {
      const all: T[] = [];
      for (let page = 0; page < MAX_PAGES; page++) {
        // Timer scoped to the page: the finally always clears it, and an unmount abort
        // rejects the in-flight fetch so no timer outlives the effect.
        const timer = setTimeout(() => {
          timedOut = true;
          controller.abort();
        }, PAGE_TIMEOUT_MS);
        let result: AssemblyPage<T>;
        try {
          result = await fetchPageRef.current(page, controller.signal);
        } catch (err) {
          if (runRef.current !== run) return; // superseded — say nothing
          if (timedOut) {
            commit({ error: 'Loading timed out. Check the connection and retry.' });
          } else {
            commit({ error: err instanceof Error ? err.message : 'Loading failed.' });
          }
          return;
        } finally {
          clearTimeout(timer);
        }
        if (runRef.current !== run) return;
        all.push(...result.items);
        commit({ pages: page + 1, count: all.length });
        if (!result.hasMore) {
          commit({ items: all });
          return;
        }
      }
      if (runRef.current === run) {
        commit({ error: 'Loading did not terminate — the server kept reporting more pages.' });
      }
    })();

    return () => {
      // Advancing the token BEFORE aborting is what makes the abort silent: the catch above
      // observes a stale run and returns without touching state.
      runRef.current += 1;
      controller.abort();
    };
  }, [runKey, armed]);

  const retry = useCallback(() => setRunKey(k => k + 1), []);

  return {
    items: current.items,
    loading: current.items === null && current.error === null,
    error: current.error,
    pagesLoaded: current.pages,
    itemsLoaded: current.count,
    retry,
  };
}
