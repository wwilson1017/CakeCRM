/**
 * Pipeline board transport (issue #59).
 *
 * The board used to fetch every live deal in one GET. This sweeps it in bounded keyset
 * pages instead — and that is ALL it changes: the sweep resolves only once the COMPLETE
 * corpus has landed, so #21's facets, the within-stage sort and #55's Select-All keep
 * operating over the whole dataset exactly as before. Pagination is transport here, never
 * a UX: no load-more, no truncated columns, no partial board. That is the model #77
 * established for the three CRM list pages (`assemblyPage.ts`: "a facet applied to a
 * server-paginated slice would silently lie about what matched") and the same call the
 * blueprint made for its own kanban.
 *
 * **Why a plain function and not `usePageAssembly`.** That hook owns a fetch lifecycle
 * driven by an effect, and `PipelinePage.load()` is imperative — it is called from
 * `replayDeferredLoad()` and from the bulk reconcile, outside any hook. So this is the
 * imperative twin: it reuses `assemblyPage.ts`'s wire format and the hook's own timing
 * constants rather than restating either, so the two sweeps cannot drift.
 *
 * Two contracts the board depends on, neither of them obvious:
 *
 * 1. **The wire order is not the delivered order.** Pages come back `id ASC`, because a
 *    cursor is only meaningful against an immutable key. But `PipelinePage` sorts each
 *    column by `lead_score` with a STABLE sort and relies on the server's recency order
 *    surviving underneath for equal scores — which is the common case, since `lead_score`
 *    is NULL until something recomputes it. Delivering id-ascending would silently flip
 *    most columns to oldest-first. So the assembled corpus is restored to
 *    `updated_at DESC, id DESC` — the server's own presentation order — before it is
 *    handed back.
 *
 * 2. **A superseded sweep stops.** Without a liveness check a stale run would keep
 *    fetching, one page per completed page; today a superseded load leaves at most one GET
 *    in flight. `isCurrent` is checked before every page so that stays true. The in-flight
 *    page is not aborted — that would be BETTER than today, but it means new machinery in
 *    `PipelinePage` for a request that is already bounded; parity is what "no behavior
 *    change" asks for. (simplification: pass an AbortSignal through `load()` if the extra
 *    request ever matters.)
 */
import { api } from '../core/api/client';
import type { CrmDeal } from '../core/types';
import { MAX_PAGES, PAGE_TIMEOUT_MS } from '../shared/collection/usePageAssembly';
import { assemblyPageParams, nextCursor, splitAssemblyPage } from './assemblyPage';

interface PipelineDealsPage {
  deals: CrmDeal[];
}

/** Thrown when a newer load has superseded this sweep. `load()` swallows it: its catch
 *  already reports nothing once `loadGen` has moved on. */
export class SweepSupersededError extends Error {
  constructor() {
    super('Pipeline sweep superseded by a newer load.');
    this.name = 'SweepSupersededError';
  }
}

/**
 * Fetch the COMPLETE board corpus in bounded keyset pages, newest-first on resolve.
 *
 * `includeArchived` is pinned by the caller for the whole sweep and carried on EVERY page,
 * so a mid-sweep facet flip can never interleave two different corpora — the flip starts a
 * new load, which supersedes this one wholesale.
 */
export async function sweepPipelineDeals(
  includeArchived: boolean,
  isCurrent: () => boolean = () => true,
): Promise<CrmDeal[]> {
  const all: CrmDeal[] = [];
  let afterId: number | null = null;

  for (let page = 0; page < MAX_PAGES; page++) {
    if (!isCurrent()) throw new SweepSupersededError();

    const params = assemblyPageParams(afterId);
    if (includeArchived) params.set('include_archived', 'true');

    // Per-page timeout, like the hook's: flaky Wi-Fi HANGS rather than fails, and a sweep
    // that stalls on page 3 must not pin the board's spinner forever.
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), PAGE_TIMEOUT_MS);
    let data: PipelineDealsPage;
    try {
      data = await api<PipelineDealsPage>(`/api/crm/deals?${params}`, {
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timer);
    }

    // The server was asked for one row more than a page holds, so an over-long response IS
    // the "there is more" signal; the surplus row is dropped and re-fetched as the head of
    // the next page (never skipped, never duplicated).
    const { items, hasMore } = splitAssemblyPage(data.deals);
    all.push(...items);
    if (!hasMore) return sortByRecency(all);
    afterId = nextCursor(items, d => d.id);
  }

  throw new Error('Loading did not terminate - the server kept reporting more deals.');
}

/** The server's presentation order (`updated_at DESC, id DESC`), restored after a sweep
 *  that walked the corpus in cursor order. ISO-8601 UTC timestamps compare correctly as
 *  strings, and the id tiebreaker (#58) makes the order total, so two deals saved in the
 *  same transaction can't shuffle between refreshes. */
function sortByRecency(deals: CrmDeal[]): CrmDeal[] {
  return deals.sort((a, b) =>
    a.updated_at === b.updated_at ? b.id - a.id : a.updated_at < b.updated_at ? 1 : -1,
  );
}
