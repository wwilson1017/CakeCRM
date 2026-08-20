/**
 * Shared search / filter / sort types.
 *
 * The facet prop shapes (`FacetOption`, `FacetGroup`) are adopted from a sibling surface's bar
 * built in the blueprint, deliberately: that work is frozen, so its
 * migration onto this module later is an import swap rather than a rewrite. Its `ToggleFacet`
 * (a standalone boolean chip) is deliberately NOT carried here — neither consumer has one, and
 * a prop nothing sets is exactly the shape this module argues against elsewhere. The migration
 * PR adds it back, with a caller. The sort model is generalised from CRM's `pipelineSort.ts`.
 */

/** One selectable value inside a facet group. */
export interface FacetOption {
  value: string | number;
  label: string;
  /** Optional swatch — CRM renders stage colors here. */
  color?: string | null;
  /**
   * Offered but not currently choosable (a sibling surface stage the board is not
   * rendering because it is hidden or closed). A SELECTED value is never actually disabled —
   * the bar keeps it clickable so a stale filter can always be undone (see `FacetRow`).
   */
  disabled?: boolean;
  /** Tooltip explaining why the option is disabled (e.g. "Turn on Show closed to filter this lane"). */
  disabledReason?: string;
}

/**
 * A multi-select facet. The app owns the selection state and the toggle behaviour; this is
 * purely how the bar is told what to draw.
 *
 * Single-select facets are expressed as a group whose `onToggle` replaces-or-clears rather
 * than accumulating — the bar does not need to know the difference.
 */
export interface FacetGroup {
  key: string;
  label: string;
  options: FacetOption[];
  selected: (string | number)[];
  onToggle: (value: string | number) => void;
  /** Short helper line under the group's options. */
  hint?: string;
  /**
   * `'chips'` (default) suits a small enumerable facet. `'list'` renders a searchable,
   * scrollable checklist and mounts its rows ONLY while that group is expanded — required
   * for high-cardinality facets (a sibling surface's supplier and location lists run to hundreds of
   * values, and mounting four of them at once is a thousand-plus nodes on a low-powered
   * tablet).
   */
  display?: 'chips' | 'list';
}

export type SortDir = 'asc' | 'desc';

/**
 * The sort selection. `field` matches a `SortFieldDef.value`.
 *
 * Deliberately a bare `string` rather than a generic key so the state can cross a component
 * boundary (the bar, a list view's column headers, sessionStorage) without every consumer
 * carrying the item type. Apps recover the safety a generic key would have given by passing
 * their `as const satisfies` field table STRAIGHT to `SortControl` and `coerceSortState`, so a
 * control option can never name a field that has no definition behind it — the failure a bare
 * string would otherwise allow (a label renders, and `sortItems` silently returns input order).
 */
export interface SortState {
  field: string;
  dir: SortDir;
}

/**
 * One sortable field.
 *
 * A discriminated union so a sortable field CANNOT be declared without a getter: with an
 * optional `get`, a forgotten or typo'd getter yields `undefined` for every row, every pair
 * ties, and the sort silently does nothing — no error, no visual signal. `tsc -b` refuses it
 * instead.
 *
 * `get` returns `null` for "no value for this field", which sorts to the bottom in BOTH
 * directions. It must never return `0` for an unparseable value: `0` is a real number and
 * would sort to the TOP ascending. For a timestamp column that may be unparseable, parse
 * with `core/utils/date.ts`'s `parseUTC` and map `Number.isNaN` to `null` — do not reach for
 * `utcMillis`, whose `0`-on-failure contract is documented safe only in a descending sort.
 */
export type SortFieldDef<T> =
  | {
      value: string;
      label: string;
      /**
       * The field IS the array's own order — sorting short-circuits to the input order
       * rather than re-sorting a column that goes stale after an optimistic drag. This is
       * the only field kind for which `isManualSort` can be true, and therefore the only
       * one under which drag-to-reorder is safe.
       */
      arrayOrder: true;
      get?: never;
    }
  | {
      value: string;
      label: string;
      arrayOrder?: false;
      get: (item: T) => number | string | null;
    };
