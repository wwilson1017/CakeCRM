/**
 * A local write overlay on top of a `usePageAssembly` corpus (issue #77).
 *
 * The CRM list pages assemble their whole corpus once and then filter it client-side, so a
 * create/edit/delete can no longer be reflected by "refetch the current page" — there is no
 * current page. Re-sweeping the corpus after every write would be both slow and jarring
 * (the layer surfaces no partial set, so the table would blank out on each save).
 *
 * So writes are folded in from the SERVER'S OWN response body, keyed by id and held in a
 * map that is independent of the base array's identity — a write that lands while a sweep
 * is still running survives it.
 *
 * Three deliberate differences from the blueprint's version:
 *
 * - Entries are **merge patches** (`{...row, ...patch}`), not replacements. Several CRM
 *   write responses are narrower than a list row (a derived `last_contact_at`, a joined
 *   `contact_name`), and a replacement would blank exactly the columns the list renders.
 * - There is a **`remove(id)` tombstone**, because CakeCRM hard-deletes contacts and todos
 *   where the blueprint archives them.
 * - `retry()` **clears the overlay before restarting the sweep**. Without that, a patch
 *   written before the re-sweep merges back over the fresh rows and resurrects stale state
 *   — e.g. completing a repeating todo re-sweeps to pick up the spawned occurrence, and a
 *   surviving `completed: 0` patch from an earlier edit would un-complete the original.
 *   Writes arriving DURING the new sweep still apply: they land in the cleared map, and
 *   the assembly publishes no rows until it finishes.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { usePageAssembly, type PageAssembly } from '../shared/collection';
import { ApiError } from '../core/api/client';
import { assemblyPageParams, nextCursor, splitAssemblyPage } from './assemblyPage';

/** Tombstone for a hard-deleted row. A unique object, so it can never collide with a patch. */
const REMOVED = Symbol('removed');

type Entry<T> = Partial<T> | typeof REMOVED;

export interface PatchableAssembly<T> {
  /** The corpus with local writes folded in — null until the sweep completes. */
  items: T[] | null;
  /** Fold a server response body in: patches an existing row, or prepends a new one. */
  upsert: (item: T) => void;
  /** Mark a row deleted locally. */
  remove: (id: number) => void;
  /** Discard local writes and re-sweep the corpus from the server. */
  retry: () => void;
}

/**
 * Fold `overlay` into `base`. Exported for testing — the ordering rules are the part worth
 * pinning, and they are pure.
 *
 * Rows unknown to `base` are treated as creations and placed FIRST, so a record the user
 * just added is visible without hunting for it in id order. (The layer sorts the result
 * anyway; this only decides the resting order and the stable-sort tiebreak.)
 */
export function applyOverlay<T>(
  base: readonly T[],
  overlay: ReadonlyMap<number, Entry<T>>,
  getId: (item: T) => number,
): T[] {
  if (overlay.size === 0) return base.slice();
  const seen = new Set<number>();
  const out: T[] = [];
  for (const row of base) {
    const id = getId(row);
    seen.add(id);
    const entry = overlay.get(id);
    if (entry === REMOVED) continue;
    // A patch that omits a field must not blank it; a patch that carries an explicit
    // null MUST overwrite (unlinking a todo's contact is a real edit).
    out.push(entry === undefined ? row : { ...row, ...entry });
  }
  const created: T[] = [];
  for (const [id, entry] of overlay) {
    if (seen.has(id) || entry === REMOVED) continue;
    created.push(entry as T);
  }
  return created.length === 0 ? out : [...created, ...out];
}

export default function usePatchableAssembly<T>(
  assembly: PageAssembly<T>,
  getId: (item: T) => number,
): PatchableAssembly<T> {
  const [overlay, setOverlay] = useState<ReadonlyMap<number, Entry<T>>>(() => new Map());

  const upsert = useCallback((item: T) => {
    setOverlay(prev => {
      const next = new Map(prev);
      next.set(getId(item), item as Partial<T>);
      return next;
    });
  }, [getId]);

  const remove = useCallback((id: number) => {
    setOverlay(prev => {
      const next = new Map(prev);
      next.set(id, REMOVED);
      return next;
    });
  }, []);

  const assemblyRetry = assembly.retry;
  const retry = useCallback(() => {
    // Order matters only for readability — both are state updates in one batch — but the
    // INTENT is: the overlay describes writes against the corpus we are about to throw
    // away, so it must not outlive it.
    setOverlay(new Map());
    assemblyRetry();
  }, [assemblyRetry]);

  const base = assembly.items;
  const items = useMemo(
    () => (base === null ? null : applyOverlay(base, overlay, getId)),
    [base, overlay, getId],
  );

  return { items, upsert, remove, retry };
}

