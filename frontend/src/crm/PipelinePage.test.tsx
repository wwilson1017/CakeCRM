// @vitest-environment jsdom
//
// The pipeline's page-level contracts — the ones that only exist where the board, the detail
// panel and the write path meet, so neither `DealDetailBody.test.tsx` nor `boardNavOrder.test.ts`
// can see them:
//
//  • The URL contract, which is the half of the Copy-link feature nobody can eyeball: the
//    parameter is stripped the instant it is read, so "did the link open the right deal, and did
//    the address bar come out clean" is only answerable from a test. The single-effect assertion
//    is the load-bearing one — `?stage=` and `?deal=` are stripped by ONE effect on purpose, since
//    two would each compute their next params from the same pre-navigation snapshot and the second
//    `replace` would put back the key the first had just deleted. That bug is invisible in the
//    common case (one parameter at a time) and only appears when a dashboard link carries both.
//
//  • `writeDeal`'s stage-vs-fields split. Every stage-specific step is conditional on the patch
//    actually CARRYING a stage; drop that condition and a fields-only save starts painting
//    optimistic restages and toasting "Failed to move deal." at someone who moved nothing.
//
//  • The bulk lock REJECTING rather than returning quietly, which is the whole difference between
//    "your edit was refused, try again" and losing what someone just typed.
//
//  • `navOrder` actually reaching the layer, in the board's column-major order rather than the
//    array's.
import { act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, useLocation, useNavigate, type NavigateFunction } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { CrmDeal } from '../core/types';
import type { KanbanBoardProps } from '../shared/dnd';

const api = vi.hoisted(() => vi.fn());
vi.mock('../core/api/client', () => ({
  api,
  ApiError: class ApiError extends Error {
    status: number;
    constructor(message: string, status = 500) { super(message); this.status = status; }
  },
}));

// dnd-kit's pointer sensors do nothing in jsdom, so the DRAG half of the board is stubbed out.
// Its RENDER contract is not: `renderColumn` / `renderCard` are PipelinePage's own code, and the
// bulk-selection affordances live inside them (`StageHeader`'s Select-all checkbox, each card's
// own checkbox, the card click that opens the detail). So this stub reproduces exactly what the
// real `KanbanBoard` does with those two props — columns in order, each column's items through
// `renderCard` — and stands in for nothing else. Every component the tests below click on is real.
vi.mock('../shared/dnd', () => ({
  KanbanBoard: ({ columns, items, renderColumn, renderCard, renderEmptyColumn }:
    KanbanBoardProps<CrmDeal, { stage: string }>) => (
    <div>
      {columns.map(col => {
        const colItems = items[String(col.id)] ?? [];
        return renderColumn(col, colItems.length === 0
          ? renderEmptyColumn?.(col)
          : colItems.map(item => <div key={item.id}>{renderCard(item, col.id, false)}</div>));
      })}
    </div>
  ),
}));

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }));
vi.mock('../shared/toast', () => ({ toast }));

const { PipelinePage } = await import('./PipelinePage');
const { ActiveRecordProvider } = await import('./RecordContext');

function deal(id: number, over: Partial<CrmDeal> = {}): CrmDeal {
  return {
    id, title: `Deal ${id}`, stage: 'lead', value: 100, notes: '',
    contact_id: null, company_id: null, expected_close_date: '', probability: 0,
    currency: 'USD', created_at: '', updated_at: '', ...over,
  };
}

const BOARD = [deal(5, { title: 'Wholesale order' }), deal(6, { title: 'Retail order' })];

let container: HTMLDivElement;
let root: Root;

/** Probe state lives on ONE object, written from an effect. Assigning to a module-level
 *  variable during render is what `react-hooks/globals` forbids (and it would be a real
 *  render-phase side effect); mutating a holder in a commit-phase effect is the pattern the
 *  layer's own suites already use. */
const probe: { search: string; go: NavigateFunction } = { search: '', go: () => {} };

function LocationProbe() {
  const { search } = useLocation();
  // `go` lets a test change the URL WITHOUT remounting the page — the only way to exercise a
  // ref that survives across navigations.
  const navigate = useNavigate();
  useEffect(() => {
    probe.search = search;
    probe.go = navigate;
  });
  return null;
}

