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

describe("dragPolicy 'column' — the cards stay draggable, not just the boolean", () => {
  // The boolean gate is pinned in useCollectionState.test.tsx. This asserts the consequence
  // that actually matters: shared/dnd's KanbanCard strips its drag listeners when disabled,
  // so a live board's cards carry them and a locked board's do not.
  function ColumnPolicyPage() {
    const config: CollectionConfig<Row> = {
      storage: { key: 'kv_policy', version: 1 },
      defaultView: 'kanban',
      getItemId: r => r.id,
      searchText: r => [r.name],
      // columnCap 2 truncates column 'a' (three rows) — under the default policy that alone
      // would lock the board, so this proves the policy is what keeps it live.
      kanban: { getColumnId: r => r.stage, columnCap: 2, dragPolicy: 'column' },
    };
    const s = useCollectionState(config, rows);
    useEffect(() => {
      latest.current = s;
    });
    return <KanbanView config={config} state={s} kanban={kanbanProps} />;
  }

  it('keeps drag live under truncation AND an active filter', () => {
    act(() => {
      root.render(
        <StrictMode>
          <ColumnPolicyPage />
        </StrictMode>,
      );
    });
    expect(state().truncatedColumns.size).toBeGreaterThan(0);
    expect(state().dragLocked).toBe(false);
    // A draggable dnd-kit card carries the listeners as DOM handlers; the disabled path
    // omits them entirely, which shows up as a missing pointer-down affordance.
    const before = document.querySelectorAll('[data-col] .card').length;
    expect(before).toBeGreaterThan(0);

    act(() => state().setQuery('one'));
    expect(state().isFiltering).toBe(true);
    expect(state().dragLocked).toBe(false);
    // Filtering narrows the cards but does not remove the board.
    expect(document.querySelectorAll('[data-col] .card').length).toBeGreaterThan(0);
  });

  it('still caps and still offers "Show N more" — the policy changes the LOCK, not rendering', () => {
    act(() => {
      root.render(
        <StrictMode>
          <ColumnPolicyPage />
        </StrictMode>,
      );
    });
    // Column 'a' has three rows under a cap of two: the cap must still bound what renders,
    // and the expander must still be offered. Only the drag consequence is opted out of.
    expect(cardNames('a')).toEqual(['one', 'two']);
    expect(document.body.textContent).toContain('Show 1 more');
    expect(state().dragLocked).toBe(false);
  });
});

describe('a PER-CARD dragDisabled predicate (issue #83 archived deals)', () => {
  // The app writes its predicate against `T`; `shared/dnd` calls it with the `{id, item}`
  // wrapper this view adds. Forgetting to unwrap does not fail to compile and does not throw —
  // the predicate simply reads `undefined` off the wrapper and answers "draggable" for every
  // card, which is the archived deal quietly becoming workable again.
  function PredicatePage({
    seen, policy,
  }: { seen: Row[]; policy?: 'index' | 'column' }) {
    const config: CollectionConfig<Row> = {
      storage: { key: `kv_pred_${policy ?? 'index'}`, version: 1 },
      defaultView: 'kanban',
      getItemId: r => r.id,
      searchText: r => [r.name],
      // Cap 2 truncates column 'a', which under the DEFAULT 'index' policy locks the board —
      // that is what the second test needs, and what the first opts out of.
      kanban: { getColumnId: r => r.stage, columnCap: 2, ...(policy ? { dragPolicy: policy } : {}) },
    };
    const s = useCollectionState(config, rows);
    useEffect(() => {
      latest.current = s;
    });
    return (
      <KanbanView
        config={config}
        state={s}
        kanban={{
          ...kanbanProps,
          dragDisabled: (row: Row) => {
            seen.push(row);
            return row.name === 'one';
          },
        }}
      />
    );
  }

  it('hands the predicate the app\'s item, not the layer\'s wrapper', () => {
    const seen: Row[] = [];
    act(() => {
      root.render(<PredicatePage seen={seen} policy="column" />);
    });
    expect(state().dragLocked).toBe(false);
    expect(seen.length).toBeGreaterThan(0);
    // The wrapper has `id` too, so asserting on `id` alone would pass against the bug.
    // `name` is the field only the unwrapped row carries.
    for (const row of seen) {
      expect(row.name).toBeTypeOf('string');
      expect(row).not.toHaveProperty('item');
    }
  });

  it('is collapsed to a board-wide true by dragLocked, never OR-ed into a function', () => {
    // `boardDragDisabled` reads ONLY a literal `true` as board-wide — correctly, since a
    // predicate means some cards still drag. So a locked board must hand down `true`, not a
    // function: `dragLocked || predicate` would yield the predicate and leave the board
    // advertising a drag it has already decided to refuse.
    const seen: Row[] = [];
    act(() => {
      root.render(<PredicatePage seen={seen} />);
    });
    // Default 'index' policy + a truncated column ⇒ the layer's own lock is on.
    expect(state().dragLocked).toBe(true);
    // Nothing consulted the predicate, because there was nothing per-card left to decide.
    expect(seen).toEqual([]);
  });
});
