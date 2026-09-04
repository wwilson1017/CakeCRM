// Strikethrough treatment for a voided (soft-deleted) row. The non-destructive standard renders
// voided records struck-through, never hidden, so the row stays auditable. Applied by the list
// and cards views only when a collection config supplies `getVoided`.

export const VOIDED_ROW_CLASS = 'line-through opacity-60';

/**
 * The `<tr>` variant (#148) — same treatment, but the dimming lands on the CELLS.
 *
 * CSS `opacity` composites an element's ENTIRE painting, its focus outline included. Now that
 * #148 has made list rows focusable, `opacity-60` on the `<tr>` would drop a focused voided
 * row's indicator to roughly 2.8:1 (light) / 1.8:1 (dark) — under the 3:1 WCAG 1.4.11 asks of a
 * focus indicator, and on exactly the rows the non-destructive standard insists stay visible and
 * openable. Reachable on any surface that declares both `getVoided` and `onSelect`.
 *
 * Deliberately a second constant rather than a change to `VOIDED_ROW_CLASS`: that one is also
 * applied to a div by `views/CardsView.tsx`, where a `[&>td]` child selector would silently
 * match nothing and drop the dimming altogether.
 */
export const VOIDED_TABLE_ROW_CLASS = 'line-through [&>td]:opacity-60';

export function voidedRowClass(voided: boolean): string {
  return voided ? VOIDED_ROW_CLASS : '';
}

/** `voidedRowClass` for a table row — see `VOIDED_TABLE_ROW_CLASS`. */
export function voidedTableRowClass(voided: boolean): string {
  return voided ? VOIDED_TABLE_ROW_CLASS : '';
}
