// @vitest-environment jsdom
/**
 * The #112 contract: a board that declares `KanbanViewConfig.dragPolicy: 'column'` cannot be
 * handed a drop index.
 *
 * `useCollectionState` sets `dragLocked = false` unconditionally under that policy, because all
 * three ambiguities the gate folds — a filtered subset, a non-array sort, a truncated column —
 * are about the drop INDEX, and a board that assigns only a column has none. That is sound only
 * if the consumer really discards `newIndex`, which #74 could state in a docstring and not
 * check. Here it is checked: the policy is the config's second type parameter, so
 * `CollectionViewProps` infers it and `onMove` receives an event with no `newIndex` member.
 *
 * **Most of this file is assertions the COMPILER runs.** The `Expect<…>` aliases below fail
 * `tsc -b` — which `npm run build` runs and CI gates on — the moment a shape regresses, and
 * they are exported so `noUnusedLocals` does not quietly delete the guard. `@ts-expect-error`
 * is deliberately not used: the repo bans suppressions, and a conditional-type assertion
 * expresses the same thing without one.
 *
 * Every pairing is asserted in BOTH directions — present under `'index'`, absent under
 * `'column'` — because a one-directional assertion over a helper that silently returned the
 * same answer for everything would be vacuously green, and a type assertion has no runtime to
 * catch that for you.
 *
 * The two probe components are the other half, and they are the reason this file is `.tsx`:
 * naming `CollectionViewProps<…, 'column'>` directly proves the type, not that
 * `CollectionView` INFERS it from the config it is given, which is the whole mechanism. They
 * are real call sites, so the compiler checks the inference, and the runtime test below
 * renders them so they cannot rot into unreachable type fodder.
 */
