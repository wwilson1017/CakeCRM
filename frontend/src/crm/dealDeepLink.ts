/**
 * The one place the deal deep link's shape lives.
 *
 * `DealDetailBody`'s Copy-link button WRITES this shape; `PipelinePage` READS it and then strips
 * the parameter immediately. That strip is the point of the contract: the address bar is never a
 * copy source, because by the time a rep could select it the parameter is already gone. So the
 * button must build the URL from the record id — never from `window.location` — and the two
 * halves must agree on one grammar, which is why both import it from here.
 */

/** The query key. Deals have no route of their own — the board is the page, a deal is a selection. */
export const DEAL_PARAM = 'deal';

/** Absolute-path form; the caller prefixes `window.location.origin` for a shareable link. */
export function dealDeepLink(dealId: number): string {
  return `/crm/pipeline?${DEAL_PARAM}=${dealId}`;
}

/**
 * Parse a raw query value into a deal id, or `null`.
 *
 * The accepted domain matches `usePublishActiveRecord`'s: a positive integer no larger than
 * Postgres `int4`. Deliberately not `Number()` — that coerces `''` to 0, `'1.5'` to 1.5 and
 * ` 3 ` to 3, and a URL a stranger can edit should not decide which record opens.
 */
export function parseDealParam(raw: string | null): number | null {
  if (raw === null || !/^\d+$/.test(raw)) return null;
  const id = Number(raw);
  return id > 0 && id <= 2147483647 ? id : null;
}
