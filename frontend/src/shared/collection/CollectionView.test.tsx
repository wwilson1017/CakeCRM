// @vitest-environment jsdom
//
// The shell: wiring assertions, not kit re-tests. SearchInput's debounce,
// ListView's sorting and the facet popover internals are pinned by their own suites — what
// this file proves is the TRANSLATION: clear-all clears facets AND the query, every facet
// kind surfaces in the bar, the switcher lists exactly the configured views, and the bulk
// bar receives ONLY the visible-selected intersection (select → filter → bulk can never
// touch a hidden record).
import { StrictMode, act, useEffect, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import useCollectionState from './useCollectionState';
import CollectionView from './CollectionView';
import type {
  CollectionConfig,
  CollectionLoadingProps,
  CollectionState,
} from './types';

interface Row {
  id: number;
  name: string;
  stage: string;
  priority: boolean;
  value: number | null;
  voided: boolean;
}

const rows: Row[] = [
  { id: 1, name: 'alpha', stage: 'New', priority: true, value: 10, voided: false },
  { id: 2, name: 'beta', stage: 'Won', priority: false, value: 250, voided: false },
  { id: 3, name: 'gamma', stage: 'New', priority: false, value: null, voided: true },
];

function makeConfig(key: string, overrides: Partial<CollectionConfig<Row>> = {}): CollectionConfig<Row> {
  return {
    storage: { key, version: 1 },
    defaultView: 'list',
    getItemId: r => r.id,
    searchText: r => [r.name],
    facets: [
      { kind: 'multi', key: 'stage', label: 'Stage', getValue: r => r.stage },
      { kind: 'boolean', key: 'priority', label: 'Priority', predicate: r => r.priority },
      { kind: 'range', key: 'value', label: 'Value', getValue: r => r.value },
    ],
    list: { columns: [{ key: 'name', header: 'Name', render: r => r.name }] },
    getVoided: r => r.voided,
    itemNoun: { singular: 'deal', plural: 'deals' },
    ...overrides,
  };
}

// Every other DOM test in the repo sets this (see `shared/listview/ListView.test.tsx`); without
// it React warns on every act() call and its flush guarantees are not the ones the assertions
// below rely on. This file had been missing it.
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;
// House hook-capture idiom: mutate a property inside an effect, never a module var in render.
const latest: { current: CollectionState<Row> | null } = { current: null };

function Page({
  config,
  data = rows,
  withSelection = false,
  loading,
  onSelect = () => {},
  noSelect = false,
}: {
  config: CollectionConfig<Row>;
  data?: readonly Row[];
  withSelection?: boolean;
  loading?: CollectionLoadingProps;
  onSelect?: (id: string | number | null) => void;
  /** A page that wires no `onSelect` at all — the surface with nothing to open (#148). */
  noSelect?: boolean;
}) {
  const state = useCollectionState(config, data);
  const [selected, setSelected] = useState<ReadonlySet<string | number>>(new Set());
  useEffect(() => {
    latest.current = state;
  });
  return (
    <CollectionView<Row>
      config={config}
      state={state}
      items={data}
      onSelect={noSelect ? undefined : onSelect}
      loading={loading}
      selection={
        withSelection
          ? {
              selectedIds: selected,
              onChange: next => setSelected(next),
              renderBulkBar: (visibleSelectedIds, count) => (
                <div data-testid="bulk">
                  {[...visibleSelectedIds].sort().join(',')}|{count}
                </div>
              ),
            }
          : undefined
      }
    />
  );
}

function renderPage(props: Parameters<typeof Page>[0]): void {
  act(() => {
    root.render(
      <StrictMode>
        <Page {...props} />
      </StrictMode>,
    );
  });
}

const state = (): CollectionState<Row> => {
  if (!latest.current) throw new Error('page did not render');
  return latest.current;
};

const buttons = () => [...document.querySelectorAll('button')];
const buttonByText = (text: string) => {
  const el = buttons().find(b => b.textContent?.trim() === text);
  if (!el) throw new Error(`no button "${text}"`);
  return el;
};
const click = (el: Element) => act(() => (el as HTMLElement).click());

/** React overrides the value setter to track controlled inputs — write through the NATIVE
 *  setter or the change is invisible to React's value tracker and onChange never fires. */
function setInputValue(input: HTMLInputElement, value: string): void {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
  act(() => {
    setter?.set?.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

beforeEach(() => {
  sessionStorage.clear();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  latest.current = null;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe('clear-all', () => {
  it('clears every facet kind AND the query (the bar names everything it represents)', () => {
    renderPage({ config: makeConfig('clear') });
    act(() => state().setQuery('alp'));
    act(() => state().setFacet('priority', true));
    act(() => state().setVoided('hide'));
    expect(state().visibleItems.map(r => r.id)).toEqual([1]);

    click(buttonByText('Clear filters'));
    expect(state().query).toBe('');
    expect(state().facetSelections.priority).toBe(false);
    expect(state().voided).toBeNull();
    expect(state().visibleItems).toHaveLength(3);
  });
});

describe('facet kinds in the bar', () => {
  it('boolean renders as a panel chip and, while active, a removable collapsed chip', () => {
    renderPage({ config: makeConfig('bool') });
    click(buttonByText('Filters'));
    click(buttonByText('Priority'));
    expect(state().facetSelections.priority).toBe(true);
    // Collapse the panel — the active chip row only renders while the panel is closed.
    click(buttons().find(b => b.textContent?.startsWith('Filters')) as Element);
    const remove = buttons().find(b => b.getAttribute('aria-label') === 'Remove filter Priority');
    expect(remove).toBeTruthy();
    click(remove as Element);
    expect(state().facetSelections.priority).toBe(false);
  });

  it('range inputs write coerced bounds; the voided tri-state rides the group row', () => {
    renderPage({ config: makeConfig('range') });
    click(buttonByText('Filters'));
    const min = document.querySelector('input[aria-label="Value minimum"]') as HTMLInputElement;
    setInputValue(min, '100');
    expect(state().facetSelections.value).toEqual({ min: 100, max: null });
    expect(state().visibleItems.map(r => r.id)).toEqual([2]);

    click(buttonByText('Hide voided'));
    expect(state().voided).toBe('hide');
  });
});

describe('view switcher', () => {
  it('lists exactly the configured views and drives setView', () => {
    renderPage({
      config: makeConfig('views', {
        cards: { getTitle: r => r.name },
      }),
    });
    expect(buttons().some(b => b.textContent === 'Board')).toBe(false);
    click(buttonByText('Cards'));
    expect(state().view).toBe('cards');
  });

  it('a single-view config renders no switcher', () => {
    renderPage({ config: makeConfig('oneview') });
    expect(document.querySelector('[role="group"][aria-label="View"]')).toBeNull();
  });
});

describe('selection', () => {
  it('the bulk bar receives ONLY the visible-selected intersection (select → filter → bulk)', () => {
    renderPage({ config: makeConfig('sel'), withSelection: true });
    const rowBoxes = [...document.querySelectorAll('input[aria-label="Select row"]')];
    expect(rowBoxes).toHaveLength(3);
    click(rowBoxes[0]); // id 1 (alpha)
    click(rowBoxes[1]); // id 2 (beta)
    expect(document.querySelector('[data-testid="bulk"]')?.textContent).toBe('1,2|2');

    // Filter alpha out — the selection SET still holds id 1, the bulk bar must not.
    act(() => state().setQuery('beta'));
    expect(state().visibleItems.map(r => r.id)).toEqual([2]);
    expect(document.querySelector('[data-testid="bulk"]')?.textContent).toBe('2|1');
  });

  it('select-all covers the VISIBLE (filtered) set', () => {
    renderPage({ config: makeConfig('selall'), withSelection: true });
    act(() => state().setVoided('hide'));
    const all = document.querySelector('input[aria-label="Select all visible"]') as HTMLInputElement;
    click(all);
    expect(document.querySelector('[data-testid="bulk"]')?.textContent).toBe('1,2|2');
  });
});

describe('toggles', () => {
  it('render as labelled checkboxes in the trailing row and write through setToggle', () => {
    renderPage({
      config: makeConfig('tog', { toggles: [{ key: 'showClosed', label: 'Show closed' }] }),
    });
    const box = [...document.querySelectorAll('input[type="checkbox"]')].find(
      el => el.parentElement?.textContent?.includes('Show closed'),
    ) as HTMLInputElement;
    expect(box.checked).toBe(true);
    click(box);
    expect(state().toggles.showClosed).toBe(false);
  });
});

describe('loading / empty', () => {
  const idle = { loading: false, error: null, itemsLoaded: 0, retry: () => {} };

  it('an in-flight assembly renders progress text with a count, never the views', () => {
    renderPage({
      config: makeConfig('load'),
      data: [],
      loading: { ...idle, loading: true, itemsLoaded: 240 },
    });
    expect(document.body.textContent).toContain('Loading deals');
    expect(document.body.textContent).toContain('240 loaded');
    expect(document.querySelector('table')).toBeNull();
  });

  it('an assembly error renders the message with a working Retry', () => {
    const retry = vi.fn();
    renderPage({
      config: makeConfig('loaderr'),
      data: [],
      loading: { ...idle, error: 'Loading timed out.', retry },
    });
    expect(document.body.textContent).toContain('Loading timed out.');
    click(buttonByText('Retry'));
    expect(retry).toHaveBeenCalled();
  });

  it('an empty canonical set renders the EmptyState instead of the bar', () => {
    renderPage({
      config: makeConfig('empty', { emptyState: { message: 'No deals yet — create one.' } }),
      data: [],
    });
    expect(document.body.textContent).toContain('No deals yet — create one.');
    expect(document.querySelector('input[type="text"]')).toBeNull();
  });

  it('a deep-link detail still opens over an EMPTY set via loadById', async () => {
    // A failed/empty list load must not kill a deep link: the detail resolves the selected id
    // through loadById even though `items` is empty and the surface shows its EmptyState.
    function DeepLinkPage() {
      const config = makeConfig('empty_detail', {
        detail: {
          getTitle: r => r.name,
          loadById: async id => ({
            id: Number(id), name: `fetched ${id}`, stage: 'New',
            priority: false, value: null, voided: false,
          }),
        },
      });
      const state = useCollectionState(config, []);
      return (
        <CollectionView<Row>
          config={config}
          state={state}
          items={[]}
          selectedId={7}
          onSelect={() => {}}
          detail={{ render: row => <div data-testid="detail-body">{row.name}</div> }}
        />
      );
    }
    await act(async () => {
      root.render(<StrictMode><DeepLinkPage /></StrictMode>);
    });
    // loadById resolves on a microtask; flush it.
    await act(async () => { await Promise.resolve(); });
    // The EmptyState is shown (default noun message) AND the detail resolved the deep-linked id.
    expect(document.body.textContent).toContain('No deals yet.');
    expect(document.querySelector('[data-testid="detail-body"]')?.textContent).toBe('fetched 7');
  });

  // The twin of the case above, for the OTHER two branches that return early. An earlier fix
  // covered `items.length === 0`; the loading/error branch still returned before
  // `detailBlock` existed, so a deep link into a surface assembling through `usePageAssembly`
  // — which reports `loading` with `items: null` for the WHOLE sweep and never a partial set —
  // showed a progress bar instead of the record the link named.
  for (const [name, loadingProps] of [
    ['an IN-FLIGHT assembly', { ...idle, loading: true, itemsLoaded: 120 }],
    ['a FAILED assembly', { ...idle, error: 'Loading timed out.' }],
  ] as const) {
    it(`a deep-link detail opens during ${name} via loadById`, async () => {
      function DeepLinkPage() {
        const config = makeConfig(`assembling_${name.length}`, {
          detail: {
            getTitle: r => r.name,
            loadById: async id => ({
              id: Number(id), name: `fetched ${id}`, stage: 'New',
              priority: false, value: null, voided: false,
            }),
          },
        });
        const state = useCollectionState(config, []);
        return (
          <CollectionView<Row>
            config={config}
            state={state}
            items={[]}
            selectedId={7}
            onSelect={() => {}}
            loading={loadingProps}
            detail={{ render: row => <div data-testid="detail-body">{row.name}</div> }}
          />
        );
      }
      await act(async () => {
        root.render(<StrictMode><DeepLinkPage /></StrictMode>);
      });
      await act(async () => { await Promise.resolve(); });
      // The status surface still renders — the detail is ADDITIVE, it does not replace it.
      expect(document.body.textContent).toContain(
        loadingProps.error !== null ? 'Loading timed out.' : 'Loading deals',
      );
      expect(document.querySelector('[data-testid="detail-body"]')?.textContent).toBe('fetched 7');
      // The views stay suppressed while the set is incomplete.
      expect(document.querySelector('table')).toBeNull();
    });
  }

  it('the range chip formats its bounds through RangeFacetDef.format', () => {
    const config = makeConfig('rangefmt', {
      facets: [
        {
          kind: 'range', key: 'value', label: 'Value',
          getValue: r => r.value,
          format: n => `$${n.toLocaleString()}`,
        },
      ],
    });
    renderPage({ config });
    act(() => state().setFacet('value', { min: 1000, max: 50000 }));
    // Formatted in the collapsed CHIP (the chip row renders only while the panel is closed) …
    expect(document.body.textContent).toContain('Value: $1,000–$50,000');
    // … while the panel's own inputs stay raw numbers — formatting a value mid-keystroke would
    // fight typing, which is why `format` is chip-only.
    // "Filters (1)" once the range is active — match by prefix, as the facet suites above do.
    click(buttons().find(b => b.textContent?.startsWith('Filters')) as Element);
    const numberInputs = [...document.querySelectorAll('input[type="number"]')] as HTMLInputElement[];
    expect(numberInputs.map(i => i.value)).toEqual(['1000', '50000']);
  });
});

describe('list rows (#148)', () => {
  // The a11y fix lands in `shared/listview`, but the ADAPTER decision lands here: a page that
  // wires no `onSelect` must get rows with no handler at all, rather than a closure that
  // swallows the call — otherwise every such row becomes a tab stop that does nothing, which
  // is worse than no tab stop.
  it('rows are keyboard-focusable and Enter-openable exactly when the page wires onSelect', () => {
    const spy = vi.fn();
    renderPage({ config: makeConfig('a11y_rows_on'), onSelect: spy });
    const row = document.querySelector('tbody tr') as HTMLElement;
    expect(row.getAttribute('tabindex')).toBe('0');
    act(() => { row.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })); });
    expect(spy).toHaveBeenCalledWith(1);
  });

  it('rows are inert when the page wires no onSelect', () => {
    renderPage({ config: makeConfig('a11y_rows_off'), noSelect: true });
    const row = document.querySelector('tbody tr') as HTMLElement;
    expect(row.getAttribute('tabindex')).toBeNull();
    expect(row.className).not.toContain('cursor-pointer');
  });
});
