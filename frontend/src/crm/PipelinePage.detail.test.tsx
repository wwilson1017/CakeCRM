// @vitest-environment jsdom
//
// The page-level contracts of the DETAIL PANEL and the write path behind it (issue #75) — the
// ones that exist only where the board, the panel and `writeDeal` meet, so neither
// `DealDetailBody.test.tsx` nor the layer's own suites can see them:
//
//  • `writeDeal`'s stage-vs-fields split. Every stage-specific step is conditional on the patch
//    actually CARRYING a stage; drop that condition and a fields-only save starts painting
//    optimistic restages and toasting "Failed to move deal." at someone who moved nothing. The
//    REVERT is deliberately not one of them — the last writer to fail owns the reconciliation,
//    whatever it happened to be writing.
//
//  • The bulk lock REJECTING rather than returning quietly, which is the whole difference between
//    "your edit was refused, try again" and losing what someone just typed — and the panel
//    staying OPEN when a close is refused, since closing is this page's way of saying the deal
//    is closed.
//
//  • ‹ › walking the board's COLUMN-MAJOR order. The layer derives that from `kanban.columns`,
//    so nothing else proves the board's columns actually reach it.
//
//  • `?stage=`'s two parameter bugs, and that stripping it leaves `?deal=` alone — #145 keeps
//    that one deliberately, and the two params are handled by one effect.
//
// It lives in its own file rather than in `PipelinePage.test.tsx` for the reason
// `PipelinePage.archived.test.tsx` gives: that suite MOCKS `DealDetailBody` (its subject is
// whether the page routes a selection into the layer at all), and every assertion here goes
// through the real panel. `vi.mock` is file-scoped, so the two harnesses cannot share a file.
import { act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { CrmDeal } from '../core/types';

const api = vi.hoisted(() => vi.fn());
// `importOriginal` rather than a hand-written stub: PipelinePage imports `ApiError` from this
// module too, and a second class with the same name would break `instanceof` silently.
vi.mock('../core/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../core/api/client')>()),
  api,
}));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }));
vi.mock('../shared/toast', () => ({ toast }));

const { PipelinePage } = await import('./PipelinePage');
const { ActiveRecordProvider } = await import('./RecordContext');
const { MemoryRouter, useLocation, useNavigate } = await import('react-router-dom');
type NavigateFunction = ReturnType<typeof useNavigate>;

function deal(id: number, over: Partial<CrmDeal> = {}): CrmDeal {
  return {
    id, title: `Deal ${id}`, stage: 'lead', value: 100, notes: '', probability: 0,
    contact_id: null, company_id: null, expected_close_date: '', currency: 'USD',
    archived_at: null, created_at: '2026-08-01T00:00:00Z', updated_at: '2026-08-01T00:00:00Z',
    ...over,
  };
}

const BOARD = [deal(5, { title: 'Wholesale order' }), deal(6, { title: 'Retail order' })];

// #59 turned the board's one GET into a keyset sweep; every fixture here is well under one page,
// so a load is exactly one request at this page-0 URL.
const BOARD_PATH = '/api/crm/deals?sort=id&limit=501';

interface ApiCallOptions { method?: string; body?: string }

let container: HTMLDivElement;
let root: Root;

/** Probe state lives on ONE object, written from an effect. Assigning to a module-level variable
 *  during render is what `react-hooks/globals` forbids, and would be a real render-phase side
 *  effect besides. */
const probe: { search: string; go: NavigateFunction } = { search: '', go: () => {} };

function LocationProbe() {
  const { search } = useLocation();
  // `go` lets a test change the URL WITHOUT remounting the page — the only way to exercise a ref
  // that survives across navigations.
  const navigate = useNavigate();
  useEffect(() => {
    probe.search = search;
    probe.go = navigate;
  });
  return null;
}

