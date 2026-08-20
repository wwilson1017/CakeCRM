/**
 * Pure facet machinery for the collection layer.
 *
 * Framework-free on purpose: everything here runs inside `useState` initialisers and
 * `useMemo`s, and all of it is node-env unit-testable. Evaluation semantics follow the house
 * pattern (`docs/solutions/architecture-patterns/client-side-facet-filtering.md`): AND across
 * facets, OR within a multi facet, and the voided gate runs FIRST so "hide voided" can never
 * be overridden by a facet match.
 *
 * Coercion is total by contract — `coerceSelections` accepts null/undefined/junk and never
 * throws (`shared/search/persist.ts`'s `loadPersistedState` funnels every failure mode
 * through it during first render, where a throw white-screens the page).
 */
import type {
  CustomFacetDef,
  FacetDef,
  FacetSelections,
  MultiFacetDef,
  RangeValue,
  VoidedFilter,
} from './types';
import type { FacetOption } from '../search';

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

function isFacetScalar(v: unknown): v is string | number {
  return typeof v === 'string' || typeof v === 'number';
}

/** The inactive selection for one facet definition. */
export function defaultSelection(def: FacetDef<unknown>): unknown {
  switch (def.kind) {
    case 'single':
      return null;
    case 'boolean':
      return false;
    case 'range':
      return { min: null, max: null } satisfies RangeValue;
    case 'custom':
      return def.defaultValue;
    case 'multi':
    default:
      return [] as (string | number)[];
  }
}

export function defaultSelections<T>(facets: readonly FacetDef<T>[]): FacetSelections {
  const out: FacetSelections = {};
  for (const def of facets) out[def.key] = defaultSelection(def as FacetDef<unknown>);
  return out;
}

/** Coerce one persisted value for `def`; anything malformed falls back to inactive. */
export function coerceSelection(def: FacetDef<unknown>, raw: unknown): unknown {
  switch (def.kind) {
    case 'single':
      return isFacetScalar(raw) ? raw : null;
    case 'boolean':
      return raw === true;
    case 'range': {
      if (!isRecord(raw)) return { min: null, max: null } satisfies RangeValue;
      const min = typeof raw.min === 'number' && Number.isFinite(raw.min) ? raw.min : null;
      const max = typeof raw.max === 'number' && Number.isFinite(raw.max) ? raw.max : null;
      return { min, max } satisfies RangeValue;
    }
    case 'custom':
      // The def owns its lifecycle; a throwing coerce is a consumer bug the tests for that
      // config should catch, but the layer still refuses to white-screen on it.
      try {
        return def.coerce(raw);
      } catch {
        return def.defaultValue;
      }
    case 'multi':
    default:
      return Array.isArray(raw) ? raw.filter(isFacetScalar) : [];
  }
}

export function coerceSelections<T>(
  facets: readonly FacetDef<T>[],
  raw: unknown,
): FacetSelections {
  const source = isRecord(raw) ? raw : {};
  const out: FacetSelections = {};
  for (const def of facets) {
    out[def.key] = coerceSelection(def as FacetDef<unknown>, source[def.key]);
  }
  return out;
}

/** Is this facet currently constraining results? */
export function selectionActive(def: FacetDef<unknown>, value: unknown): boolean {
  switch (def.kind) {
    case 'single':
      return value !== null && value !== undefined;
    case 'boolean':
      return value === true;
    case 'range': {
      const r = value as RangeValue;
      return isRecord(r) && (r.min !== null || r.max !== null);
    }
    case 'custom':
      try {
        return def.isActive(def.coerce(value));
      } catch {
        return false;
      }
    case 'multi':
    default:
      return Array.isArray(value) && value.length > 0;
  }
}

/** Facets constraining results right now. The voided tri-state counts when set (the
 *  the blueprint convention); the query deliberately does not ("as in CRM"). */
export function activeFacetCount<T>(
  facets: readonly FacetDef<T>[],
  selections: FacetSelections,
  voided: VoidedFilter,
): number {
  let n = voided === null ? 0 : 1;
  for (const def of facets) {
    if (selectionActive(def as FacetDef<unknown>, selections[def.key])) n += 1;
  }
  return n;
}

function facetKey(v: string | number): string | number {
  // Trim string values on BOTH sides so a derived option ('Acme') matches a row whose raw
  // value carries whitespace ('  Acme ') — the same normalization deriveFacetOptions dedups
  // by, and the blueprint precedent (`(field ?? '').trim()` comparisons).
  return typeof v === 'string' ? v.trim() : v;
}

function matchesMulti<T>(def: MultiFacetDef<T>, item: T, selected: (string | number)[]): boolean {
  const value = def.getValue(item);
  if (value === null || value === undefined) return false;
  const keys = selected.map(facetKey);
  if (Array.isArray(value)) return value.some(v => keys.includes(facetKey(v)));
  return keys.includes(facetKey(value));
}

/** One item against one ACTIVE facet. Callers skip inactive facets. */
export function facetMatches<T>(def: FacetDef<T>, item: T, value: unknown): boolean {
  switch (def.kind) {
    case 'single':
      return def.predicate(item, value as string | number);
    case 'boolean':
      return def.predicate(item);
    case 'range': {
      const r = value as RangeValue;
      const v = def.getValue(item);
      if (v === null || v === undefined) return false;
      if (r.min !== null && v < r.min) return false;
      if (r.max !== null && v > r.max) return false;
      return true;
    }
    case 'custom':
      return (def as CustomFacetDef<T, unknown>).predicate(item, value);
    case 'multi':
    default:
      return matchesMulti(def as MultiFacetDef<T>, item, value as (string | number)[]);
  }
}

/**
 * Apply the voided gate, then every active facet (AND across, OR within multi). The search
 * query is applied separately by the caller (it needs the prebuilt docs).
 */
export function applyFacets<T>(
  items: readonly T[],
  facets: readonly FacetDef<T>[],
  selections: FacetSelections,
  voided: VoidedFilter,
  getVoided?: (item: T) => boolean,
): T[] {
  const active = facets.filter(def =>
    selectionActive(def as FacetDef<unknown>, selections[def.key]),
  );
  return items.filter(item => {
    if (getVoided) {
      const isVoided = getVoided(item);
      if (voided === 'hide' && isVoided) return false;
      if (voided === 'only' && !isVoided) return false;
    }
    for (const def of active) {
      if (!facetMatches(def, item, selections[def.key])) return false;
    }
    return true;
  });
}

/**
 * Distinct options for a multi facet from the LOADED rows (free text, not an enum) — trimmed,
 * empties dropped, label-sorted. `localeCompare` is fine HERE: these are human labels for a
 * picker, not the row ordering `shared/search/sort.ts` bans it from (the blueprint is about ISO
 * timestamps in row sorts).
 */
export function deriveFacetOptions<T>(items: readonly T[], def: MultiFacetDef<T>): FacetOption[] {
  if (def.options) return def.options;
  const seen = new Map<string | number, string>();
  for (const item of items) {
    const value = def.getValue(item);
    const values = Array.isArray(value) ? value : [value];
    for (const v of values) {
      if (v === null || v === undefined) continue;
      const label = typeof v === 'string' ? v.trim() : String(v);
      if (label === '') continue;
      const key = typeof v === 'string' ? v.trim() : v;
      if (!seen.has(key)) seen.set(key, label);
    }
  }
  return [...seen.entries()]
    .sort((a, b) => a[1].localeCompare(b[1]))
    .map(([value, label]) => ({ value, label }));
}
