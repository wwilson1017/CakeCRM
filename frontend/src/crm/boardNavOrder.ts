/**
 * The ‹ › order for the pipeline board's detail panel.
 *
 * It IS the board, flattened: column-major over the columns actually RENDERED, and within a
 * column exactly as `grouped` already sorted them (`lead_score` desc, `updated_at` desc as the
 * tiebreak). That is the same shape `shared/collection`'s `visibleOrder` produces for a kanban
 * view, computed here instead because the board is the page's own — it keeps issue #21's filter
 * bar and runs no `useCollectionState` for the layer to read.
 *
 * Two details are load-bearing. It walks the caller's `visibleStages`, never `STAGE_ORDER`: a
 * hidden column is not something the user is looking at, so ‹ › must not page through it. And it
 * applies no cap — the board renders every card in a column, and even if it did not, navigation
 * walks the whole filtered set rather than the rendered slice (the layer's stated rule).
 */
import type { CrmDeal } from '../core/types';

export function boardNavOrder(
  visibleStages: readonly string[],
  grouped: Readonly<Record<string, readonly CrmDeal[]>>,
): number[] {
  return visibleStages.flatMap(stage => (grouped[stage] ?? []).map(deal => deal.id));
}