beforeEach(() => {
  sessionStorage.clear();
  probe.search = '';
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: () => ({ matches: false, addEventListener: () => {}, removeEventListener: () => {} }),
  });
  Element.prototype.scrollIntoView = vi.fn();
  api.mockReset();
  api.mockImplementation((path: string) => {
    if (path === '/api/crm/deals') return Promise.resolve({ deals: BOARD });
    if (/^\/api\/crm\/deals\/\d+$/.test(path)) {
      const id = Number(path.split('/').pop());
      return Promise.resolve({ ...deal(id, { title: `Fetched ${id}` }), activity: [] });
    }
    if (path.startsWith('/api/crm/provenance/')) return Promise.resolve({ provenance: [] });
    if (path.startsWith('/api/crm/chatter/')) return Promise.resolve({ notes: [] });
    // The edit form's two pickers. Answered properly rather than left to the catch-all, because a
    // rejected picker now raises its own toast and would drown out the ones under test here.
    if (path.startsWith('/api/crm/contacts')) return Promise.resolve({ contacts: [] });
    if (path.startsWith('/api/crm/companies')) return Promise.resolve({ companies: [] });
    if (/\/fields$/.test(path)) return Promise.resolve([]);
    if (path.startsWith('/api/users')) return Promise.resolve({ users: [] });
    return Promise.resolve({});
  });
  toast.success.mockReset();
  toast.error.mockReset();
  toast.info.mockReset();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function renderAt(url: string) {
  act(() => {
    root.render(
      <MemoryRouter initialEntries={[url]}>
        <LocationProbe />
        <ActiveRecordProvider>
          <PipelinePage />
        </ActiveRecordProvider>
      </MemoryRouter>,
    );
  });
}

async function settle(rounds = 6) {
  for (let i = 0; i < rounds; i++) {
    await act(async () => { await Promise.resolve(); });
  }
}

const dialogTitle = () => document.querySelector('[role="dialog"] h2')?.textContent ?? null;

// ── DOM helpers ──────────────────────────────────────────────────────────────────────────────

const click = (el: Element | null) => act(() => { (el as HTMLElement).click(); });

const buttonByText = (text: string) =>
  [...container.querySelectorAll('button')].find(b => b.textContent?.trim() === text) ?? null;

const byLabel = <E extends Element>(label: string) =>
  container.querySelector<E>(`[aria-label="${label}"]`);

/** Card opener: the board card is a `role="button"` div; its checkbox stops propagation itself. */
const cardTitled = (title: string) =>
  [...container.querySelectorAll<HTMLElement>('[role="button"]')]
    .find(el => el.textContent?.includes(title)) ?? null;

/** Which column a card is RENDERED in — the board's own answer, not `data.deals`' stage field.
 *  `renderColumn` stamps `data-stage`, and the detail panel is a sibling of the whole board, so
 *  a column's text is only its own cards. */
const stageColumn = (stage: string) =>
  container.querySelector<HTMLElement>(`[data-stage="${stage}"]`);

/** React tracks the DOM value node-side, so a bare `el.value = x` is swallowed as a no-op. */
function setValue(el: HTMLInputElement | HTMLSelectElement | null, value: string) {
  const target = el as HTMLInputElement | HTMLSelectElement;
  act(() => {
    const proto = target instanceof HTMLSelectElement
      ? HTMLSelectElement.prototype
      : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(target, value);
    target.dispatchEvent(new Event('input', { bubbles: true }));
    target.dispatchEvent(new Event('change', { bubbles: true }));
  });
}

const setField = (id: string, value: string) =>
  setValue(container.querySelector<HTMLInputElement>(`#${id}`), value);

interface ApiCallOptions { method?: string; body?: string }
const callsWithMethod = (method: string) =>
  (api.mock.calls as [string, ApiCallOptions | undefined][])
    .filter(([, options]) => options?.method === method);

describe('the deal deep link', () => {
  it('opens the linked deal and empties the address bar', async () => {
    renderAt('/crm/pipeline?deal=5');
    await settle();
    expect(dialogTitle()).toBe('Wholesale order');
    // The whole point of the Copy-link contract: by the time anyone could select the URL, the
    // parameter is gone, so the button is the only link handoff there is.
    expect(probe.search).toBe('');
  });

  it('opens nothing for a malformed id, and still cleans up after itself', async () => {
    renderAt('/crm/pipeline?deal=abc');
    await settle();
    expect(dialogTitle()).toBeNull();
    expect(probe.search).toBe('');
  });

  it('resolves a deal that is not on the board at all', async () => {
    // An archived deal, or one a shared link opened before this board holds it: the layer
    // fetches it through `loadById` rather than showing an empty panel.
    renderAt('/crm/pipeline?deal=404');
    await settle();
    expect(dialogTitle()).toBe('Fetched 404');
  });

  it('strips ?deal= even while the board is still loading', async () => {
    // The id already lives in state by then, and a board that never loads must still leave a
    // clean address bar rather than a link that re-fires on every refresh.
    let releaseBoard: (v: unknown) => void = () => {};
    const defaults = api.getMockImplementation()!;
    api.mockImplementation((path: string, options?: RequestInit) =>
      path === '/api/crm/deals'
        ? new Promise(res => { releaseBoard = res; })
        : defaults(path, options));
    renderAt('/crm/pipeline?deal=5');
    await settle();
    expect(probe.search).toBe('');

    await act(async () => { releaseBoard({ deals: BOARD }); await Promise.resolve(); });
    await settle();
    expect(dialogTitle()).toBe('Wholesale order');
  });
});

