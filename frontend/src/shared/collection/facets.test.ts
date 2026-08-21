import { describe, expect, it } from 'vitest';
import {
  activeFacetCount,
  applyFacets,
  coerceSelection,
  coerceSelections,
  defaultSelections,
  deriveFacetOptions,
  facetMatches,
  selectionActive,
} from './facets';
import type {
  BooleanFacetDef,
  CustomFacetDef,
  FacetDef,
  MultiFacetDef,
  RangeFacetDef,
  SingleFacetDef,
} from './types';

interface Row {
  id: number;
  supplier: string | null;
  tags: string[];
  priority: number;
  value: number | null;
  voided: boolean;
}

const rows: Row[] = [
  { id: 1, supplier: 'Acme', tags: ['a'], priority: 2, value: 100, voided: false },
  { id: 2, supplier: 'Zenith', tags: ['a', 'b'], priority: 0, value: null, voided: false },
  { id: 3, supplier: '  Acme ', tags: [], priority: 1, value: 50, voided: true },
  { id: 4, supplier: null, tags: ['b'], priority: 3, value: 200, voided: false },
];

const supplierFacet: MultiFacetDef<Row> = {
  key: 'supplier',
  label: 'Supplier',
  getValue: r => r.supplier,
};
const tagFacet: MultiFacetDef<Row> = { key: 'tags', label: 'Tags', getValue: r => r.tags };
const priorityFacet: BooleanFacetDef<Row> = {
  kind: 'boolean',
  key: 'priority',
  label: 'Priority',
  predicate: r => r.priority > 1,
};
const bandFacet: SingleFacetDef<Row> = {
  kind: 'single',
  key: 'band',
  label: 'Band',
  options: [
    { value: 'low', label: 'Low' },
    { value: 'high', label: 'High' },
  ],
  predicate: (r, v) => (v === 'high' ? r.priority >= 2 : r.priority < 2),
};
const valueFacet: RangeFacetDef<Row> = {
  kind: 'range',
  key: 'value',
  label: 'Value',
  getValue: r => r.value,
};
const mineFacet: CustomFacetDef<Row, { email: string | null }> = {
  kind: 'custom',
  key: 'mine',
  label: 'Mine',
  defaultValue: { email: null },
  isActive: v => v.email !== null,
  coerce: raw => {
    if (typeof raw === 'object' && raw !== null && 'email' in raw) {
      const email = (raw as { email: unknown }).email;
      return { email: typeof email === 'string' ? email : null };
    }
    return { email: null };
  },
  predicate: (r, v) => v.email !== null && r.supplier === 'Acme',
  renderControl: () => null,
  renderChip: () => null,
};

const allFacets: FacetDef<Row>[] = [
  supplierFacet,
  tagFacet,
  priorityFacet,
  bandFacet,
  valueFacet,
  mineFacet,
];

describe('coercion is total per kind', () => {
  it('multi drops non-scalars and non-arrays', () => {
    expect(coerceSelection(supplierFacet as FacetDef<unknown>, ['Acme', {}, 3, null])).toEqual([
      'Acme',
      3,
    ]);
    expect(coerceSelection(supplierFacet as FacetDef<unknown>, 'Acme')).toEqual([]);
  });

  it('single accepts scalars only', () => {
    expect(coerceSelection(bandFacet as FacetDef<unknown>, 'high')).toBe('high');
    expect(coerceSelection(bandFacet as FacetDef<unknown>, { v: 1 })).toBeNull();
  });

  it('boolean is true only on literal true', () => {
    expect(coerceSelection(priorityFacet as FacetDef<unknown>, 'true')).toBe(false);
    expect(coerceSelection(priorityFacet as FacetDef<unknown>, true)).toBe(true);
  });

  it('range keeps only finite numbers per bound', () => {
    expect(coerceSelection(valueFacet as FacetDef<unknown>, { min: 5, max: 'x' })).toEqual({
      min: 5,
      max: null,
    });
    expect(coerceSelection(valueFacet as FacetDef<unknown>, { min: NaN, max: Infinity })).toEqual({
      min: null,
      max: null,
    });
    expect(coerceSelection(valueFacet as FacetDef<unknown>, null)).toEqual({ min: null, max: null });
  });

  it('custom rides its own coerce, round-tripping junk to its default', () => {
    expect(coerceSelection(mineFacet as FacetDef<unknown>, { email: 'w@t.com' })).toEqual({
      email: 'w@t.com',
    });
    expect(coerceSelection(mineFacet as FacetDef<unknown>, 42)).toEqual({ email: null });
  });

  it('a THROWING custom coerce falls back to the default instead of white-screening', () => {
    const hostile: CustomFacetDef<Row, string> = {
      kind: 'custom',
      key: 'hostile',
      label: 'Hostile',
      defaultValue: 'safe',
      coerce: () => {
        throw new Error('bad coerce');
      },
      isActive: v => v !== 'safe',
      predicate: () => true,
      renderControl: () => null,
      renderChip: () => null,
    };
    expect(coerceSelection(hostile as FacetDef<unknown>, 'anything')).toBe('safe');
  });

  it('coerceSelections tolerates a non-object envelope', () => {
    const out = coerceSelections(allFacets, 'junk');
    expect(out.supplier).toEqual([]);
    expect(out.mine).toEqual({ email: null });
  });
});

