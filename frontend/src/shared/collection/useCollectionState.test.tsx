// @vitest-environment jsdom
import { StrictMode, act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import useCollectionState from './useCollectionState';
import type { UseCollectionStateOptions } from './useCollectionState';
import type { CollectionConfig, CollectionState } from './types';

interface Row {
  id: number;
  name: string;
  stage: number;
  priority: boolean;
  voided: boolean;
}

const items: Row[] = [
  { id: 1, name: 'alpha', stage: 1, priority: true, voided: false },
  { id: 2, name: 'beta', stage: 1, priority: false, voided: false },
  { id: 3, name: 'gamma', stage: 1, priority: false, voided: true },
  { id: 4, name: 'delta', stage: 2, priority: true, voided: false },
];

function makeConfig(key: string, overrides: Partial<CollectionConfig<Row>> = {}): CollectionConfig<Row> {
  return {
    storage: { key, version: 1 },
    defaultView: 'kanban',
    getItemId: r => r.id,
    searchText: r => [r.name],
    facets: [
      { kind: 'boolean', key: 'priority', label: 'Priority', predicate: r => r.priority },
    ],
    sort: {
      fields: [
        { value: 'manual', label: 'Board order', arrayOrder: true },
        { value: 'name', label: 'Name', get: r => r.name },
      ],
    },
    toggles: [{ key: 'showClosed', label: 'Show closed', default: false }],
    kanban: { getColumnId: r => r.stage, columnCap: 2 },
    list: { columns: [] },
    getVoided: r => r.voided,
    ...overrides,
  };
}

// House DOM-test harness: createRoot + React 19 act under StrictMode (no RTL).
let container: HTMLDivElement;
let root: Root;
// House hook-capture idiom (the blueprint's polling-hook test): mutate a property inside an
// effect — reassigning a module variable during render trips react-hooks/globals.
const latest: { current: CollectionState<Row> | null } = { current: null };

function Probe({
  config,
  data,
  options,
}: {
  config: CollectionConfig<Row>;
  data: readonly Row[];
  options?: UseCollectionStateOptions;
}) {
  const value = useCollectionState(config, data, options);
  useEffect(() => {
    latest.current = value;
  });
  return null;
}

function renderState(
  config: CollectionConfig<Row>,
  data: readonly Row[] = items,
  options?: UseCollectionStateOptions,
): void {
  act(() => {
    root.render(
      <StrictMode>
        <Probe config={config} data={data} options={options} />
      </StrictMode>,
    );
  });
}

const state = (): CollectionState<Row> => {
  if (!latest.current) throw new Error('probe did not render');
  return latest.current;
};

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

describe('view validity', () => {
  it('fails fast when defaultView has no matching block', () => {
    const bad = makeConfig('vv_bad', { defaultView: 'cards' });
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    expect(() => renderState(bad)).toThrow(/defaultView "cards" has no matching view block/);
    spy.mockRestore();
  });

  it('a stale persisted view falls back to defaultView', () => {
    sessionStorage.setItem('collection_vv_stale_view', JSON.stringify('cards'));
    renderState(makeConfig('vv_stale'));
    expect(state().view).toBe('kanban');
  });
});

describe('persistence restore', () => {
  it('restores facets, voided and toggles without clobbering on mount', () => {
    sessionStorage.setItem(
      'collection_restore_v1',
      JSON.stringify({ facets: { priority: true }, voided: 'hide', toggles: { showClosed: true } }),
    );
    renderState(makeConfig('restore'));
    expect(state().facetSelections.priority).toBe(true);
    expect(state().voided).toBe('hide');
    expect(state().toggles.showClosed).toBe(true);
  });

  // Storage-restore cases mount FRESH — useState initialisers run once per mount, so a config
  // swap into a live probe would (correctly) not re-read storage. One mount per case.
  it('does NOT persist or restore the query by default', () => {
    sessionStorage.setItem('collection_q_off_v1', JSON.stringify({ query: 'alpha' }));
    renderState(makeConfig('q_off'));
    expect(state().query).toBe('');
    act(() => state().setQuery('beta'));
    expect(JSON.parse(sessionStorage.getItem('collection_q_off_v1') ?? '{}').query).toBeUndefined();
  });

  it('the persistSearch knob opts the query into the envelope', () => {
    sessionStorage.setItem('collection_q_on_v1', JSON.stringify({ query: 'alpha' }));
    renderState(makeConfig('q_on', { persistSearch: true }));
    expect(state().query).toBe('alpha');
    act(() => state().setQuery('beta'));
    expect(JSON.parse(sessionStorage.getItem('collection_q_on_v1') ?? '{}').query).toBe('beta');
  });

  it('restores sort from its OWN key', () => {
    sessionStorage.setItem('collection_sortk_sort_v1', JSON.stringify({ field: 'name', dir: 'desc' }));
    renderState(makeConfig('sortk'));
    expect(state().sort).toEqual({ field: 'name', dir: 'desc' });
  });

  it('coerces a stale persisted sort back to the default', () => {
    sessionStorage.setItem('collection_sortj_sort_v1', JSON.stringify({ field: 'gone', dir: 'up' }));
    renderState(makeConfig('sortj'));
    expect(state().sort).toEqual({ field: 'manual', dir: 'asc' });
  });
});

describe('the drag gate', () => {
  it('unlocked at rest under manual order with columns under the cap', () => {
    // Column 1 holds 2 visible non-voided rows with cap 2 — at, not over, the cap.
    renderState(makeConfig('gate_rest'));
    expect(state().dragLocked).toBe(false);
    expect(state().manualOrder).toBe(true);
  });

  it('locks while filtering (query or facet or voided), and a toggle does NOT lock', () => {
    renderState(makeConfig('gate_filter'));
    act(() => state().setQuery('alp'));
    expect(state().isFiltering).toBe(true);
    expect(state().dragLocked).toBe(true);
    act(() => state().setQuery(''));
    expect(state().dragLocked).toBe(false);

    act(() => state().setVoided('hide'));
    expect(state().dragLocked).toBe(true);
    act(() => state().setVoided(null));

    act(() => state().setToggle('showClosed', true));
    expect(state().isFiltering).toBe(false);
    expect(state().dragLocked).toBe(false);
  });

  it('locks under a non-manual sort; returning to manual unlocks', () => {
    renderState(makeConfig('gate_sort'));
    act(() => state().setSort({ field: 'name', dir: 'asc' }));
    expect(state().manualOrder).toBe(false);
    expect(state().dragLocked).toBe(true);
    act(() => state().setSort({ field: 'manual', dir: 'asc' }));
    expect(state().dragLocked).toBe(false);
  });

  it('arrayOrder DESCENDING is a reversal, not manual order', () => {
    renderState(makeConfig('gate_desc'));
    act(() => state().setSort({ field: 'manual', dir: 'desc' }));
    expect(state().manualOrder).toBe(false);
    expect(state().dragLocked).toBe(true);
  });

  it('locks while any column is truncated; expanding it unlocks', () => {
    const config = makeConfig('gate_cap', { kanban: { getColumnId: r => r.stage, columnCap: 1 } });
    renderState(config);
    // Column 1 renders 2 non-voided rows over cap 1 → truncated → locked.
    expect(state().truncatedColumns.has(1)).toBe(true);
    expect(state().dragLocked).toBe(true);
    act(() => state().expandColumn(1));
    expect(state().truncatedColumns.has(1)).toBe(false);
    expect(state().dragLocked).toBe(false);
  });
});

describe('handlers own their resets', () => {
  it('query/facet/sort/view mutations clear expansions; a toggle preserves them', () => {
    const config = makeConfig('resets', { kanban: { getColumnId: r => r.stage, columnCap: 1 } });
    renderState(config);
    act(() => state().expandColumn(1));
    expect(state().expandedColumns.has(1)).toBe(true);

    act(() => state().setQuery('x'));
    expect(state().expandedColumns.size).toBe(0);

    act(() => state().expandColumn(1));
    act(() => state().setToggle('showClosed', true));
    expect(state().expandedColumns.has(1)).toBe(true);

    act(() => state().setSort({ field: 'name', dir: 'asc' }));
    expect(state().expandedColumns.size).toBe(0);
  });

  it('clearFacets clears facets and voided but PRESERVES the query', () => {
    renderState(makeConfig('clear'));
    act(() => {
      state().setQuery('alp');
    });
    act(() => {
      state().setFacet('priority', true);
    });
    act(() => {
      state().setVoided('hide');
    });
    act(() => state().clearFacets());
    expect(state().query).toBe('alp');
    expect(state().facetSelections.priority).toBe(false);
    expect(state().voided).toBeNull();
  });
});

describe('controlled toggles', () => {
  it('override the persisted value and route writes to the app, never the envelope', () => {
    // Default false, controlled true, write true: a buggy envelope write would flip the
    // stored value to true, the correct path leaves it false — the states are distinguishable
    // in every direction.
    const onToggle = vi.fn();
    renderState(makeConfig('ctrl'), items, {
      controlledToggles: { values: { showClosed: true }, onToggle },
    });
    expect(state().toggles.showClosed).toBe(true);
    act(() => state().setToggle('showClosed', true));
    expect(onToggle).toHaveBeenCalledWith('showClosed', true);
    expect(
      JSON.parse(sessionStorage.getItem('collection_ctrl_v1') ?? '{}').toggles?.showClosed,
    ).toBe(false);
  });

  it('work with NO matching ToggleDef declared', () => {
    // CRM stage visibility is the shape: one controlled key per pipeline stage, discovered at
    // runtime, rendered by the APP's own panel (color dots, per-stage deal counts) rather than
    // by the shell's toolbar checkboxes — so the config declares no `toggles` at all and the
    // dispatch must key on membership in `controlled.values`, not on a declaration.
    const onToggle = vi.fn();
    const config = makeConfig('nodefs', { toggles: undefined });
    expect(config.toggles).toBeUndefined();
    renderState(config, items, {
      controlledToggles: { values: { 'stage:7': true, 'stage:9': false }, onToggle },
    });
    expect(state().toggles['stage:7']).toBe(true);
    expect(state().toggles['stage:9']).toBe(false);

    act(() => state().setToggle('stage:9', true));
    expect(onToggle).toHaveBeenCalledWith('stage:9', true);
    // Nothing about server-persisted visibility may reach sessionStorage.
    expect(
      JSON.parse(sessionStorage.getItem('collection_nodefs_v1') ?? '{}').toggles ?? {},
    ).toEqual({});
  });
});

describe('per-view void policy', () => {
  it("kanban hides voided rows by default while the list's visible set keeps them", () => {
    renderState(makeConfig('voidpol'));
    expect(state().visibleItems.map(r => r.id)).toEqual([1, 2, 3, 4]);
    expect(state().kanbanItems.map(r => r.id)).toEqual([1, 2, 4]);
  });

  it("voidedPolicy 'facet' makes the board obey the tri-state like the list", () => {
    renderState(
      makeConfig('voidfacet', {
        kanban: { getColumnId: r => r.stage, voidedPolicy: 'facet' },
      }),
    );
    expect(state().kanbanItems.map(r => r.id)).toEqual([1, 2, 3, 4]);
  });
});

describe('filtering pipeline', () => {
  it('token-AND search over searchText plus facets, sorted by the active sort', () => {
    renderState(makeConfig('pipe'));
    act(() => state().setSort({ field: 'name', dir: 'desc' }));
    expect(state().visibleItems.map(r => r.name)).toEqual(['gamma', 'delta', 'beta', 'alpha']);
    act(() => state().setFacet('priority', true));
    expect(state().visibleItems.map(r => r.name)).toEqual(['delta', 'alpha']);
    act(() => state().setQuery('del'));
    expect(state().visibleItems.map(r => r.name)).toEqual(['delta']);
  });
});

describe('searchTuning', () => {
  // Rows whose searchText contains a short numeric token embedded in a longer token, so that
  // substring-matching '1' would hit the wrong row while whole-word anchoring would not.
  const tuned: Row[] = [
    { id: 1, name: 'Unit 1', stage: 1, priority: false, voided: false },
    { id: 2, name: 'Serial A1B2', stage: 1, priority: false, voided: false },
  ];

  it('anchorShortTokens matches a short token as a whole word only', () => {
    const config = makeConfig('tune_anchor', {
      searchTuning: { anchorShortTokens: true },
    });
    renderState(config, tuned);
    act(() => state().setQuery('1'));
    // Only "Unit 1" has a standalone ' 1 '; "Serial A1B2" (→ ' serial a1b2 ') does not.
    expect(state().visibleItems.map(r => r.id)).toEqual([1]);
  });

  it('without tuning, a short token substring-matches both rows (default preserved)', () => {
    renderState(makeConfig('tune_default'), tuned);
    act(() => state().setQuery('1'));
    expect(state().visibleItems.map(r => r.id)).toEqual([1, 2]);
  });

  it('stopwords make an all-stopword multi-word query inactive (no filtering)', () => {
    const config = makeConfig('tune_stop', {
      searchTuning: { stopwords: ['the', 'in'] },
    });
    renderState(config, tuned);
    act(() => state().setQuery('in the'));
    expect(state().isFiltering).toBe(false);
    expect(state().visibleItems).toHaveLength(2);
  });
});

describe('dragPolicy — what a drop MEANS decides what can lock it', () => {
  // The default 'index' policy assumes the app persists the drop position, so a filtered
  // subset, a non-array sort and a truncated column each make the index unmappable.
  // A 'column' board discards newIndex entirely, so none of the three can make a drop
  // ambiguous and the layer must contribute no lock at all.
  it("'column' keeps drag live under a filter, a non-manual sort AND a truncated column", () => {
    // columnCap 1 with three stage-1 rows guarantees a truncated column from the start.
    const config = makeConfig('dp_column', {
      kanban: { getColumnId: r => r.stage, columnCap: 1, dragPolicy: 'column' },
    });
    renderState(config);
    expect(state().truncatedColumns.size).toBeGreaterThan(0);
    expect(state().dragLocked).toBe(false);

    act(() => state().setQuery('alp'));
    expect(state().isFiltering).toBe(true);
    expect(state().dragLocked).toBe(false);

    act(() => state().setSort({ field: 'name', dir: 'asc' }));
    expect(state().manualOrder).toBe(false);
    expect(state().dragLocked).toBe(false);
  });

  // Each condition gets its own mount: re-rendering the same root with a different config
  // does NOT re-run the hook's useState initialisers, so a query set earlier would leak in
  // and make the next assertion pass for the wrong reason.
  it("an omitted policy behaves as 'index' — a filter locks drag", () => {
    renderState(makeConfig('dp_default_filter'));
    expect(state().dragLocked).toBe(false);
    act(() => state().setQuery('alp'));
    expect(state().dragLocked).toBe(true);
  });

  it("an omitted policy behaves as 'index' — a non-manual sort locks drag", () => {
    renderState(makeConfig('dp_default_sort'));
    expect(state().dragLocked).toBe(false);
    act(() => state().setSort({ field: 'name', dir: 'asc' }));
    expect(state().manualOrder).toBe(false);
    expect(state().dragLocked).toBe(true);
  });

  it("an omitted policy behaves as 'index' — a truncated column locks drag alone", () => {
    // columnCap 1 truncates stage 1 (three rows) with no query and the resting sort, so
    // truncation is provably the only reason the gate is closed.
    renderState(makeConfig('dp_default_trunc', {
      kanban: { getColumnId: r => r.stage, columnCap: 1 },
    }));
    expect(state().isFiltering).toBe(false);
    expect(state().manualOrder).toBe(true);
    expect(state().truncatedColumns.size).toBeGreaterThan(0);
    expect(state().dragLocked).toBe(true);
  });

  it("'index' is the explicit default and matches an omitted policy exactly", () => {
    renderState(makeConfig('dp_explicit', {
      kanban: { getColumnId: r => r.stage, columnCap: 2, dragPolicy: 'index' },
    }));
    act(() => state().setQuery('alp'));
    expect(state().dragLocked).toBe(true);
  });
});
