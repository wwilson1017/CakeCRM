/**
 * Per-deal deep links: the frontend half of the shape, and the rules for acting on
 * one (issue #145).
 *
 * The backend attaches `/crm/pipeline?deal={id}` to every agent tool result that names
 * a deal, so the assistant can hand out a link instead of a title. This module holds
 * the browser's copy of that shape plus the logic `PipelinePage` runs when such a link
 * arrives.
 *
 * The logic lives here rather than inside the component because vitest runs in node:
 * anything left in `PipelinePage.tsx` is untestable in this repo, and the verdict rules
 * below are the whole correctness story of the feature.
 */

/** Query parameter that names the deal to open. */
export const DEAL_DEEP_LINK_PARAM = 'deal';

/**
 * Relative deep link that opens the pipeline board on a specific deal (#145).
 *
 * MUST stay byte-identical to `DEAL_PATH_TEMPLATE` in `backend/crm/links.py`. The
 * backend builds the same shape for every deal it hands the assistant, and
 * `PipelinePage` is the single parser both depend on. The duplication is unavoidable
 * — a browser cannot call Python — so it is pinned instead:
 * `test_the_frontend_producer_agrees_with_the_backend_one` in
 * `backend/tests/test_crm_deal_links.py` READS this file, extracts the template below,
 * and fails if it disagrees with the Python one. Editing this string alone breaks the
 * backend suite.
 */
export function dealDeepLink(dealId: number): string {
  return `/crm/pipeline?deal=${dealId}`;
}

/**
 * The deal id a `?deal=` value names, or null when it names none.
 *
 * Strict on purpose. The value is arbitrary text from an address bar, and every id that
 * reaches here becomes a lookup and possibly a "that deal is gone" accusation. `Number`
 * alone would accept `''` (→ 0), `' 42 '`, `4.5`, `1e3` and `0x2a`; only a run of
 * digits naming a positive integer is a deal id.
 */
export function parseDealDeepLinkId(raw: string | null | undefined): number | null {
  if (!raw || !/^\d+$/.test(raw)) return null;
  const id = Number(raw);
  return Number.isSafeInteger(id) && id > 0 ? id : null;
}

export interface DeepLinkVerdictInput {
  /** Deal id the URL names, or null when absent or unparseable. */
  dealId: number | null;
  /** Has a board payload ever been applied? NOT `!loading`. */
  boardLoaded: boolean;
  /** Did that deal id resolve to a deal on the loaded board? */
  dealOnBoard: boolean;
  /**
   * Has a board payload been applied SINCE this link arrived?
   *
   * False means the board on screen is older than the link, which is the one way a live
   * deal can be absent from it.
   */
  boardRefreshedSinceLink: boolean;
}

export type DeepLinkVerdict =
  /** Nothing to do — no link, or no board to decide against yet. */
  | 'idle'
  /** Open the deal's detail sheet. */
  | 'open'
  /** Ask for fresh board data; decide on the next payload. */
  | 'refresh'
  /** The id names no deal the board can show — say so. */
  | 'dead';

/**
 * What the pipeline should do about the deal a deep link names.
 *
 * Two rules are load-bearing, and both exist because the alternative is telling a rep
 * their live deal was deleted:
 *
 * 1. **It never decides before a payload has been applied.** `boardLoaded` is "a board
 *    is on screen", not `!loading` and not `!error`. `PipelinePage` returns the spinner
 *    while loading and `LoadError` when a first load failed, so a `dead` verdict reached
 *    on either of those states could only ever be an accusation about a network blip.
 *
 * 2. **A board older than the link earns one refresh before any accusation.** The
 *    common case is a cold navigation — the board is fetched after the link arrives, so
 *    it is authoritative about it. But the assistant also hands out links to deals it
 *    just created, and the pipeline stays mounted while its drawer is open: that board
 *    predates the deal and is silent about it, not evidence against it. So a miss on a
 *    stale board is `refresh`, and only a miss on a board fetched since the link is
 *    `dead`.
 *
 *    That refresh is bounded to ONE attempt without any flag here, and deliberately so:
 *    a `refresh` verdict is stable, so the caller's effect — keyed on the verdict and the
 *    deal id — does not re-run while it holds. A failed refresh applies no payload, so
 *    `boardRefreshedSinceLink` stays false, the verdict stays `refresh`, and the pipeline
 *    says NOTHING rather than risking a false accusation. A genuinely deleted deal then
 *    reads as the old silent no-op until the next successful load: worse than a correct
 *    notice, better than a confident wrong one. Encoding the attempt as an input here was
 *    tried and removed — it can only be maintained by a ref, and reading a ref during
 *    render is a build-blocking error under this repo's react-hooks ruleset.
 *
 * Note what is deliberately NOT an input: the board's facet filters. `dealOnBoard` is
 * asked of the whole payload, never of the filtered view — a session filter that hides a
 * card says nothing about whether the deal exists, and the detail sheet opens over the
 * board regardless of what the columns are showing.
 */
export function deepLinkVerdict(input: DeepLinkVerdictInput): DeepLinkVerdict {
  const { dealId, boardLoaded, dealOnBoard, boardRefreshedSinceLink } = input;
  if (dealId === null) return 'idle';
  if (!boardLoaded) return 'idle';
  if (dealOnBoard) return 'open';
  if (!boardRefreshedSinceLink) return 'refresh';
  return 'dead';
}
