// @vitest-environment jsdom
//
// Issue #125, at the PAGE level. `DealTemperatureIcon` and `DealTemperatureCell` have their own
// suites and drive a mocked `onCycle`; what those cannot reach is the wiring in between — whether
// a click on a real board card actually reaches `writeDeal`, what patch it sends, and what
// happens when that write fails or is refused.
//
// Three things here would ship green without it, and each is invisible on screen:
//
//  - the patch carrying a `stage` as well, which would MOVE a deal as a side effect of judging it;
//  - no toast on failure. `writeDeal` deliberately toasts only for a stage write ("a fields-only
//    write announced no move, so it announces no failed one"), so this control has to speak for
//    itself and the icon's own revert is silent;
//  - a temperature write slipping past the bulk lock, which owns the board until its reconcile
//    refetch lands (#55) and would otherwise reconcile against a stage the bulk request is in the
//    middle of changing.
//
// A separate file from `PipelinePage.test.tsx` because `vi.mock` is file-scoped and this one has
// to mock the toast module — the same reason `.archived` and `.deeplink` are their own files.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

import { ActiveRecordProvider } from './RecordContext';
import type { CrmDeal } from '../core/types';

const api = vi.hoisted(() => vi.fn());
vi.mock('../core/api/client', () => ({
  api,
  ApiError: class ApiError extends Error {
    status?: number;
    detail?: string;
  },
}));

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }));
vi.mock('../shared/toast', () => ({ toast }));

vi.mock('../shared/useIsMobile', () => ({ useIsMobile: () => false }));

vi.mock('./components/DealDetailBody', () => ({
  DealDetailBody: ({ deal }: { deal: { id: number; title: string } }) => (
    <div data-testid="deal-sheet" data-deal-id={deal.id}>{deal.title}</div>
  ),
}));

const { PipelinePage } = await import('./PipelinePage');

function deal(over: Partial<CrmDeal> & { id: number }): CrmDeal {
  return {
    contact_id: null, company_id: null, title: `Deal ${over.id}`, stage: 'lead',
    value: 100, notes: '', expected_close_date: '', probability: 50, currency: 'USD',
    created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    ...over,
  };
}

const DEALS: CrmDeal[] = [
  deal({ id: 1, title: 'Alpha contract', stage: 'lead', deal_temperature: null }),
  deal({ id: 2, title: 'Beta renewal', stage: 'qualified', deal_temperature: 'hot' }),
];

let container: HTMLDivElement;
let root: Root;

async function mount(): Promise<void> {
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={['/crm/pipeline']}>
        <ActiveRecordProvider>
          <PipelinePage />
        </ActiveRecordProvider>
      </MemoryRouter>,
    );
  });
  await act(async () => { await Promise.resolve(); });
}

/** The card control for one deal, found by the label the icon publishes for it. */
function temperatureButton(dealTitle: string): HTMLButtonElement | null {
  const card = [...document.querySelectorAll('[role="button"]')]
    .find(el => (el.textContent ?? '').includes(dealTitle));
  return card?.querySelector('button[aria-label^="Deal temperature"]') ?? null;
}

const dealPut = (): Array<[string, { body: string }]> =>
  api.mock.calls.filter(
    c => /^\/api\/crm\/deals\/\d+$/.test(String(c[0]))
      && (c[1] as { method?: string } | undefined)?.method === 'PUT',
  ) as Array<[string, { body: string }]>;

beforeEach(() => {
  sessionStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
  api.mockReset();
  toast.error.mockReset();
  api.mockImplementation((url: string) => {
    if (url === '/api/users') return Promise.resolve({ users: [] });
    if (url.startsWith('/api/crm/deals?sort=id')) return Promise.resolve({ deals: DEALS });
    if (/^\/api\/crm\/deals\/\d+$/.test(url)) return Promise.resolve(DEALS[0]);
    return Promise.resolve({});
  });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe('cycling a deal temperature from a board card', () => {
  it('PUTs the new tier alone, carrying no stage', async () => {
    await mount();
    await act(async () => { temperatureButton('Alpha contract')!.click(); });
    await act(async () => { await Promise.resolve(); });

    const calls = dealPut();
    expect(calls).toHaveLength(1);
    const [url, init] = calls[0];
    expect(url).toBe('/api/crm/deals/1');
    // An untriaged deal steps straight to hot, and the patch is that column and nothing else —
    // a `stage` in here would move the deal as a side effect of judging it.
    expect(JSON.parse(init.body)).toEqual({ deal_temperature: 'hot' });
  });

  it('steps an already-hot deal onward rather than re-sending the same value', async () => {
    await mount();
    await act(async () => { temperatureButton('Beta renewal')!.click(); });
    await act(async () => { await Promise.resolve(); });
    expect(JSON.parse(dealPut()[0][1].body))
      .toEqual({ deal_temperature: 'warm' });
  });

  it('toasts when the write fails, because the icon puts the glyph back silently', async () => {
    api.mockImplementation((url: string, init?: { method?: string }) => {
      if (url === '/api/users') return Promise.resolve({ users: [] });
      if (url.startsWith('/api/crm/deals?sort=id')) return Promise.resolve({ deals: DEALS });
      if (/^\/api\/crm\/deals\/\d+$/.test(url) && init?.method === 'PUT') {
        return Promise.reject(new Error('boom'));
      }
      return Promise.resolve({});
    });
    await mount();
    await act(async () => { temperatureButton('Alpha contract')!.click(); });
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await Promise.resolve(); });

    expect(toast.error).toHaveBeenCalledWith('Failed to update deal temperature.');
  });

  it('writes nothing while a bulk move owns the board', async () => {
    // The bulk lock is held from the click until the reconcile refetch settles (#55). A
    // temperature write started inside it could reconcile against the stage the bulk request is
    // still changing, clobbering server truth the board is about to fetch.
    let releaseBulk: (v: unknown) => void = () => {};
    api.mockImplementation((url: string) => {
      if (url === '/api/users') return Promise.resolve({ users: [] });
      if (url.startsWith('/api/crm/deals?sort=id')) return Promise.resolve({ deals: DEALS });
      if (url === '/api/crm/deals/bulk-move') return new Promise(res => { releaseBulk = res; });
      return Promise.resolve({});
    });
    await mount();

    const checkbox = document.querySelector<HTMLInputElement>(
      'input[aria-label="Select Alpha contract"]',
    )!;
    await act(async () => { checkbox.click(); });
    const select = document.querySelector<HTMLSelectElement>(
      'select[aria-label="Move selected deals to stage"]',
    )!;
    await act(async () => {
      select.value = 'proposal';
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });
    const apply = [...document.querySelectorAll('button')]
      .find(b => (b.textContent ?? '').trim() === 'Apply')!;
    await act(async () => { apply.click(); });   // lock taken; the request never settles

    const before = dealPut().length;
    await act(async () => { temperatureButton('Beta renewal')?.click(); });
    await act(async () => { await Promise.resolve(); });
    expect(dealPut().length).toBe(before);

    releaseBulk({ ok: true, updated: 1, updated_ids: [1], errors: [] });
    await act(async () => { await Promise.resolve(); });
  });
});