describe('selectionActive / activeFacetCount', () => {
  it('reports each kind by its own inactive shape', () => {
    const defaults = defaultSelections(allFacets);
    for (const def of allFacets) {
      expect(selectionActive(def as FacetDef<unknown>, defaults[def.key])).toBe(false);
    }
    expect(selectionActive(supplierFacet as FacetDef<unknown>, ['Acme'])).toBe(true);
    expect(selectionActive(valueFacet as FacetDef<unknown>, { min: 1, max: null })).toBe(true);
    expect(selectionActive(mineFacet as FacetDef<unknown>, { email: 'w@t.com' })).toBe(true);
  });

  it('counts the voided tri-state when set; never counts the query', () => {
    const defaults = defaultSelections(allFacets);
    expect(activeFacetCount(allFacets, defaults, null)).toBe(0);
    expect(activeFacetCount(allFacets, defaults, 'hide')).toBe(1);
    expect(activeFacetCount(allFacets, { ...defaults, priority: true }, 'only')).toBe(2);
  });
});

describe('applyFacets', () => {
  const defaults = defaultSelections(allFacets);

  it('ANDs across facets and ORs within a multi', () => {
    const out = applyFacets(
      rows,
      allFacets,
      { ...defaults, tags: ['a', 'b'], priority: true },
      null,
    );
    // tags a|b → rows 1,2,4; priority>1 → rows 1,4.
    expect(out.map(r => r.id)).toEqual([1, 4]);
  });

  it('runs the voided gate FIRST — hide beats any facet match', () => {
    // Row 3 is the only voided row and (after trimming) the only OTHER 'Acme' — selecting the
    // derived 'Acme' option under 'hide' must yield row 1 only, never resurrect row 3.
    const out = applyFacets(rows, allFacets, { ...defaults, supplier: ['Acme'] }, 'hide', r => r.voided);
    expect(out.map(r => r.id)).toEqual([1]);
    const only = applyFacets(rows, allFacets, defaults, 'only', r => r.voided);
    expect(only.map(r => r.id)).toEqual([3]);
  });

  it('range bounds are inclusive and a null value never matches an active range', () => {
    const out = applyFacets(rows, allFacets, { ...defaults, value: { min: 50, max: 100 } }, null);
    expect(out.map(r => r.id)).toEqual([1, 3]);
  });

  it('single predicate receives the selected value', () => {
    expect(facetMatches(bandFacet, rows[0], 'high')).toBe(true);
    expect(facetMatches(bandFacet, rows[1], 'high')).toBe(false);
  });
});

describe('deriveFacetOptions', () => {
  it('derives distinct trimmed label-sorted options from array and scalar getters', () => {
    // 'Acme' and '  Acme ' dedupe to ONE option; null suppliers are dropped.
    expect(deriveFacetOptions(rows, supplierFacet).map(o => o.label)).toEqual(['Acme', 'Zenith']);
    expect(deriveFacetOptions(rows, tagFacet).map(o => o.value)).toEqual(['a', 'b']);
  });

  it('explicit options win over derivation', () => {
    const withOptions: MultiFacetDef<Row> = {
      ...supplierFacet,
      options: [{ value: 'X', label: 'X' }],
    };
    expect(deriveFacetOptions(rows, withOptions)).toEqual([{ value: 'X', label: 'X' }]);
  });
});
