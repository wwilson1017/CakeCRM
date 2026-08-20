// @vitest-environment jsdom
//
// The list adapter: the SortState bridge ({field,dir} ⇄ {key,dir}, null ⇒
// resting), the sortValue overlay (headers order by the SAME getter the hook sorts by), and
// the selection column (checkbox toggles never double as row-opens).
import { StrictMode, act, useEffect, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import useCollectionState from '../useCollectionState';
import CollectionListView from './CollectionListView';
import type { CollectionConfig, CollectionSelectionProps, CollectionState } from '../types';

interface Row {
  id: number;
  name: string;
  voided: boolean;
}

const rows: Row[] = [
  { id: 1, name: 'beta', voided: false },
  { id: 2, name: 'alpha', voided: false },
  { id: 3, name: 'gamma', voided: true },
];

function makeConfig(key: string): CollectionConfig<Row> {
  return {
    storage: { key, version: 1 },
    defaultView: 'list',
    getItemId: r => r.id,
    searchText: r => [r.name],
    sort: {
      fields: [
        { value: 'manual', label: 'Board order', arrayOrder: true },
        { value: 'name', label: 'Name', get: r => r.name },
      ],
    },
    list: { columns: [{ key: 'name', header: 'Name', render: r => r.name }] },
    getVoided: r => r.voided,
  };
}

let container: HTMLDivElement;
let root: Root;
const latest: { current: CollectionState<Row> | null } = { current: null };
const onSelect = vi.fn();

function Page({
  config,
  withSelection = false,
}: {
  config: CollectionConfig<Row>;
  withSelection?: boolean;
}) {
  const state = useCollectionState(config, rows);
  const [selected, setSelected] = useState<ReadonlySet<string | number>>(new Set());
  useEffect(() => {
    latest.current = state;
  });
  const selection: CollectionSelectionProps | undefined = withSelection
    ? { selectedIds: selected, onChange: setSelected, renderBulkBar: () => null }
    : undefined;
  return (
    <CollectionListView config={config} state={state} selection={selection} onSelect={onSelect} />
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

const rowTexts = () =>
  [...document.querySelectorAll('tbody tr')].map(tr =>
    (tr.textContent ?? '').replace(/\s+/g, ' ').trim(),
  );
const headerButton = () => {
  const el = [...document.querySelectorAll('th button')].find(b =>
    b.textContent?.includes('Name'),
  );
  if (!el) throw new Error('no Name header button');
  return el as HTMLElement;
};

beforeEach(() => {
  sessionStorage.clear();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  latest.current = null;
  onSelect.mockClear();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe('the SortState bridge', () => {
  it('header clicks cycle asc → desc → resting, mapped through the layer sort state', () => {
    renderPage({ config: makeConfig('bridge') });
    expect(state().sort).toEqual({ field: 'manual', dir: 'asc' });
    expect(rowTexts()).toEqual(['beta', 'alpha', 'gamma']); // manual = array order

    act(() => headerButton().click());
    expect(state().sort).toEqual({ field: 'name', dir: 'asc' });
    expect(rowTexts()).toEqual(['alpha', 'beta', 'gamma']);
    expect(document.querySelector('th[aria-sort="ascending"]')).toBeTruthy();

    act(() => headerButton().click());
    expect(state().sort).toEqual({ field: 'name', dir: 'desc' });
    expect(rowTexts()).toEqual(['gamma', 'beta', 'alpha']);

    // Third click: nextSortState yields null ("natural") — bridged back to the RESTING sort.
    act(() => headerButton().click());
    expect(state().sort).toEqual({ field: 'manual', dir: 'asc' });
    expect(rowTexts()).toEqual(['beta', 'alpha', 'gamma']);
    expect(document.querySelector('th[aria-sort]')).toBeNull();
  });

  it('a bar-driven sort shows on the matching header (two affordances, one value)', () => {
    renderPage({ config: makeConfig('barsort') });
    act(() => state().setSort({ field: 'name', dir: 'desc' }));
    expect(document.querySelector('th[aria-sort="descending"]')).toBeTruthy();
  });
});

describe('selection column', () => {
  it('renders only when selection is passed', () => {
    renderPage({ config: makeConfig('nosel') });
    expect(document.querySelector('input[aria-label="Select row"]')).toBeNull();
    renderPage({ config: makeConfig('nosel2'), withSelection: true });
    expect(document.querySelectorAll('input[aria-label="Select row"]')).toHaveLength(3);
  });

  it('a checkbox toggle never doubles as a row-open; a row click still opens', () => {
    renderPage({ config: makeConfig('selrow'), withSelection: true });
    const box = document.querySelector('input[aria-label="Select row"]') as HTMLElement;
    act(() => box.click());
    expect(onSelect).not.toHaveBeenCalled();
    const firstRow = document.querySelector('tbody tr') as HTMLElement;
    act(() => firstRow.click());
    expect(onSelect).toHaveBeenCalledWith(1);
  });
});

// A LIST-ONLY surface (CRM Contacts/Companies) has no `arrayOrder` field, so its resting
// sort is a real one — `name asc`. Collapsing that to `null` for the header meant every click on
// Name saw "unsorted" and asked for asc again (`nextSortState(null, key)` always returns asc), so
// descending was unreachable while the column rendered as unsorted. Only an `arrayOrder` resting
// field may read as "natural order".
describe('a resting sort that is a real field (list-only surface)', () => {
  function listOnlyConfig(key: string): CollectionConfig<Row> {
    return {
      storage: { key, version: 1 },
      defaultView: 'list',
      getItemId: r => r.id,
      searchText: r => [r.name],
      // No arrayOrder field at all — `fields[0]` is the resting sort.
      sort: { fields: [{ value: 'name', label: 'Name', get: r => r.name }] },
      list: { columns: [{ key: 'name', header: 'Name', render: r => r.name }] },
    };
  }

  it('reports the resting sort to the header instead of null, so desc is reachable', () => {
    renderPage({ config: listOnlyConfig('listonly') });
    // At rest the rows ARE sorted, and the header must say so.
    expect(rowTexts()).toEqual(['alpha', 'beta', 'gamma']);
    expect(document.querySelector('th[aria-sort="ascending"]')).not.toBeNull();

    // One click reaches descending — the whole point.
    act(() => headerButton().click());
    expect(state().sort).toEqual({ field: 'name', dir: 'desc' });
    expect(rowTexts()).toEqual(['gamma', 'beta', 'alpha']);

    // A further click returns to the resting order.
    act(() => headerButton().click());
    expect(state().sort).toEqual({ field: 'name', dir: 'asc' });
    expect(rowTexts()).toEqual(['alpha', 'beta', 'gamma']);
  });

  it('a board-backed surface still renders its arrayOrder resting sort as natural order', () => {
    // The behavior the null-collapse was written for is untouched.
    renderPage({ config: makeConfig('boardrest') });
    expect(document.querySelector('th[aria-sort]')).toBeNull();
  });
});

describe('voided rendering', () => {
  it('voided rows are struck through, never hidden', () => {
    renderPage({ config: makeConfig('void') });
    const struck = [...document.querySelectorAll('tbody tr')].filter(tr =>
      tr.className.includes('line-through'),
    );
    expect(struck).toHaveLength(1);
    expect(struck[0].textContent).toContain('gamma');
  });
});