function defaultRoutes(path: string): Promise<unknown> | undefined {
  if (path.startsWith(BOARD_PATH)) return Promise.resolve({ deals: BOARD });
  if (/^\/api\/crm\/deals\/\d+$/.test(path)) {
    const id = Number(path.split('/').pop());
    return Promise.resolve({
      ...deal(id, { title: `Deal ${id}` }),
      activity: [{ id: 900 + id, activity: 'call', note: 'Left a voicemail', created_at: '2026-08-19T00:00:00Z' }],
    });
  }
  if (path.startsWith('/api/crm/provenance/')) return Promise.resolve({ provenance: [] });
  if (path.startsWith('/api/crm/chatter/')) return Promise.resolve({ notes: [] });
  if (path.startsWith('/api/crm/contacts')) return Promise.resolve({ contacts: [] });
  if (path.startsWith('/api/crm/companies')) return Promise.resolve({ companies: [] });
  if (/\/fields$/.test(path)) return Promise.resolve([]);
  if (path.startsWith('/api/users')) return Promise.resolve({ users: [] });
  return undefined;
}

beforeEach(() => {
  sessionStorage.clear();
  probe.search = '';
  window.matchMedia = ((query: string) => ({
    matches: false, media: query, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {},
    addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
  Element.prototype.scrollIntoView = vi.fn();
  api.mockReset();
  api.mockImplementation((path: string) => defaultRoutes(path) ?? Promise.resolve({}));
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

const panelTitle = () => container.querySelector('[role="dialog"] h2')?.textContent ?? null;

const click = (el: Element | null) => act(() => { (el as HTMLElement).click(); });

/** Match a button by visible text OR `aria-label` — the shell's chrome is icon buttons. */
const buttonByText = (text: string) =>
  [...container.querySelectorAll('button')]
    .find(b => b.textContent?.trim() === text || b.getAttribute('aria-label') === text) ?? null;

const byLabel = <E extends Element>(label: string) =>
  container.querySelector<E>(`[aria-label="${label}"]`);

/** Card opener: the board card is a `role="button"` div; its checkbox stops propagation itself. */
const cardTitled = (title: string) =>
  [...container.querySelectorAll<HTMLElement>('[role="button"]')]
    .find(el => el.textContent?.includes(title)) ?? null;

/** Which column a card is RENDERED in — the board's own answer, not `data.deals`' stage field. */
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

const callsWithMethod = (method: string) =>
  (api.mock.calls as [string, ApiCallOptions | undefined][])
    .filter(([, options]) => options?.method === method);

// The panel's own detail READ is not asserted here. On this page a deep-linked deal is always
// found in the board's `items` — the verdict that opens it requires a payload to have landed —
// so `CollectionDetail` never reaches for `loadById` and the body's seed path is unreachable.
// That path belongs to the hosts whose rows are summaries (the dashboard, the touches page) and
// is pinned in `components/DealDetailBody.test.tsx`.

// ── The URL contract ─────────────────────────────────────────────────────────────────────────

describe('the two deep-link params together', () => {
  it('strips ?stage= and LEAVES ?deal= alone', async () => {
    // One effect owns both keys, and it must touch only one of them. #145 keeps `?deal=` on
    // purpose — a reload reopens the deal, and the address bar is a real copy source — so a
    // strip here would not merely lose the parameter: the rewrite is itself a navigation, which
    // re-arms the very link it just resolved and fires a second board refresh.
    renderAt('/crm/pipeline?stage=lead&deal=5');
    await settle();

    expect(probe.search).toBe('?deal=5');
    expect(panelTitle()).toBe('Wholesale order');
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
    // Deleting only VALID stages left `?stage=bogus` in the address bar forever: it never
    // matches `STAGE_ORDER`, so the delete never ran. Nothing will ever scroll to a column that
    // cannot exist, so there is nothing for the parameter to be waiting on.
    renderAt('/crm/pipeline?stage=bogus');
    await settle();
    expect(probe.search).toBe('');
  });
});

// ── writeDeal's stage-vs-fields split ────────────────────────────────────────────────────────

describe('writeDeal: a fields-only save', () => {
  it('PUTs only the changed column, with no stage key at all', async () => {
    // The whole reason `writeDeal` generalised from `moveDealStage`: an inline edit rides the
    // same per-deal chain and the same sequence numbering as a drag, but it must not SEND a
    // stage. Sending one turns a title edit into a stage write, and `_classify_deal_update`
    // overrides `probability` to 100/0 inside any transaction that changes the stage.
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
    // This is the assertion that pins the `toStage !== undefined` conditionals. A fields-only
    // write painted nothing optimistically, so there is nothing to revert and nothing to
    // announce: the form keeps the draft and shows the error inline, next to what it is asking
    // the user to retry. Toasting a move failure here would report a board change that never
    // happened.
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    api.mockImplementation((path: string, options?: ApiCallOptions) =>
      options?.method === 'PUT'
        ? Promise.reject(new Error('Deal is archived'))
        : defaultRoutes(path) ?? Promise.resolve({}));

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
  it('reports a superseded stage failure that a fields-only success quietly undid', async () => {
    // The other half of the same family, and the one nothing announces. Stage write paints 'won'
    // (seq 1) and FAILS; a fields-only save (seq 2) SUCCEEDS behind it, and its response carries
    // the server's real stage — so the card slides back to 'lead' on its own, correctly. The
    // board ends up right, but the rep's drag was undone with nothing on screen to say so.
    //
    // "Superseded — leave the newer state" was written when only another stage write could
    // supersede one. A fields-only save expresses no stage intent at all, so it cannot stand in
    // for the report.
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    const pendingPuts: { resolve: (v: unknown) => void; reject: (err: unknown) => void }[] = [];
    api.mockImplementation((path: string, options?: ApiCallOptions) =>
      options?.method === 'PUT'
        ? new Promise((resolve, reject) => { pendingPuts.push({ resolve, reject }); })
        : defaultRoutes(path) ?? Promise.resolve({}));

    renderAt('/crm/pipeline?deal=5');
    await settle();

    click(buttonByText('Mark Won'));
    await settle();
    expect(stageColumn('won')!.textContent).toContain('Wholesale order');

    click(buttonByText('Edit'));
    await settle();
    setField('deal-title', 'Renamed');
    click(buttonByText('Save'));
    await settle();

    await act(async () => { pendingPuts[0].reject(new Error('stage write failed')); });
    await settle();

    // seq 2 succeeds, and the server says the deal is still in `lead`.
    await act(async () => {
      pendingPuts[1].resolve({ ...deal(5, { title: 'Renamed', stage: 'lead' }) });
    });
    await settle();

    expect(stageColumn('lead')!.textContent).toContain('Renamed');
    expect(toast.error).toHaveBeenCalledWith('Failed to move deal.');
    consoleError.mockRestore();
  });

  it('reverts the optimistic stage even though the last write carried no stage', async () => {
    // The failure the `toStage !== undefined` revert gate could not see. A stage write paints
    // 'won' (seq 1); an inline field save queues behind it on the same per-deal chain (seq 2);
    // the stage PUT fails, but by then it is SUPERSEDED so it returns before reverting —
    // correctly, since a newer op owns the board. The newer op then fails too, and it is
    // carrying no stage of its own, so gating its revert on `toStage` left the board showing a
    // stage the server never stored, silently, until the next full load.
    //
    // The rule that replaces it: the last writer to fail owns the reconciliation, whatever it
    // was writing.
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    const pendingPuts: { reject: (err: unknown) => void }[] = [];
    api.mockImplementation((path: string, options?: ApiCallOptions) =>
      options?.method === 'PUT'
        ? new Promise((_resolve, reject) => { pendingPuts.push({ reject }); })
        : defaultRoutes(path) ?? Promise.resolve({}));

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

// ── The bulk lock ────────────────────────────────────────────────────────────────────────────

/** Hold a bulk move in flight for the rest of a test, and return the release. */
function holdBulkMove() {
  let release: (v: unknown) => void = () => {};
  api.mockImplementation((path: string) =>
    path === '/api/crm/deals/bulk-move'
      ? new Promise(res => { release = res; })
      : defaultRoutes(path) ?? Promise.resolve({}));
  return () => release({ ok: true, updated: 2, updated_ids: [5, 6], errors: [] });
}

async function startBulkMove() {
  click(byLabel('Select all lead deals'));
  setValue(byLabel<HTMLSelectElement>('Move selected deals to stage'), 'proposal');
  click(buttonByText('Apply'));
  await settle();
}

describe('the bulk lock', () => {
  it('rejects an inline field save while a bulk move is in flight, rather than silently succeeding', async () => {
    // Dropping a redundant drag under the lock is invisible and fine; dropping the field edit
    // someone just typed is not. A quiet `Promise.resolve()` here would close the form on a
    // write that never happened, and the reconcile refetch would then paint the pre-edit values
    // back.
    const releaseBulk = holdBulkMove();
    renderAt('/crm/pipeline');
    await settle();
    await startBulkMove();

    click(cardTitled('Wholesale order'));
    await settle();
    expect(panelTitle()).toBe('Wholesale order');
    click(buttonByText('Edit'));
    await settle();
    setField('deal-title', 'Renamed mid-bulk');
    click(buttonByText('Save'));
    await settle();

    // Refused, said so, and kept the draft — no PUT reached the server.
    expect(callsWithMethod('PUT')).toHaveLength(0);
    expect(container.textContent).toContain('A bulk update is in progress');
    expect(container.querySelector<HTMLInputElement>('#deal-title')!.value).toBe('Renamed mid-bulk');

    await act(async () => { releaseBulk(); });
    await settle();
  });

  it('keeps the panel OPEN when Mark Won is refused, instead of reporting a close that never happened', async () => {
    // Closing the panel is this page's way of saying "that deal is closed now". Under the lock
    // `writeDeal` rejects before it sends anything and before any toast of its own, so a
    // fire-and-forget close dismissed the panel on a write the server never saw — the deal
    // stayed open, and nothing on screen said so.
    const releaseBulk = holdBulkMove();
    renderAt('/crm/pipeline');
    await settle();
    await startBulkMove();

    click(cardTitled('Wholesale order'));
    await settle();
    expect(panelTitle()).toBe('Wholesale order');

    click(buttonByText('Mark Won'));
    await settle();

    expect(panelTitle()).toBe('Wholesale order');
    expect(callsWithMethod('PUT')).toHaveLength(0);
    expect(toast.error).toHaveBeenCalledWith(expect.stringContaining('A bulk update is in progress'));

    await act(async () => { releaseBulk(); });
    await settle();
  });
});

// ── The body's own exits ─────────────────────────────────────────────────────────────────────

describe("the body's own exits", () => {
  it('reports a SERVER-refused Mark Won exactly once, not once per layer', async () => {
    // `writeDeal` already toasts a failed stage PUT itself, so the caller must report only the
    // refusal `writeDeal` cannot — the bulk lock, which rejects before doing any work. Reporting
    // every rejection says the same thing twice; reporting none loses the lock refusal entirely.
    // The two are told apart by TYPE (`BulkLockError`), never by matching the message text.
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    api.mockImplementation((path: string, options?: ApiCallOptions) =>
      options?.method === 'PUT'
        ? Promise.reject(new Error('boom'))
        : defaultRoutes(path) ?? Promise.resolve({}));

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
    // guard and both ran the action: two stage writes for one gesture.
    renderAt('/crm/pipeline?deal=5');
    await settle();
    const markWon = buttonByText('Mark Won')!;
    act(() => { (markWon as HTMLElement).click(); (markWon as HTMLElement).click(); });
    await settle();
    expect(callsWithMethod('PUT')).toHaveLength(1);
  });
});

// ── ‹ › record navigation ────────────────────────────────────────────────────────────────────

describe('‹ › record navigation', () => {
  it("follows the board's column-major order, not the order deals arrived in", async () => {
    // The layer derives the walk order from `kanban.columns`, which the page builds from the
    // stages it is RENDERING — so nothing but a page-level test proves the two are connected.
    // The fixture arrives qualified-first while the board renders lead first, which is exactly
    // the case where the two orders disagree: array order and column order give different
    // answers, and `[]` (arrows dead) gives a third.
    const twoStages = [
      deal(8, { title: 'Qualified deal', stage: 'qualified' }),
      deal(9, { title: 'Lead deal', stage: 'lead' }),
    ];
    api.mockImplementation((path: string) =>
      path.startsWith(BOARD_PATH)
        ? Promise.resolve({ deals: twoStages })
        : defaultRoutes(path) ?? Promise.resolve({}));

    renderAt('/crm/pipeline?deal=8');
    await settle();
    expect(panelTitle()).toBe('Qualified deal');

    // 'lead' precedes 'qualified' in STAGE_ORDER, so the lead deal is index 0 — reachable only
    // BACKWARDS from here, the opposite of what the array order would say.
    click(byLabel('Previous record'));
    await settle();
    expect(panelTitle()).toBe('Lead deal');
    expect(byLabel<HTMLButtonElement>('Previous record')!.disabled).toBe(true);

    click(byLabel('Next record'));
    await settle();
    expect(panelTitle()).toBe('Qualified deal');
    expect(byLabel<HTMLButtonElement>('Next record')!.disabled).toBe(true);
  });
});
