// @vitest-environment jsdom
//
// The detail close contract. Every leave path — Escape, ×, backdrop, ‹ › nav —
// asks permission and the LAYER performs the result. This suite drives the real
// `shared/overlay/DetailModal` underneath (its jsdom visibility probe is stubbed the same way
// its own suite stubs it), so the reason routing is proven through the actual chrome, not a
// mock.
import { StrictMode, act, useEffect, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import useCollectionState from '../useCollectionState';
import CollectionDetail from './CollectionDetail';
import type {
  CollectionConfig,
  CollectionDetailProps,
  CollectionState,
  DetailCloseGuard,
  DetailRenderContext,
} from '../types';

interface Row {
  id: number;
  name: string;
}

const rows: Row[] = [
  { id: 1, name: 'alpha' },
  { id: 2, name: 'beta' },
  { id: 3, name: 'gamma' },
];

function makeConfig(key: string, overrides: Partial<CollectionConfig<Row>> = {}): CollectionConfig<Row> {
  return {
    storage: { key, version: 1 },
    defaultView: 'list',
    getItemId: r => r.id,
    searchText: r => [r.name],
    list: { columns: [{ key: 'name', header: 'Name', render: r => r.name }] },
    detail: { getTitle: r => r.name },
    ...overrides,
  };
}

// ── Module-scope bodies (the blueprint — never declare components during render) ─────────────────────

function PlainBody({ item }: { item: Row }) {
  return <p data-testid="body">{item.name}</p>;
}

/** Registers a guard that denies ONLY Escape — nav stays allowed so binding across records
 *  is testable — and only for the record id it is told to guard. */
function EscapeGuardBody({
  item,
  ctx,
  guardId,
}: {
  item: Row;
  ctx: DetailRenderContext;
  guardId: number;
}) {
  useEffect(() => {
    if (item.id !== guardId) return;
    return ctx.registerCloseGuard(reason => reason !== 'escape');
  }, [ctx, item.id, guardId]);
  return <p data-testid="body">{item.name}</p>;
}

/** Per-record draft state — proves the keyed remount resets it on ‹ › nav. */
function CounterBody({ item }: { item: Row }) {
  const [count, setCount] = useState(0);
  return (
    <button type="button" data-testid="counter" onClick={() => setCount(c => c + 1)}>
      {item.name}:{count}
    </button>
  );
}

// ── Harness ──────────────────────────────────────────────────────────────────────────────────

let container: HTMLDivElement;
let root: Root;
let rectSpy: ReturnType<typeof vi.spyOn>;
const latest: { current: CollectionState<Row> | null } = { current: null };
const selectCalls: Array<string | number | null> = [];

function Page({
  config,
  initialId,
  detail,
  // Re-rendering the page with a smaller array is how a test makes an OPEN record leave the
  // canonical set — the shape CRM's Mark Won produces when the Won stage is hidden.
  items = rows,
  // A page rendering its records outside CollectionView supplies the order the user sees
 // — a sibling surface's dashboard-filtered branch is the live caller.
  navOrder,
}: {
  config: CollectionConfig<Row>;
  initialId: number;
  detail: CollectionDetailProps<Row>;
  items?: Row[];
  navOrder?: readonly (string | number)[];
}) {
  const state = useCollectionState(config, items);
  const [selected, setSelected] = useState<string | number | null>(initialId);
  useEffect(() => {
    latest.current = state;
  });
  return (
    <CollectionDetail
      config={config}
      state={state}
      items={items}
      selectedId={selected}
      onSelect={id => {
        selectCalls.push(id);
        setSelected(id);
      }}
      detail={detail}
      navOrder={navOrder}
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

/** Flush the async permission chain (guards resolve in microtasks). */
async function settle(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

const dialogTitle = () => document.querySelector('[role="dialog"] h2')?.textContent ?? null;
const pressEscape = () =>
  act(() => {
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
  });
const clickClose = () =>
  act(() => {
    (document.querySelector('button[aria-label="Close"]') as HTMLElement).click();
  });
const navButton = (label: 'Previous record' | 'Next record') =>
  document.querySelector(`button[aria-label="${label}"]`) as HTMLButtonElement;
const clickBackdropGesture = () => {
  // A real backdrop dismissal is mousedown + mouseup + click, ALL on the wrapper — the
  // DetailModal gesture contract (its own suite dispatches the same trio).
  const wrapper = document.querySelector('.fixed.inset-0') as HTMLElement;
  act(() => {
    wrapper.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    wrapper.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
    wrapper.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
};

const plainDetail: CollectionDetailProps<Row> = {
  render: item => <PlainBody item={item} />,
};

beforeEach(() => {
  sessionStorage.clear();
  selectCalls.length = 0;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  latest.current = null;
  // jsdom lays nothing out — drive DetailModal's visibility probe so the modal joins its
  // stack and answers Escape (the same stub its own suite uses).
  rectSpy = vi
    .spyOn(Element.prototype, 'getBoundingClientRect')
    .mockImplementation(
      () =>
        ({ width: 480, height: 600, top: 0, left: 0, right: 480, bottom: 600, x: 0, y: 0, toJSON: () => ({}) }) as DOMRect,
    );
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  rectSpy.mockRestore();
  document.body.style.overflow = '';
  document.body.style.paddingRight = '';
  document.body.innerHTML = '';
});

// ---------------------------------------------------------------------------------------------

describe('default close policy (no onRequestClose)', () => {
  it('Escape and the × close; backdrop does NOT', async () => {
    renderPage({ config: makeConfig('def'), initialId: 1, detail: plainDetail });
    expect(dialogTitle()).toBe('alpha');

    clickBackdropGesture();
    await settle();
    expect(dialogTitle()).toBe('alpha');
    expect(selectCalls).toEqual([]);

    pressEscape();
    await settle();
    expect(selectCalls).toEqual([null]);
    expect(document.querySelector('[role="dialog"]')).toBeNull();
  });

  it('the × routes as a button close', async () => {
    renderPage({ config: makeConfig('defx'), initialId: 1, detail: plainDetail });
    clickClose();
    await settle();
    expect(selectCalls).toEqual([null]);
  });
});

describe('onRequestClose', () => {
  it('receives the true reason per path and its verdict decides', async () => {
    const reasons: string[] = [];
    const guard: DetailCloseGuard = reason => {
      reasons.push(reason);
      return reason === 'nav';
    };
    renderPage({
      config: makeConfig('reasons'),
      initialId: 1,
      detail: { render: item => <PlainBody item={item} />, onRequestClose: guard },
    });

    pressEscape();
    await settle();
    expect(dialogTitle()).toBe('alpha'); // denied

    clickClose();
    await settle();
    expect(dialogTitle()).toBe('alpha'); // denied

    clickBackdropGesture();
    await settle();
    expect(dialogTitle()).toBe('alpha'); // denied (handler owns backdrop when present)

    act(() => navButton('Next record').click());
    await settle();
    expect(dialogTitle()).toBe('beta'); // allowed — the LAYER performed the nav
    expect(selectCalls).toEqual([2]);
    expect(reasons).toEqual(['escape', 'button', 'backdrop', 'nav']);
  });
});

describe('‹ › navigation', () => {
  it('walks visibleOrder, disabling at the ends', async () => {
    renderPage({ config: makeConfig('nav'), initialId: 1, detail: plainDetail });
    expect(navButton('Previous record').disabled).toBe(true);
    expect(navButton('Next record').disabled).toBe(false);

    act(() => navButton('Next record').click());
    await settle();
    act(() => navButton('Next record').click());
    await settle();
    expect(dialogTitle()).toBe('gamma');
    expect(navButton('Next record').disabled).toBe(true);
    expect(navButton('Previous record').disabled).toBe(false);
    expect(selectCalls).toEqual([2, 3]);
  });

  it('disables BOTH arrows when the open record leaves the visible set', () => {
    renderPage({ config: makeConfig('navout'), initialId: 2, detail: plainDetail });
    act(() => state().setQuery('alpha')); // beta is filtered out while open
    expect(navButton('Previous record').disabled).toBe(true);
    expect(navButton('Next record').disabled).toBe(true);
    expect(dialogTitle()).toBe('beta'); // still open — filtered ≠ dismissed
  });
});

describe('navOrder — a surface the page renders outside CollectionView', () => {
  it('walks the page order instead of visibleOrder', async () => {
    // Reversed, so an order taken from the layer could not produce this sequence by accident.
    renderPage({
      config: makeConfig('navorder'),
      initialId: 3,
      detail: plainDetail,
      navOrder: [3, 2, 1],
    });
    expect(navButton('Previous record').disabled).toBe(true);

    act(() => navButton('Next record').click());
    await settle();
    expect(dialogTitle()).toBe('beta');
    act(() => navButton('Next record').click());
    await settle();
    expect(dialogTitle()).toBe('alpha');
    expect(navButton('Next record').disabled).toBe(true);
    expect(selectCalls).toEqual([2, 1]);
  });

  it('keeps navigating a record the LAYER has filtered out — the reported bug', () => {
    // A sibling surface's shape: the page is rendering a different query's rows (a completed
    // request the canonical set never held), so the open id is absent from `visibleOrder` and
    // both arrows used to sit dead. The page's own order is what the user is looking at.
    renderPage({
      config: makeConfig('navorderout'),
      initialId: 2,
      detail: plainDetail,
      navOrder: [1, 2, 3],
    });
    act(() => state().setQuery('alpha')); // beta leaves the LAYER's visible set
    expect(dialogTitle()).toBe('beta');
    expect(navButton('Previous record').disabled).toBe(false);
    expect(navButton('Next record').disabled).toBe(false);
  });

  it('pages through records the canonical set never held, via loadById', async () => {
    // The literal "Completed this week" shape, and the one the previous case only
    // approximates: these rows come from a DIFFERENT query, so they are absent from `items`
    // entirely rather than merely filtered out of it. Every record therefore resolves through
    // `loadById`, which is the path an archived request actually takes.
    const archived: Record<number, string> = { 7: 'seven', 8: 'eight', 9: 'nine' };
    const loadById = vi.fn(async (id: string | number): Promise<Row> =>
      ({ id: Number(id), name: archived[Number(id)] }));
    renderPage({
      config: makeConfig('navorderfetch', { detail: { getTitle: r => r.name, loadById } }),
      initialId: 8,
      detail: plainDetail,
      items: [],
      navOrder: [7, 8, 9],
    });
    await settle();
    expect(dialogTitle()).toBe('eight');
    expect(navButton('Previous record').disabled).toBe(false);
    expect(navButton('Next record').disabled).toBe(false);

    act(() => navButton('Next record').click());
    await settle();
    expect(dialogTitle()).toBe('nine');
    expect(document.querySelector('[data-testid="body"]')?.textContent).toBe('nine');
    expect(navButton('Next record').disabled).toBe(true);

    act(() => navButton('Previous record').click());
    await settle();
    expect(dialogTitle()).toBe('eight');
    expect(selectCalls).toEqual([9, 8]);
  });

  it('an EMPTY order means nothing to navigate — not "fall back to the layer"', () => {
    renderPage({
      config: makeConfig('navorderempty'),
      initialId: 1,
      detail: plainDetail,
      navOrder: [],
    });
    expect(navButton('Previous record').disabled).toBe(true);
    expect(navButton('Next record').disabled).toBe(true);
  });
});

describe('registerCloseGuard', () => {
  it('a body guard denies its reason; unregister (via nav remount) releases it', async () => {
    renderPage({
      config: makeConfig('guard'),
      initialId: 1,
      detail: { render: (item, ctx) => <EscapeGuardBody item={item} ctx={ctx} guardId={1} /> },
    });
    pressEscape();
    await settle();
    expect(dialogTitle()).toBe('alpha'); // record 1's guard denies escape

    act(() => navButton('Next record').click()); // nav is allowed by the guard
    await settle();
    expect(dialogTitle()).toBe('beta');

    // Record 2 registered nothing — record 1's guard must NOT be consulted for it.
    pressEscape();
    await settle();
    expect(selectCalls).toEqual([2, null]);
  });

  it('nav remounts the keyed body — per-record draft state never leaks across records', async () => {
    renderPage({
      config: makeConfig('remount'),
      initialId: 1,
      detail: { render: item => <CounterBody item={item} /> },
    });
    const counter = () => document.querySelector('[data-testid="counter"]') as HTMLElement;
    act(() => counter().click());
    act(() => counter().click());
    expect(counter().textContent).toBe('alpha:2');

    act(() => navButton('Next record').click());
    await settle();
    expect(counter().textContent).toBe('beta:0');
  });
});

describe('a record that leaves the canonical set while open', () => {
  /** Never settles on purpose: the panel has to survive on the one-record memory alone, not on
   *  a fetch that happens to be fast. */
  const pending = () => vi.fn((): Promise<Row> => new Promise(() => {}));

  it('keeps rendering it — no loading shell, and the body does NOT remount', async () => {
    const loadById = pending();
    const config = makeConfig('leaves', { detail: { getTitle: r => r.name, loadById } });
    renderPage({ config, initialId: 1, detail: plainDetail });
    expect(dialogTitle()).toBe('alpha');
    const body = document.querySelector('[data-testid="body"]');
    expect(body).not.toBeNull();

    renderPage({
      config,
      initialId: 1,
      detail: plainDetail,
      items: rows.filter(r => r.id !== 1),
    });
    await settle();

    expect(dialogTitle()).toBe('alpha');
    // NODE IDENTITY, not text: the defect is the remount (which destroys scroll position and
    // every draft the body holds), and identical text renders either way.
    expect(document.querySelector('[data-testid="body"]')).toBe(body);
    // The refresh is still in flight — the memory covers the gap, it does not replace the read.
    expect(loadById).toHaveBeenCalledWith(1);
  });

  it('a COLD deep link — nothing ever resolved for this id — still shows the loading shell', () => {
    renderPage({
      config: makeConfig('cold', { detail: { getTitle: r => r.name, loadById: pending() } }),
      initialId: 99,
      detail: plainDetail,
    });
    expect(dialogTitle()).toBe('Loading…');
  });
});

describe('loadById deep links', () => {
  it('renders a loading body, then the fetched record; errors offer Retry', async () => {
    let mode: 'fail' | 'ok' = 'fail';
    const loadById = vi.fn(async (id: string | number): Promise<Row> => {
      if (mode === 'fail') throw new Error('nope');
      return { id: Number(id), name: 'fetched' };
    });
    renderPage({
      config: makeConfig('deep', { detail: { getTitle: r => r.name, loadById } }),
      initialId: 99,
      detail: plainDetail,
    });
    await settle();
    expect(dialogTitle()).toBe('Record unavailable');

    mode = 'ok';
    act(() => {
      [...document.querySelectorAll('button')]
        .find(b => b.textContent === 'Retry')
        ?.click();
    });
    await settle();
    expect(dialogTitle()).toBe('fetched');
    expect(document.querySelector('[data-testid="body"]')?.textContent).toBe('fetched');
    expect(loadById).toHaveBeenCalledWith(99);
  });
});
