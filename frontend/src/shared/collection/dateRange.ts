/**
 * Pure date-range filtering (issue #181) — the value shape, its never-throwing coercion,
 * the predicate and the chip label. Kept out of the `.tsx` factory so the rules are
 * unit-testable without a DOM.
 *
 * Bounds are viewer-LOCAL calendar days as `YYYY-MM-DD`, the same representation
 * `crm/pipelineFilters.ts` uses everywhere and for the same reason: a `toISOString()` day is
 * already tomorrow for a US-evening user. Days in that format compare lexicographically in
 * calendar order, so the predicate is two string comparisons and needs no clock at all — a
 * date range is absolute, unlike the relative presets it sits beside.
 */
import type { DateRangeValue } from './types';

/** Shape only — `2026-02-31` passes this and is rejected by the calendar check below. */
export const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;

export const EMPTY_DATE_RANGE: DateRangeValue = { from: null, to: null };

function dayOrNull(v: unknown): string | null {
  if (typeof v !== 'string' || !DAY_RE.test(v)) return null;
  const [y, m, d] = v.split('-').map(Number);
  // A real calendar day, not merely a well-shaped string: JS normalises Feb 31 to Mar 3, so a
  // round-trip through the LOCAL constructor that comes back unchanged is the check. A native
  // date input cannot emit an impossible day, but a hand-edited saved-view payload can, and it
  // would otherwise sit in the bar as an active filter nothing can match.
  const probe = new Date(y, m - 1, d);
  return probe.getFullYear() === y && probe.getMonth() === m - 1 && probe.getDate() === d
    ? v
    : null;
}

/** Total: null/undefined/junk and each bound independently coerce to "no bound". Never throws
 *  — `CustomFacetDef.coerce` runs inside a `useState` initialiser. */
export function coerceDateRange(raw: unknown): DateRangeValue {
  const r = typeof raw === 'object' && raw !== null && !Array.isArray(raw)
    ? (raw as Record<string, unknown>)
    : {};
  return { from: dayOrNull(r.from), to: dayOrNull(r.to) };
}

export function dateRangeActive(v: DateRangeValue): boolean {
  return v.from !== null || v.to !== null;
}

/**
 * Inclusive on both ends. An item with no day ('') never matches an ACTIVE range — the same
 * choice the numeric `range` facet makes for a null value, and the reason the presets keep a
 * separate "No date" bucket. An inverted range (from > to) matches nothing rather than being
 * silently swapped: the chip shows what was typed, so an empty board is legible.
 */
export function dayInRange(day: string, v: DateRangeValue): boolean {
  if (!day) return false;
  if (v.from !== null && day < v.from) return false;
  if (v.to !== null && day > v.to) return false;
  return true;
}

/** Mirrors `rangeChipLabel`'s three shapes for numbers. ISO days render verbatim — unambiguous
 *  across locales, and already the format the input shows. */
export function dateRangeChipLabel(label: string, v: DateRangeValue): string {
  if (v.from !== null && v.to !== null) return `${label}: ${v.from} – ${v.to}`;
  if (v.from !== null) return `${label}: ≥ ${v.from}`;
  return `${label}: ≤ ${v.to}`;
}
