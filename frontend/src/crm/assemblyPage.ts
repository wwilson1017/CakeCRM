/**
 * CRM list-page assembly transport (issue #77).
 *
 * The #73 collection layer filters, searches and sorts an IN-MEMORY array — it has no
 * server-search hook — so a facet applied to a server-paginated slice would silently lie
 * about what matched. The three CRM list pages therefore sweep their whole corpus with
 * `usePageAssembly` before anything becomes filterable, and this module owns the wire
 * format of one page of that sweep.
 *
 * Two decisions are load-bearing:
 *
 * 1. **The sweep is a KEYSET walk (`after_id`), never OFFSET.** Membership is not stable
 *    across a multi-second sweep — contacts and tasks are hard-deleted here, and a task
 *    also leaves `list_tasks` when it is dropped or its deal is archived. Under OFFSET,
 *    one deletion behind the cursor shifts every later row back by one and a record is
 *    skipped entirely; under `id > cursor` a shift cannot move a row across the boundary.
 *    `sort=id` (an immutable, append-only key) is what makes the cursor meaningful: a row
 *    inserted mid-sweep sorts PAST the window instead of displacing it.
 *
 * 2. **`hasMore` comes from one extra row, never from a `total`.** `list_contacts` runs
 *    its COUNT and its page SELECT in two separate transactions, so an insert landing
 *    between them makes a `(page+1)*size < total` test report "done" and TRUNCATE the
 *    corpus. Asking for `size + 1` and testing what came back keeps the question inside a
 *    single query, and is also exact — a corpus that is an exact multiple of the page size
 *    neither costs an extra round-trip nor (on the last page) burns `MAX_PAGES`.
 *
 * Residual, and deliberately not engineered away: a row whose SERIAL id was allocated
 * before the cursor passed it but which COMMITS afterwards is missed until the next sweep.
 * That is inherent to any snapshot-less walk; the answer is the pages' Refresh control,
 * not a longer-lived transaction.
 */

/** Rows kept per assembly page. The endpoints cap `limit` at 1000, and we ask for
 *  `SIZE + 1`, so this must stay <= 999.
 *
 *  simplification: SIZE x usePageAssembly's MAX_PAGES (200) is a hard ceiling of 100,000
 *  rows per entity, past which the sweep reports that it did not terminate. The upgrade
 *  when an install outgrows that is server-side search + a virtualized list, not a bigger
 *  number here. */
export const CRM_LIST_PAGE_SIZE = 500;

/**
 * Query params for one page of the sweep. `afterId` is the id of the LAST row already
 * kept (`null` on the first page).
 *
 * Deliberately sends no `q` / `status` / `tags` / `owner_id`: the corpus is everything the
 * endpoint will return, and every facet is applied client-side from there.
 */
export function assemblyPageParams(afterId: number | null): URLSearchParams {
  const params = new URLSearchParams();
  params.set('sort', 'id');
  params.set('limit', String(CRM_LIST_PAGE_SIZE + 1));
  if (afterId !== null) params.set('after_id', String(afterId));
  return params;
}

/**
 * Split a raw page into the rows to keep and whether another page exists.
 *
 * The server was asked for one row more than a page holds, so an over-long response IS
 * the "there is more" signal — and the surplus row is dropped rather than kept, so the
 * next cursor is taken from the last KEPT row and that surplus row is re-fetched as the
 * head of the following page (never skipped, never duplicated in the assembled array).
 */
export function splitAssemblyPage<T>(rows: readonly T[]): { items: T[]; hasMore: boolean } {
  if (rows.length > CRM_LIST_PAGE_SIZE) {
    return { items: rows.slice(0, CRM_LIST_PAGE_SIZE), hasMore: true };
  }
  return { items: rows.slice(), hasMore: false };
}

/**
 * The cursor for the NEXT page: the id of the last kept row, or `null` for an empty page
 * (which can only be a final page, so the value is never used to fetch again).
 */
export function nextCursor<T>(items: readonly T[], getId: (item: T) => number): number | null {
  return items.length === 0 ? null : getId(items[items.length - 1]);
}
