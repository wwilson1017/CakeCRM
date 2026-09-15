// @vitest-environment jsdom
//
// The factory (#181): a `custom` facet def with the full lifecycle wired, two native date
// inputs, and a removable chip. What matters here is that it needs NO layer changes —
// `facets.ts`'s four switches and `CollectionView`'s render switch already handle `custom`.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { EMPTY_DATE_RANGE } from './dateRange';
import { dateRangeFacet } from './dateRangeFacet';
import { coerceSelection, defaultSelection, facetMatches, selectionActive } from './facets';
import type { DateRangeValue, FacetDef } from './types';

interface Row {
  id: number;
  day: string;
}

const facet = dateRangeFacet<Row>({
  key: 'closeDateRange',
  label: 'Close date range',
  getDay: r => r.day,
});

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function inputBy(label: string): HTMLInputElement {
  const el = document.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`);
  if (!el) throw new Error(`no input labelled "${label}"`);
  return el;
}

/** React overrides the value setter to track controlled inputs — write through the NATIVE
 *  setter or the change is invisible to React's value tracker and onChange never fires. */
function setInputValue(input: HTMLInputElement, value: string): void {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
  act(() => {
    setter?.set?.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

describe('shape', () => {
  it('is a custom facet, so the layer needs no new kind', () => {
    expect(facet.kind).toBe('custom');
    expect(facet.key).toBe('closeDateRange');
  });

  it('rests inactive', () => {
    expect(facet.defaultValue).toEqual(EMPTY_DATE_RANGE);
    expect(facet.isActive(facet.defaultValue)).toBe(false);
  });
});

describe('the layer drives it through the existing custom arm', () => {
  // The erasing-facet helpers take `FacetDef<unknown>` — the same shape the heterogeneous
  // facet array holds, and the reason `CustomFacetDef`'s lifecycle uses method syntax.
  const erased = facet as unknown as FacetDef<unknown>;
  const typed: FacetDef<Row> = facet;

  it('defaults, coerces and counts through facets.ts with no changes there', () => {
    expect(defaultSelection(erased)).toEqual(EMPTY_DATE_RANGE);
    expect(coerceSelection(erased, { from: '2026-01-01', to: 'junk' })).toEqual({
      from: '2026-01-01',
      to: null,
    });
    expect(selectionActive(erased, { from: '2026-01-01', to: null })).toBe(true);
    expect(selectionActive(erased, null)).toBe(false);
  });

  it('filters through facetMatches using getDay', () => {
    const value = { from: '2026-01-01', to: '2026-03-31' };
    expect(facetMatches(typed, { id: 1, day: '2026-02-02' }, value)).toBe(true);
    expect(facetMatches(typed, { id: 2, day: '2026-05-05' }, value)).toBe(false);
    expect(facetMatches(typed, { id: 3, day: '' }, value)).toBe(false);
  });
});

describe('renderControl', () => {
  function renderControl(value: DateRangeValue, setValue: (next: DateRangeValue) => void) {
    act(() => root.render(<>{facet.renderControl(value, setValue)}</>));
  }

  it('draws two native date inputs labelled from and to', () => {
    renderControl(EMPTY_DATE_RANGE, () => {});
    expect(inputBy('Close date range from').type).toBe('date');
    expect(inputBy('Close date range to').type).toBe('date');
    expect(document.body.textContent).toContain('Close date range');
  });

  it('shows the current bounds', () => {
    renderControl({ from: '2026-01-01', to: '2026-03-31' }, () => {});
    expect(inputBy('Close date range from').value).toBe('2026-01-01');
    expect(inputBy('Close date range to').value).toBe('2026-03-31');
  });

  it('reports a typed lower bound and leaves the other alone', () => {
    const setValue = vi.fn();
    renderControl({ from: null, to: '2026-03-31' }, setValue);
    setInputValue(inputBy('Close date range from'), '2026-01-01');
    expect(setValue).toHaveBeenCalledWith({ from: '2026-01-01', to: '2026-03-31' });
  });

  it('reports a cleared bound as null, not as an empty string', () => {
    const setValue = vi.fn();
    renderControl({ from: '2026-01-01', to: '2026-03-31' }, setValue);
    setInputValue(inputBy('Close date range to'), '');
    expect(setValue).toHaveBeenCalledWith({ from: '2026-01-01', to: null });
  });
});

describe('renderChip', () => {
  it('labels the active range and clears it on remove', () => {
    const clear = vi.fn();
    act(() =>
      root.render(<>{facet.renderChip({ from: '2026-01-01', to: '2026-03-31' }, clear)}</>),
    );
    expect(document.body.textContent).toContain('Close date range: 2026-01-01 – 2026-03-31');
    const remove = document.querySelector('button');
    act(() => (remove as HTMLElement).click());
    expect(clear).toHaveBeenCalledOnce();
  });
});
