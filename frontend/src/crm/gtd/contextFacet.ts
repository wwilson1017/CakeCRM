/**
 * The context facet for the pages that render `shared/search`'s bar directly.
 *
 * Inbox, Next Actions and Waiting keep bespoke bodies — each depends on SECTIONS,
 * which the collection layer's list view has no primitive for — so they own their
 * query/facet state and pass it to `SearchFilterBar` as fully-controlled props (the
 * kit's page-owns-state contract).
 *
 * Shared rather than duplicated per page, because a drift between them would be
 * exactly the silent divergence the shared layer exists to end.
 */
import type { FacetGroup, FacetOption } from '../../shared/search';

/**
 * Facet values are matched EXACTLY by the bar, while a plain `<select>` would compare
 * case-insensitively — so values are lower-cased on both sides and only the label
 * keeps the casing the user typed.
 */
export function contextOptions(contexts: string[]): FacetOption[] {
  return contexts.map(c => ({ value: c.toLowerCase(), label: c }));
}

export function contextGroup(
  contexts: string[],
  selected: (string | number)[],
  onToggle: (value: string | number) => void,
): FacetGroup {
  return { key: 'context', label: 'Context', options: contextOptions(contexts), selected, onToggle };
}

/**
 * Whether a todo passes the context facet.
 *
 * Multi-select is a deliberate superset of a single-select dropdown: an empty
 * selection means "all contexts", and selecting one behaves identically. A todo with
 * no context matches only when nothing is selected.
 */
export function matchesContexts(context: string, selected: (string | number)[]): boolean {
  return selected.length === 0 || selected.includes(context.toLowerCase());
}
