/**
 * The apply-time half of the pipeline's visible-intersection invariant (issue #55).
 *
 * Issue #55 requires bulk actions to operate on the currently filtered set. That has two
 * halves and both are needed: the COUNT the operator reads in the bulk bar, and the ids the
 * mutation actually runs over. A selection can change between the render that produced the
 * count and the click that applies it, so `PipelinePage` feeds BOTH from one call to this —
 * the bar's number and the request payload cannot disagree by construction.
 */

/**
 * The selected ids that are also visible, in SELECTION order.
 *
 * `visible` is the pipeline's `filteredDeals`, which already embeds #21's facet predicate —
 * including the stage facet that hides whole columns — so intersecting with it is exactly
 * "respecting the filters currently on screen". Deals selected before a filter narrowed the
 * board stay in the Set (so clearing the filter brings them back) but drop out of this
 * result, which is what stops a hidden deal from being moved by a click the operator made
 * about the visible ones.
 *
 * Order is preserved rather than incidental: `Set` iterates in insertion order, so filtering
 * it yields the order the operator clicked, which is the order the ids reach the server and
 * the order any per-deal errors come back in.
 */
export function applicableBulkIds<T extends { id: number }>(
  selected: ReadonlySet<number>,
  visible: readonly T[],
): number[] {
  const visibleIds = new Set<number>();
  for (const item of visible) visibleIds.add(item.id);
  return Array.from(selected).filter(id => visibleIds.has(id));
}
