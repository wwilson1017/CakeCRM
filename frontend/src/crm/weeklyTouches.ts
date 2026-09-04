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

/** The URL spelling of the null-owner bucket. `deals.owner_id` is nullable forever (#60),
 *  so the drill-down needs a way to address "nobody" that is not an absent param. */
export const UNASSIGNED_OWNER_PARAM = 'unassigned';

export function ownerParamOf(userId: number | null): string {
  return userId === null ? UNASSIGNED_OWNER_PARAM : String(userId);
}

/** The parsed owner, kept as the RAW TOKEN rather than a number.
 *
 *  Deliberately not `Number(raw)` for the value it carries: the page only ever forwards
 *  this back as a query param, so converting it buys nothing and costs precision on a very
 *  long digit string. The BOUNDS below are checked, though — and they mirror the server's
 *  `parse_touch_owner` exactly (positive, at most a 32-bit id). Not for security, which is
 *  the server's job either way, but for the message: an id the server rejects with a 400
 *  would otherwise reach the page as a generic 4xx and be reported as a bad date range,
 *  sending the user to look at the wrong half of the URL.
 *
 *  The length test comes first, so `Number()` only ever sees at most ten digits and is
 *  exact. `ok: false` is a malformed URL, which the page renders without ever fetching. */
export type OwnerParam = { ok: true; owner: string } | { ok: false };

/** `users.id` is a 32-bit SERIAL, the same ceiling the server enforces. */
const MAX_OWNER_ID = 2147483647;

export function parseOwnerParam(raw: string | undefined): OwnerParam {
  if (raw === UNASSIGNED_OWNER_PARAM) return { ok: true, owner: UNASSIGNED_OWNER_PARAM };
  if (raw === undefined || !/^[0-9]{1,10}$/.test(raw)) return { ok: false };
  const id = Number(raw);
  if (id < 1 || id > MAX_OWNER_ID) return { ok: false };
  return { ok: true, owner: raw };
}

/**
 * The detail page's path, carrying the card's CUSTOM range when one is applied and nothing
 * at all when the card is on its rolling default.
 *
 * It forwards the same `YYYY-MM-DD` days the card sends, NOT the exact instants the
 * payload's `window` reports — which is the opposite of what this function did first, and
 * the reason is worth keeping: freezing the bounds looks like it guarantees the page lists
 * what the clicked number counted, but membership is "this deal's CURRENT most recent touch
 * falls in the window", so a touch made after the card rendered pushes the deal past a
 * frozen upper bound and DELETES it from the page — including a touch made from that page.
 * Sending the window's KIND instead lets the server ask the same question at open time.
 */
export function touchDetailPath(
  userId: number | null,
  applied: { start: string; end: string } | null,
): string {
  const path = `/crm/touches/${ownerParamOf(userId)}`;
  if (!applied) return path;
  const qs = new URLSearchParams({ start: applied.start, end: applied.end });
  return `${path}?${qs}`;
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
  for (const key of ['start', 'end']) {
    const value = search.get(key);
    if (value) qs.set(key, value);
  }
  return `/api/crm/dashboard/weekly-touches/detail?${qs}`;
}