describe('the two deep-link params together', () => {
  it('strips both without either resurrecting the other', async () => {
    // ONE effect, ONE setSearchParams. Split them and this is the case that breaks: the deal
    // strip and the stage strip each start from the same params, so whichever replaces second
    // restores the other's key.
    renderAt('/crm/pipeline?stage=lead&deal=5');
    await settle();
    expect(probe.search).toBe('');
    expect(dialogTitle()).toBe('Wholesale order');
  });

  it('strips the same ?stage= a second time instead of leaving it stuck', async () => {
    // The scroll guard remembers the last stage it scrolled to; gating the DELETE on it too
    // meant a repeat link to the SAME column returned early and left the parameter in the bar
    // forever after. This must navigate within one mount — a remount resets the ref and would
    // make the assertion unfalsifiable.
    renderAt('/crm/pipeline?stage=lead');
    await settle();
    expect(probe.search).toBe('');

    act(() => probe.go('/crm/pipeline?stage=lead'));
    await settle();
    expect(probe.search).toBe('');
  });

  it('strips a stage that is not a stage at all', async () => {
    // Deleting only VALID stages left `?stage=bogus` in the address bar forever: it never matches
    // `STAGE_ORDER`, so the delete never ran. Nothing will ever scroll to a column that cannot
    // exist, so there is nothing for the parameter to be waiting on.
    renderAt('/crm/pipeline?stage=bogus');
    await settle();
    expect(probe.search).toBe('');
  });

  // The SCROLL half of that guard is not asserted here: the `KanbanBoard` stub renders columns
  // but jsdom does no layout, so `scrollIntoView` is a stub with nothing to measure. The parameter
  // handling above is the part that broke and the part this suite owns; the scroll itself is
  // covered on the PR's evidence run.
});

describe('writeDeal: a fields-only save', () => {
  it('PUTs only the changed column, with no stage key at all', async () => {
    // The whole reason `writeDeal` generalised from `moveDealStage`: an inline edit rides the same
    // per-deal chain and the same sequence numbering as a drag, but it must not SEND a stage.
    // Sending one turns a title edit into a stage write, and `_classify_deal_update` overrides
    // `probability` to 100/0 inside any transaction that changes the stage.
    renderAt('/crm/pipeline?deal=5');
    await settle();
    click(buttonByText('Edit'));
    await settle();
    setField('deal-title', 'Renamed');
    click(buttonByText('Save'));
    await settle();

    const puts = callsWithMethod('PUT');
    expect(puts).toHaveLength(1);
    expect(puts[0][0]).toBe('/api/crm/deals/5');
    expect(JSON.parse(puts[0][1]!.body!)).toEqual({ title: 'Renamed' });
  });

  it('stays quiet when it fails — "Failed to move deal." is about a MOVE', async () => {
    // This is the assertion that pins the `toStage !== undefined` conditionals. A fields-only write
    // painted nothing optimistically, so there is nothing to revert and nothing to announce: the
    // form keeps the draft and shows the error inline, next to what it is asking the user to retry.
    // Toasting a move failure here would report a board change that never happened.
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    const defaults = api.getMockImplementation()!;
    api.mockImplementation((path: string, options?: ApiCallOptions) =>
      options?.method === 'PUT'
        ? Promise.reject(new Error('Deal is archived'))
        : defaults(path, options));

    renderAt('/crm/pipeline?deal=5');
    await settle();
    click(buttonByText('Edit'));
    await settle();
    setField('deal-title', 'Renamed');
    click(buttonByText('Save'));
    await settle();

    expect(toast.error).not.toHaveBeenCalled();
    // ...and the draft plus its reason are still on screen, which is where the report belongs.
    expect(container.querySelector<HTMLInputElement>('#deal-title')!.value).toBe('Renamed');
    expect(container.textContent).toContain('Deal is archived');
    consoleError.mockRestore();
  });
});

