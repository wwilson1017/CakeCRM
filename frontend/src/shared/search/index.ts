/**
 * Shared search / filter / sort module.
 *
 * ONE bar for every client-side board and list in the app — keyword search, facet filters and
 * sorting — plus the pure matching, sorting and persistence logic behind it. Import from this
 * barrel, never from a file inside the directory.
 *
 * The contract in one paragraph: the **page** owns the query, the facet selections and the
 * sort; `SearchFilterBar` is fully controlled; the page computes ONE filtered-and-sorted
 * array and every view — board, list, whatever comes next — renders from it. Domain-shaped
 * things (facet definitions, predicates, state shapes, sort-field getters) stay in the app;
 * this module knows nothing about any of them.
 *
 * Scope: **client-side surfaces that have already loaded their whole dataset** — it makes no
 * claim over server-paginated boards (a product table, CRM contacts and companies,
 * Usage), which filter on the server by design.
 */
//
// The barrel lists what consumers actually import, and nothing else — an export no one imports
// is dead surface that reads as proven API. `normalize`/`MAX_TOKENS`/`isAnchored` are
// promoted here for a sibling surface's tuning-bound delegates; `ChipButton` was promoted for
// `shared/collection`'s boolean facet chips.
export { default as SearchFilterBar, ChipButton } from './SearchFilterBar';
export { tokenize, buildDoc, docMatchesTokens, normalize, isAnchored, MAX_TOKENS } from './match';
export type { TokenizeOptions, MatchOptions } from './match';
export { sortItems, isManualSort, coerceSortState } from './sort';
export { loadPersistedState, savePersistedState, toggleValue } from './persist';
export type { FacetOption, FacetGroup, SortDir, SortState, SortFieldDef } from './types';