import { StrictMode, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import useCollectionState from './useCollectionState';
import CollectionView from './CollectionView';
import type {
  CollectionConfig,
  CollectionMoveEvent,
  CollectionState,
  CollectionViewProps,
  DragPolicy,
} from './types';

interface Row {
  id: number;
  name: string;
  stage: string;
}

// Two rows share column 'a', so a `columnCap` of 1 truncates it — which is what makes the
// two probes below a real pair: identical board shape, opposite `dragLocked`.
const rows: Row[] = [
  { id: 1, name: 'one', stage: 'a' },
  { id: 2, name: 'two', stage: 'a' },
  { id: 3, name: 'three', stage: 'b' },
];

// ---------------------------------------------------------------------------------------------
// Compile-time assertions. `Expect<T>` accepts only `true`, so a `false` is a build error.

type Expect<T extends true> = T;
type HasNewIndex<E> = 'newIndex' extends keyof E ? true : false;
/** Plain assignability, uncurried into a boolean so it can be asserted in either direction. */
type IsAssignable<A, B> = A extends B ? true : false;

type ColumnEvent = CollectionMoveEvent<Row, 'column'>;
type IndexEvent = CollectionMoveEvent<Row, 'index'>;

/** THE contract: a `'column'` drop has no index to persist… */
export type _ColumnEventHasNoIndex = Expect<HasNewIndex<ColumnEvent> extends false ? true : false>;
/** …and an `'index'` drop still does, so the assertion above is not vacuous. */
export type _IndexEventHasIndex = Expect<HasNewIndex<IndexEvent>>;
/** An omitted policy stays `'index'` — every pre-#112 board keeps its index unchanged. */
export type _DefaultPolicyIsIndex = Expect<HasNewIndex<CollectionMoveEvent<Row>>>;

/** The same, through the whole prop chain rather than the event alias alone — this is what a
 *  consumer's handler is actually checked against. */
type OnMoveParam<P extends DragPolicy> = Parameters<
  NonNullable<CollectionViewProps<Row, unknown, P>['kanban']>['onMove']
>[0];
export type _ColumnPropsHideTheIndex = Expect<
  HasNewIndex<OnMoveParam<'column'>> extends false ? true : false
>;
export type _IndexPropsCarryTheIndex = Expect<HasNewIndex<OnMoveParam<'index'>>>;

/** THE hazard: a handler that names the index cannot serve a `'column'` board. Parameters are
 *  contravariant, so this is exactly the check that fails at a real `onMove` call site. */
export type _IndexHandlerRejectedOnAColumnBoard = Expect<
  IsAssignable<(e: IndexEvent) => Promise<void>, (e: ColumnEvent) => Promise<void>> extends false
    ? true
    : false
>;
/** The converse, and the reason `KanbanView` needs no cast: a `'column'` handler is legal
 *  wherever the layer emits the superset. Without this the assertion above would be satisfied
 *  by two types that are merely unrelated. */
export type _ColumnHandlerAcceptsTheSuperset = Expect<
  IsAssignable<(e: ColumnEvent) => Promise<void>, (e: IndexEvent) => Promise<void>>
>;

/** A `'column'` config is not a `CollectionConfig<Row>` — the policy has to be declared in the
 *  TYPE, which is what carries it to `CollectionViewProps`. */
export type _ColumnConfigIsNotTheDefaultConfig = Expect<
  IsAssignable<CollectionConfig<Row, 'column'>, CollectionConfig<Row>> extends false ? true : false
>;
/** But both satisfy the widened form the layer's internals hold. */
export type _EitherPolicySatisfiesTheWideForm = Expect<
  IsAssignable<CollectionConfig<Row, 'column'>, CollectionConfig<Row, DragPolicy>>
>;

// ---------------------------------------------------------------------------------------------
// Inference probes — real call sites, so the COMPILER checks that `CollectionView` derives the
// policy from `config` rather than from anything the caller restates.

const columnConfig: CollectionConfig<Row, 'column'> = {
  storage: { key: 'dp_column', version: 1 },
  defaultView: 'kanban',
  getItemId: r => r.id,
  searchText: r => [r.name],
  // columnCap 1 truncates column 'a': under the default policy that alone locks the board,
  // so a live board here is the policy doing the work.
  kanban: { getColumnId: r => r.stage, columnCap: 1, dragPolicy: 'column' },
};

const indexConfig: CollectionConfig<Row> = {
  storage: { key: 'dp_index', version: 1 },
  defaultView: 'kanban',
  getItemId: r => r.id,
  searchText: r => [r.name],
  // The SAME cap, so the only difference between the two probes is the policy.
  kanban: { getColumnId: r => r.stage, columnCap: 1 },
};

const columns = [
  { id: 'a', data: null },
  { id: 'b', data: null },
];

const latest: { current: CollectionState<Row> | null } = { current: null };

/**
 * The policy is inferred from `config`, and the handler may not name an index. Written with
 * explicit type arguments because `PipelinePage` does the same — the third argument is not
 * optional there, since supplying any of them turns inference off for all of them, and a
 * `'column'` config under a defaulted `'index'` parameter is a compile error rather than a
 * silent index.
 */
function ColumnBoardProbe() {
  const state = useCollectionState(columnConfig, rows);
  useEffect(() => {
    latest.current = state;
  });
  return (
    <CollectionView<Row, null, 'column'>
      config={columnConfig}
      state={state}
      items={rows}
      kanban={{
        columns,
        // Destructuring `newIndex` here is the exact regression this file exists to prevent.
        onMove: async ({ item, fromColumnId, toColumnId }) => {
          void item;
          void fromColumnId;
          void toColumnId;
        },
        renderColumn: (column, children) => <div data-col={String(column.id)} key={column.id}>{children}</div>,
        renderCard: item => <span className="card">{item.name}</span>,
      }}
    />
  );
}

/** The converse probe: a default-policy board still receives the index, with inference doing
 *  the work and no type argument restated. */
function IndexBoardProbe() {
  const state = useCollectionState(indexConfig, rows);
  useEffect(() => {
    latest.current = state;
  });
  return (
    <CollectionView
      config={indexConfig}
      state={state}
      items={rows}
      kanban={{
        columns,
        onMove: async ({ newIndex }) => {
          void newIndex;
        },
        renderColumn: (column, children) => <div data-col={String(column.id)} key={column.id}>{children}</div>,
        renderCard: item => <span className="card">{item.name}</span>,
      }}
    />
  );
}

// ---------------------------------------------------------------------------------------------

let container: HTMLDivElement;
let root: Root;

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

const state = (): CollectionState<Row> => {
  if (!latest.current) throw new Error('probe did not render');
  return latest.current;
};

describe('the drag-policy probes are live boards, not type fodder', () => {
  it("a 'column' board renders and stays unlocked under a truncated column", () => {
    act(() => {
      root.render(<StrictMode><ColumnBoardProbe /></StrictMode>);
    });
    // The gate the type contract justifies: the layer contributes no lock.
    expect(state().truncatedColumns.size).toBeGreaterThan(0);
    expect(state().dragLocked).toBe(false);
    expect(document.querySelectorAll('[data-col] .card').length).toBeGreaterThan(0);
  });

  it('the default-policy probe LOCKS on the identical board shape', () => {
    act(() => {
      root.render(<StrictMode><IndexBoardProbe /></StrictMode>);
    });
    // Same rows, same cap, same truncation — only the policy differs. Without this the test
    // above would pass on a board that was never lockable, proving nothing about the policy.
    expect(state().truncatedColumns.size).toBeGreaterThan(0);
    expect(state().dragLocked).toBe(true);
    expect(document.querySelectorAll('[data-col] .card').length).toBeGreaterThan(0);
  });
});