describe('writeDeal: a chain where BOTH writes fail', () => {
  it('reverts the optimistic stage even though the last write carried no stage', async () => {
    // The failure the `toStage !== undefined` revert gate could not see. A stage write paints 'won'
    // (seq 1); an inline field save queues behind it on the same per-deal chain (seq 2); the stage
    // PUT fails, but by then it is SUPERSEDED so it returns before reverting — correctly, since a
    // newer op owns the board. The newer op then fails too, and it is carrying no stage of its own,
    // so gating its revert on `toStage` left the board showing a stage the server never stored,
    // silently, until the next full load.
    //
    // The rule that replaces it: the last writer to fail owns the reconciliation, whatever it was
    // writing.
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    const pendingPuts: { reject: (err: unknown) => void }[] = [];
    const defaults = api.getMockImplementation()!;
    api.mockImplementation((path: string, options?: ApiCallOptions) =>
      options?.method === 'PUT'
        ? new Promise((_resolve, reject) => { pendingPuts.push({ reject }); })
        : defaults(path, options));

    renderAt('/crm/pipeline?deal=5');
    await settle();
    expect(stageColumn('lead')!.textContent).toContain('Wholesale order');

    // seq 1 — a stage write, painted optimistically and left in flight.
    click(buttonByText('Mark Won'));
    await settle();
    expect(stageColumn('won')!.textContent).toContain('Wholesale order');
    expect(pendingPuts).toHaveLength(1);

    // seq 2 — a fields-only save, queued BEHIND seq 1 (its PUT has not been issued yet).
    click(buttonByText('Edit'));
    await settle();
    setField('deal-title', 'Renamed');
    click(buttonByText('Save'));
    await settle();
    expect(pendingPuts).toHaveLength(1);

    // seq 1 fails while superseded: no revert here, by design.
    await act(async () => { pendingPuts[0].reject(new Error('stage write failed')); });
    await settle();
    expect(pendingPuts).toHaveLength(2);
    expect(stageColumn('won')!.textContent).toContain('Wholesale order');

    // seq 2 fails, and it is the latest — so IT reconciles, back to the server-confirmed stage.
    await act(async () => { pendingPuts[1].reject(new Error('field write failed')); });
    await settle();
    expect(stageColumn('lead')!.textContent).toContain('Wholesale order');
    expect(stageColumn('won')!.textContent).not.toContain('Wholesale order');
    consoleError.mockRestore();
  });
});

describe('the bulk lock', () => {
  it('rejects an inline field save while a bulk move is in flight, rather than silently succeeding', async () => {
    // Dropping a redundant drag under the lock is invisible and fine; dropping the field edit
    // someone just typed is not. A quiet `Promise.resolve()` here would close the form on a write
    // that never happened, and the reconcile refetch would then paint the pre-edit values back.
    let releaseBulk: (v: unknown) => void = () => {};
    const defaults = api.getMockImplementation()!;
    api.mockImplementation((path: string, options?: ApiCallOptions) =>
      path === '/api/crm/deals/bulk-move'
        ? new Promise(res => { releaseBulk = res; })
        : defaults(path, options));

    renderAt('/crm/pipeline');
    await settle();

    // The real flow: Select-all on the lead column, pick a target stage, Apply. The POST above
    // never settles, so the lock is still held for the rest of this test.
    click(byLabel('Select all lead deals'));
    setValue(byLabel<HTMLSelectElement>('Move selected deals to stage'), 'proposal');
    click(buttonByText('Apply'));
    await settle();

    // Now open a deal from the board and try to save a field edit through the detail panel.
    click(cardTitled('Wholesale order'));
    await settle();
    expect(dialogTitle()).toBe('Wholesale order');
    click(buttonByText('Edit'));
    await settle();
    setField('deal-title', 'Renamed mid-bulk');
    click(buttonByText('Save'));
    await settle();

    // Refused, said so, and kept the draft — no PUT reached the server.
    expect(callsWithMethod('PUT')).toHaveLength(0);
    expect(container.textContent).toContain('A bulk update is in progress');
    expect(container.querySelector<HTMLInputElement>('#deal-title')!.value).toBe('Renamed mid-bulk');

    // Let the bulk finish so the page unmounts with nothing in flight.
    await act(async () => { releaseBulk({ ok: true, updated_ids: [5, 6] }); });
    await settle();
  });

  it('keeps the panel OPEN when Mark Won is refused, instead of reporting a close that never happened', async () => {
    // Closing the panel is this page's way of saying "that deal is closed now". Under the lock
    // `writeDeal` rejects before it sends anything and before any toast of its own, so a
    // fire-and-forget close dismissed the panel on a write the server never saw — the deal stayed
    // open, and nothing on screen said so.
    let releaseBulk: (v: unknown) => void = () => {};
    const defaults = api.getMockImplementation()!;
    api.mockImplementation((path: string, options?: ApiCallOptions) =>
      path === '/api/crm/deals/bulk-move'
        ? new Promise(res => { releaseBulk = res; })
        : defaults(path, options));

    renderAt('/crm/pipeline');
    await settle();
    click(byLabel('Select all lead deals'));
    setValue(byLabel<HTMLSelectElement>('Move selected deals to stage'), 'proposal');
    click(buttonByText('Apply'));
    await settle();

    click(cardTitled('Wholesale order'));
    await settle();
    expect(dialogTitle()).toBe('Wholesale order');

    click(buttonByText('Mark Won'));
    await settle();

    expect(dialogTitle()).toBe('Wholesale order');
    expect(callsWithMethod('PUT')).toHaveLength(0);
    expect(toast.error).toHaveBeenCalledWith(expect.stringContaining('A bulk update is in progress'));

    await act(async () => { releaseBulk({ ok: true, updated_ids: [5, 6] }); });
    await settle();
  });
});

