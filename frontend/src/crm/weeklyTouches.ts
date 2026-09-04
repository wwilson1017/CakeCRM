/**
 * Pure URL helpers for the Weekly Touches drill-down (issue #146).
 *
 * These exist so the owner bucket has exactly ONE spelling in a URL, shared by the card's
 * links, the detail page's parser and the API call it builds — and so that spelling is
 * mirrored by `crm.service.parse_touch_owner` on the server rather than reinvented at each
 * call site.
 *
 * Extracted rather than inlined for the usual reason a pure module earns its place: the
 * encoding rules below (a `+` inside an ISO offset, a bucket that is `null`) are exactly
 * the kind of thing that is easy to get subtly wrong and cheap to pin in a test with no DOM.
 */

import type { CrmWeeklyTouches } from '../core/types';

/** The URL spelling of the null-owner bucket. `deals.owner_id` is nullable forever (#60),
 *  so the drill-down needs a way to address "nobody" that is not an absent param. */
export const UNASSIGNED_OWNER_PARAM = 'unassigned';

export function ownerParamOf(userId: number | null): string {
  return userId === null ? UNASSIGNED_OWNER_PARAM : String(userId);
}

/** The parsed owner, kept as the RAW TOKEN rather than a number.
 *
 *  Deliberately not `Number(raw)`: the page only ever forwards this value back as a query
 *  param, so converting it buys nothing and costs precision on an id past 2^53 — the
 *  server is the one that range-checks it against a 32-bit column. `ok: false` is a
 *  malformed URL, which the page renders without ever issuing a request. */
export type OwnerParam = { ok: true; owner: string } | { ok: false };

export function parseOwnerParam(raw: string | undefined): OwnerParam {
  if (raw === UNASSIGNED_OWNER_PARAM) return { ok: true, owner: UNASSIGNED_OWNER_PARAM };
  if (raw !== undefined && /^[0-9]+$/.test(raw)) return { ok: true, owner: raw };
  return { ok: false };
}

/**
 * The detail page's path, carrying the EXACT window bounds currently on screen.
 *
 * Built from the payload's `window`, never from the card's `applied` filter state: a
 * custom range whose fetch failed leaves `applied` describing a window the numbers on
 * screen do not, and the drill-down must list what the number counted. On the rolling
 * default window this is what stops the page from re-resolving "last 7 days" against a
 * later `now` and quietly listing a different week.
 *
 * `URLSearchParams` is what encodes the `+` in a `+00:00` offset; string concatenation
 * would deliver it to the server as a space, which `fromisoformat` then rejects.
 */
export function touchDetailPath(
  userId: number | null,
  window: CrmWeeklyTouches['window'],
): string {
  const qs = new URLSearchParams({ ws: window.start, we: window.end });
  return `/crm/touches/${ownerParamOf(userId)}?${qs}`;
}

/**
 * The API URL for the detail page: the owner, plus whichever window params its own URL
 * carries, forwarded verbatim.
 *
 * The client does no date arithmetic at all — the server validates the pair and owns the
 * label, so a page reached by a hand-typed URL and one reached from the card go through
 * exactly the same resolution.
 */
export function touchDetailApiPath(owner: string, search: URLSearchParams): string {
  const qs = new URLSearchParams({ owner });
  for (const key of ['ws', 'we', 'start', 'end']) {
    const value = search.get(key);
    if (value) qs.set(key, value);
  }
  return `/api/crm/dashboard/weekly-touches/detail?${qs}`;
}
