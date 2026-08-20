/**
 * sessionStorage helpers for filter and sort state.
 *
 * `sessionStorage`, not `localStorage`, on purpose: a filter the user forgot they set and
 * that is silently still applied next week is a support call ("the board is missing cards").
 * A filter that survives a refresh and a tab-internal navigation, and dies with the tab, is
 * the behaviour every existing surface already chose.
 *
 * Keys carry a version suffix by convention (`board_filters_v1`) so a future change to
 * the stored SHAPE can bump the key rather than teach every coercion function to migrate.
 * Storing sort under its own key rather than folding it into the filter envelope keeps the
 * two independent, which is what makes adding sort to a surface that already persists filters
 * a no-migration change.
 *
 * URL-param persistence is a legitimate alternative for a surface where a filtered view is
 * worth sharing as a link — a sibling surface does exactly that and should keep doing it. It is not
 * abstracted here: one consumer is not a pattern, and pluggable persistence with a single
 * implementation is a knob with no second setting.
 */

/**
 * Read persisted state, funnelling every failure mode through the caller's `coerce`.
 *
 * `coerce` MUST accept `null` and `undefined` as well as arbitrary junk: an absent key, a
 * stored `null`, malformed JSON, a `sessionStorage` that throws (private mode), and a payload
 * of the wrong shape entirely all arrive there. It is the one place that decides what a
 * missing or corrupt value means, and it must never throw — this runs inside a `useState`
 * initialiser during first render, where an escaped exception white-screens the page rather
 * than degrading to an unfiltered view.
 */
export function loadPersistedState<T>(key: string, coerce: (raw: unknown) => T): T {
  try {
    const raw = sessionStorage.getItem(key);
    return coerce(raw === null ? null : JSON.parse(raw));
  } catch {
    return coerce(null);
  }
}

/** Write persisted state. Silently non-fatal — a full quota or a blocked store must never
 *  break the surface whose state it was trying to remember. */
export function savePersistedState(key: string, value: unknown): void {
  try {
    sessionStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* sessionStorage unavailable (private mode / quota) — non-fatal */
  }
}

/**
 * Add or remove `value` in a multi-select facet's selection, returning a new array.
 *
 * Every facet bar in the repo had re-derived this three-line toggle inline; it lives here so
 * a page wiring six facets writes `onToggle` six times without writing the toggle six times.
 */
export function toggleValue<T>(list: readonly T[], value: T): T[] {
  return list.includes(value) ? list.filter(v => v !== value) : [...list, value];
}
