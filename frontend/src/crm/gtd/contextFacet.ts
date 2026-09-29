/**
 * The context facet for the pages that render `shared/search`'s bar directly.
 *
 * Inbox, Next Actions and Waiting keep bespoke bodies — each depends on SECTIONS,
 * which the collection layer's list view has no primitive for — so they own their
 * query/facet state and pass it to `SearchFilterBar` as fully-controlled props (the
 * kit's page-owns-state contract).
 *
 * Someday and Done instead go through `shared/collection` (#234) and declare their
 * equivalent facet in `collectionConfig.ts` — built from THIS module's `contextOptions`, so
 * the two routes share one value mapping.
 *
 * Shared rather than duplicated per page, because a drift between them would be
 * exactly the silent divergence the shared layer exists to end.
 */
import type { FacetGroup, FacetOption } from '../../shared/search';

/**
 * Facet values are matched EXACTLY by the bar, while a plain `<select>` would compare
 * case-insensitively — so values are lower-cased on both sides and only the label
 * keeps the casing the user typed.
 *
 * One option per lower-cased value, first spelling wins. Contexts are free text, so the
 * server's list can hold `@home` AND `@Home`; both already match the same todos here, and
 * two options sharing one value would render two identical chips under one React key.
 */
export function contextOptions(contexts: string[]): FacetOption[] {
  const seen = new Set<string>();
  const options: FacetOption[] = [];
  for (const c of contexts) {
    const value = c.toLowerCase();
    if (seen.has(value)) continue;
    seen.add(value);
    options.push({ value, label: c });
  }
  return options;
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
