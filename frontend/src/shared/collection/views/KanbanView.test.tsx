// @vitest-environment jsdom
//
// The board composition: items reach the app's slots UNWRAPPED, per-column
// caps render "Show N more" wired to the hook's expandColumn (whose truncation rule the
// grouping helper mirrors — grouping.test.ts pins the arithmetic), and expanding releases
// both the extra cards and the truncated-column drag lock.
import { StrictMode, act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import useCollectionState from '../useCollectionState';
import KanbanView from './KanbanView';
import type { CollectionConfig, CollectionKanbanProps, CollectionState } from '../types';

interface Row {
  id: number;
  name: string;
  stage: string;
}

const rows: Row[] = [
  { id: 1, name: 'one', stage: 'a' },
  { id: 2, name: 'two', stage: 'a' },
  { id: 3, name: 'three', stage: 'a' },
  { id: 4, name: 'four', stage: 'b' },
];

function makeConfig(key: string): CollectionConfig<Row> {
  return {
    storage: { key, version: 1 },
    defaultView: 'kanban',
    getItemId: r => r.id,
    searchText: r => [r.name],
    kanban: { getColumnId: r => r.stage, columnCap: 2 },
  };
}

const kanbanProps: CollectionKanbanProps<Row, null> = {
  columns: [
    { id: 'a', data: null },
    { id: 'b', data: null },
  ],
  onMove: async () => {},
  renderColumn: (column, children) => (
    <div data-col={column.id} key={column.id}>
      {children}
    </div>
  ),
  renderCard: item => <span className="card">{item.name}</span>,
};

let container: HTMLDivElement;
let root: Root;
const latest: { current: CollectionState<Row> | null } = { current: null };

function Page({ config }: { config: CollectionConfig<Row> }) {
  const state = useCollectionState(config, rows);
  useEffect(() => {
    latest.current = state;
  });
  return <KanbanView config={config} state={state} kanban={kanbanProps} />;
}

const state = (): CollectionState<Row> => {
  if (!latest.current) throw new Error('page did not render');
  return latest.current;
};

const cardNames = (col: string) =>
  [...document.querySelectorAll(`[data-col="${col}"] .card`)].map(el => el.textContent);

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

describe('KanbanView', () => {
  it('caps a column, renders the app items unwrapped, and expands via the hook', () => {
    act(() => {
      root.render(
        <StrictMode>
          <Page config={makeConfig('cap')} />
        </StrictMode>,
      );
    });
    expect(cardNames('a')).toEqual(['one', 'two']);
    expect(cardNames('b')).toEqual(['four']);
    expect(state().truncatedColumns.has('a')).toBe(true);
    expect(state().dragLocked).toBe(true);

    const showMore = [...document.querySelectorAll('button')].find(
      b => b.textContent === 'Show 1 more',
    );
    expect(showMore).toBeTruthy();
    act(() => (showMore as HTMLElement).click());

    expect(cardNames('a')).toEqual(['one', 'two', 'three']);
    expect(state().truncatedColumns.size).toBe(0);
    expect(state().dragLocked).toBe(false);
    expect(
      [...document.querySelectorAll('button')].some(b => b.textContent === 'Show 1 more'),
    ).toBe(false);
  });
});