/**
 * Whether a failed write may nonetheless have reached the database (#55's distinction,
 * applied to a client-loaded corpus).
 *
 * A definite 4xx refusal never wrote anything, so the corpus is still correct and the page
 * only has to say so. Anything else — a 5xx, or a transport failure with no status at all —
 * is genuinely unknown: the connection can drop after Postgres has committed but before the
 * response arrives. There the honest move is to re-sweep rather than keep rendering a row
 * we can no longer vouch for.
 */
export function writeMayHaveLanded(err: unknown): boolean {
  return !(err instanceof ApiError && err.status >= 400 && err.status < 500);
}

/**
 * Whether a failed request says the row no longer exists.
 *
 * The one 4xx that proves the CORPUS wrong even though it proves the WRITE never
 * happened. Since #60 rows are deleted by other seats — and by the assistant, Telegram
 * and the capture surface — behind a swept corpus's back, so a 404 here means the local
 * copy is a ghost: `writeMayHaveLanded` alone would leave it on screen, failing the same
 * way on every click, until someone hit Refresh. Before #77 the next keystroke refetched
 * and it vanished on its own.
 */
export function rowIsGone(err: unknown): boolean {
  return err instanceof ApiError && err.status === 404;
}

/** How stale a backgrounded corpus may be before returning to the tab re-sweeps it.
 *  Long enough that flicking between tabs never refetches; short enough that a morning's
 *  worth of other people's writes is never silently filtered against. */
export const CORPUS_MAX_AGE_MS = 10 * 60 * 1000;

/** What a page needs to render a swept corpus: the rows plus the assembly's own progress. */
export interface CrmCorpus<T> extends PatchableAssembly<T> {
  loading: boolean;
  error: string | null;
  itemsLoaded: number;
}

/**
 * Sweep a CRM list endpoint's whole corpus and keep local writes on top of it.
 *
 * The keyset cursor lives in a ref rather than in the page number `usePageAssembly` hands
 * out, because the two describe different things: the page number is just "how many
 * requests in", while the cursor is the id we actually stopped at. Page 0 resets it, which
 * is exactly what `retry()` restarts from — so a re-sweep can never resume from a stale
 * position.
 *
 * `fetchRows` receives ready-made query params and returns the raw array from the response
 * envelope (each endpoint names it differently); everything else — cursor, page splitting,
 * `hasMore`, the write overlay — is handled here.
 */
export function useCrmCorpus<T extends { id: number }>(
  fetchRows: (params: URLSearchParams, signal: AbortSignal) => Promise<T[]>,
  enabled = true,
): CrmCorpus<T> {
  // Latest-ref, for the same reason usePageAssembly keeps one: the natural call site is an
  // inline arrow, and depending on its identity would restart the sweep every render.
  // Written in an effect, not during render — a render React discards must not leak.
  const fetchRef = useRef(fetchRows);
  useEffect(() => {
    fetchRef.current = fetchRows;
  });

  const cursor = useRef<number | null>(null);
  const fetchPage = useCallback(async (page: number, signal: AbortSignal) => {
    if (page === 0) cursor.current = null;
    const rows = await fetchRef.current(assemblyPageParams(cursor.current), signal);
    const { items, hasMore } = splitAssemblyPage(rows);
    // Advance only on a non-empty page; an empty one is terminal, so the old value is
    // never read again either way.
    cursor.current = nextCursor(items, r => r.id) ?? cursor.current;
    return { items, hasMore };
  }, []);

  const assembly = usePageAssembly<T>(fetchPage, enabled);
  const getId = useCallback((row: T) => row.id, []);
  const patchable = usePatchableAssembly(assembly, getId);

  // Bound how stale the corpus can get while nobody touches it.
  //
  // A swept corpus never reloads on its own, and this app has several writers the page
  // cannot see: the assistant's CRM tools, Telegram capture, the Gmail touch scan, other
  // seats (#60), a second tab. Before #77 any keystroke round-tripped and picked those up
  // incidentally; now only Refresh does, so a tab left open for a week would filter and
  // search week-old data with nothing on screen saying so.
  //
  // Re-sweeping when the tab is brought back to the foreground after a long absence is the
  // cheap general answer — it covers every one of those writers rather than wiring the
  // page to any particular one, and it costs nothing while the tab is in use.
  const swept = assembly.items !== null;
  const patchableRetry = patchable.retry;
  const sweptAt = useRef(0);
  useEffect(() => {
    if (swept) sweptAt.current = Date.now();
  }, [swept]);
  useEffect(() => {
    if (!enabled) return;
    const onVisible = () => {
      if (document.visibilityState !== 'visible') return;
      if (sweptAt.current === 0) return;                       // nothing assembled yet
      if (Date.now() - sweptAt.current < CORPUS_MAX_AGE_MS) return;
      patchableRetry();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [enabled, patchableRetry]);

  return {
    ...patchable,
    loading: assembly.loading,
    error: assembly.error,
    itemsLoaded: assembly.itemsLoaded,
  };
}