describe('the body\'s own exits', () => {
  it('reports a SERVER-refused Mark Won exactly once, not once per layer', async () => {
    // `writeDeal` already toasts a failed stage PUT itself, so the caller must report only the
    // refusal `writeDeal` cannot — the bulk lock, which rejects before doing any work. Reporting
    // every rejection says the same thing twice; reporting none loses the lock refusal entirely.
    // The two are told apart by TYPE (`BulkLockError`), never by matching the message text.
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    const defaults = api.getMockImplementation()!;
    api.mockImplementation((path: string, options?: ApiCallOptions) =>
      options?.method === 'PUT'
        ? Promise.reject(new Error('boom'))
        : defaults(path, options));

    renderAt('/crm/pipeline?deal=5');
    await settle();
    click(buttonByText('Mark Won'));
    await settle();

    expect(toast.error.mock.calls).toEqual([['Failed to move deal.']]);
    consoleError.mockRestore();
  });

  it('runs Mark Won once when the button is double-clicked', async () => {
    // The body's own leave paths never reach `CollectionDetail.request()`, so they never had its
    // one-in-flight lock. `leaveVia` awaits `canLeave`, and on a CLEAN body that resolves in a
    // microtask with no dialog at all — so two clicks landing in the same task both cleared the
    // guard and both ran the action: two stage writes for one gesture (or, on the contact/company
    // links, two navigations).
    renderAt('/crm/pipeline?deal=5');
    await settle();
    const markWon = buttonByText('Mark Won')!;
    act(() => { markWon.click(); markWon.click(); });
    await settle();
    expect(callsWithMethod('PUT')).toHaveLength(1);
  });
});

describe('‹ › record navigation', () => {
  it('follows the board\'s column-major order, not the order deals arrived in', async () => {
    // `navOrder` is supplied by the page (the board runs no `useCollectionState` for the layer to
    // read), so nothing else proves it is actually wired through. The fixture arrives
    // qualified-first while the BOARD renders lead first, which is exactly the case where the two
    // orders disagree — and where `[]` (arrows dead) and `deals.map(d => d.id)` (array order) both
    // give different answers from the right one.
    const twoStages = [
      deal(8, { title: 'Qualified deal', stage: 'qualified' }),
      deal(9, { title: 'Lead deal', stage: 'lead' }),
    ];
    const defaults = api.getMockImplementation()!;
    api.mockImplementation((path: string, options?: ApiCallOptions) =>
      path === '/api/crm/deals' ? Promise.resolve({ deals: twoStages }) : defaults(path, options));

    renderAt('/crm/pipeline?deal=8');
    await settle();
    expect(dialogTitle()).toBe('Qualified deal');

    // 'lead' precedes 'qualified' in STAGE_ORDER, so the lead deal is index 0 — reachable only
    // BACKWARDS from here, the opposite of what the array order would say.
    click(byLabel('Previous record'));
    await settle();
    expect(dialogTitle()).toBe('Lead deal');
    expect(byLabel<HTMLButtonElement>('Previous record')!.disabled).toBe(true);

    click(byLabel('Next record'));
    await settle();
    expect(dialogTitle()).toBe('Qualified deal');
    expect(byLabel<HTMLButtonElement>('Next record')!.disabled).toBe(true);
  });
});
