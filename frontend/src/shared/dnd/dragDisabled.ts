/**
 * Drag-disabling policy for the kanban board (issue #83).
 *
 * `dragDisabled` used to be a plain boolean meaning "the whole board is read-only"
 * (mobile, a bulk operation in flight). Archived deals need a PER-CARD answer instead:
 * they must be visible on the board so they can be found and restored, but they must not
 * drag, because the server refuses a stage change on an archived deal.
 *
 * Widening the prop to a union is only safe because the two questions it now answers are
 * genuinely different, and conflating them is a silent bug rather than a type error:
 *
 *   • "Is dragging off for THIS card?"   → resolveDragDisabled  (calls the predicate)
 *   • "Is dragging off for the WHOLE board?" → boardDragDisabled (a predicate is NOT)
 *
 * The board question is what gates the DragOverlay, and a truthiness test gets it exactly
 * backwards: a function is truthy, so `!dragDisabled` would suppress the overlay for every
 * card the moment any per-item policy was supplied — live cards would drag with no lifted
 * card following the pointer. Hence two named helpers rather than one inline check.
 */

/** Board-wide off (`true`), board-wide on (`false`/absent), or a per-card predicate. */
export type DragDisabled<TItem> = boolean | ((item: TItem) => boolean);

/** Whether dragging is disabled for one specific card. */
export function resolveDragDisabled<TItem>(
  dragDisabled: DragDisabled<TItem> | undefined,
  item: TItem,
): boolean {
  return typeof dragDisabled === 'function' ? dragDisabled(item) : dragDisabled === true;
}

/**
 * Whether dragging is disabled for the entire board. ONLY a literal `true` qualifies —
 * a per-card predicate means some cards still drag, so the board-level affordances
 * (the drag overlay) must stay mounted.
 */
export function boardDragDisabled<TItem>(dragDisabled: DragDisabled<TItem> | undefined): boolean {
  return dragDisabled === true;
}
