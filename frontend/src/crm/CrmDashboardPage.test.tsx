// @vitest-environment jsdom
//
// The dashboard's half of the deal-detail migration.
//
// This page opens deals from THREE unrelated queries — `top_deals`, the analytics "Needs a touch"
// list, and the weekly-touches card — and only the first of those is ever in `items`. Everything
// else reaches the panel through `DEAL_DETAIL_CONFIG.loadById`, so the fetch is not an edge case
// here the way it is on the board: it is the ordinary path. That is what these tests pin, along
// with the one deliberate DIFFERENCE from the pipeline — `stageWritable` is unconditional here,
// because there is no board for a deal to be off and "Needs a touch" is a real place to close a
// deal from. Wire it to `top_deals` membership by reflex and the close buttons vanish from
// precisely the list that exists to prompt a close.
//
// The other two are the writes that used to be someone else's job: `saveDeal` (its PUT plus the
// reload that is this page's entire reconciliation — there is no board to patch a row into) and
// `openDeal`, which no longer pre-fetches or toasts and now relies on the layer's own
// "Record unavailable · Retry" panel.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { CrmAnalytics, CrmDashboard, CrmDeal } from '../core/types';

const api = vi.hoisted(() => vi.fn());
vi.mock('../core/api/client', () => ({
  api,
  ApiError: class ApiError extends Error {
    status: number;
    constructor(message: string, status = 500) { super(message); this.status = status; }
  },
}));

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }));
vi.mock('../shared/toast', () => ({ toast }));

const { CrmDashboardPage } = await import('./CrmDashboardPage');
const { ActiveRecordProvider } = await import('./RecordContext');
const { AuthProvider } = await import('../core/auth/AuthContext');

// ── Fixtures ─────────────────────────────────────────────────────────────────────────────────

function deal(id: number, over: Partial<CrmDeal> = {}): CrmDeal {
  return {
    id, title: `Deal ${id}`, stage: 'lead', value: 100, notes: '',
    contact_id: null, company_id: null, expected_close_date: '', probability: 0,
    currency: 'USD', created_at: '', updated_at: '', ...over,
  };
}

/** Deal 5 is the ONLY row this page holds canonically; 9 exists solely on the stale list. */
const TOP_DEAL = deal(5, { title: 'Wholesale order', contact_name: 'Dana Reyes' });

const DASHBOARD: CrmDashboard = {
  total_contacts: 4,
  total_companies: 2,
  contacts_by_status: { active: 4 },
  pipeline_by_stage: [{ stage: 'lead', count: 1, total_value: 100 }],
  total_pipeline_value: 100,
  overdue_tasks: 0,
  pending_tasks: 1,
  recent_activity: [],
  top_deals: [TOP_DEAL],
};

const ANALYTICS: CrmAnalytics = {
  window_days: 30,
  stale_days: 14,
  win_loss: {
    deals_won: 1, deals_lost: 1, open_deals: 1, win_rate_pct: 50,
    avg_won_deal_size: 100, avg_open_deal_size: 100, avg_days_to_close: 10,
    total_pipeline_value: 100,
  },
  activity: { daily: [], by_type: [], total: 0 },
  per_rep: [],
  aging: {
    buckets: [],
    stale_count: 1,
    // Deliberately NOT in `top_deals` — this is the row that can only reach the panel via
    // `loadById`, and the one whose close buttons the `stageWritable` rule decides.
    stale_deals: [{
      id: 9, title: 'Idle order', value: 250, stage: 'proposal',
      contact_name: null, company_name: 'Northwind', days_since_touch: 40, age_days: 90,
    }],
  },
};

interface ApiCallOptions { method?: string; body?: string }

/** Route-based mock, so the composed children (chatter, custom fields, provenance) run for real. */
function routeApi(path: string, options?: ApiCallOptions) {
  const method = options?.method ?? 'GET';
  if (path === '/api/crm/dashboard') return Promise.resolve(DASHBOARD);
  if (path === '/api/crm/analytics') return Promise.resolve(ANALYTICS);
  if (path.startsWith('/api/crm/dashboard/weekly-touches')) {
    // computed_deals 0 is the zero-AI-keys state: the card renders nothing, which keeps this
    // suite about the detail panel rather than about #76's KPI.
    return Promise.resolve({
      window: { start: '', end: '', label: '', custom: false },
      deals: [], total_touches: 0, total_open_deals: 1, computed_deals: 0,
    });
  }
  if (method === 'GET' && /^\/api\/crm\/deals\/\d+$/.test(path)) {
    const id = Number(path.split('/').pop());
    // `stage: 'proposal'` matters: `closable` is `stageWritable && OPEN_STAGES.includes(stage)`,
    // so a fetched deal has to be OPEN for the Mark Lost assertion to be about `stageWritable`.
    return Promise.resolve({ ...deal(id, { title: `Fetched ${id}`, stage: 'proposal' }), activity: [] });
  }
  if (path.startsWith('/api/crm/provenance/')) return Promise.resolve({ provenance: [] });
  if (path.startsWith('/api/crm/chatter/')) return Promise.resolve({ notes: [] });
  if (path.startsWith('/api/crm/contacts')) return Promise.resolve({ contacts: [] });
  if (path.startsWith('/api/crm/companies')) return Promise.resolve({ companies: [] });
  if (/\/fields$/.test(path)) return Promise.resolve([]);
  if (path.startsWith('/api/users')) return Promise.resolve({ users: [] });
  return Promise.resolve({});
}

// ── Harness ──────────────────────────────────────────────────────────────────────────────────

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: () => ({ matches: false, addEventListener: () => {}, removeEventListener: () => {} }),
  });
  Element.prototype.scrollIntoView = vi.fn();
  api.mockReset();
  api.mockImplementation(routeApi);
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

function render() {
  act(() => {
    root.render(
      <MemoryRouter initialEntries={['/crm']}>
        {/* #130's Today panel reads the signed-in account for its Mine/Everyone scope. Provided
            rather than mocked: with no token in sessionStorage the provider settles without a
            request, and the panel renders its Everyone view. */}
        <AuthProvider>
          <ActiveRecordProvider>
            <CrmDashboardPage />
          </ActiveRecordProvider>
        </AuthProvider>
      </MemoryRouter>,
    );
  });
}

async function settle(rounds = 8) {
  for (let i = 0; i < rounds; i++) {
    await act(async () => { await Promise.resolve(); });
  }
}

const click = (el: Element | null) => act(() => { (el as HTMLElement).click(); });

const dialogTitle = () => container.querySelector('[role="dialog"] h2')?.textContent ?? null;

const buttonByText = (text: string) =>
  [...container.querySelectorAll('button')].find(b => b.textContent?.trim() === text) ?? null;

/** The clickable rows on this page are plain divs with onClick, so find them by their text. */
const rowContaining = (text: string) =>
  [...container.querySelectorAll<HTMLElement>('div[style]')]
    .reverse()
    .find(el => el.textContent?.includes(text) && !el.querySelector('[role="dialog"]')) ?? null;

/** React tracks the DOM value node-side, so a bare `el.value = x` is swallowed as a no-op. */
function setField(id: string, value: string) {
  const el = container.querySelector<HTMLInputElement>(`#${id}`)!;
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  });
}

const callsTo = (path: string) =>
  (api.mock.calls as [string, ApiCallOptions | undefined][]).filter(([p]) => p === path);
const callsWithMethod = (method: string) =>
  (api.mock.calls as [string, ApiCallOptions | undefined][])
    .filter(([, options]) => options?.method === method);

// ── Opening a deal ───────────────────────────────────────────────────────────────────────────

describe('opening a deal', () => {
  it('opens a top-deals row from the row the page already holds', async () => {
    render();
    await settle();
    expect(dialogTitle()).toBeNull();

    click(rowContaining('Wholesale order'));
    await settle();
    // The canonical row wins over the detail fetch, which returns a different title on purpose.
    expect(dialogTitle()).toBe('Wholesale order');
  });

  it('resolves a "Needs a touch" row through loadById, since it is not in top_deals', async () => {
    // The dashboard's stale list carries a summary, not a deal — every field the panel needs
    // comes from `GET /api/crm/deals/:id`. Without `loadById` this row opens nothing at all.
    render();
    await settle();
    click(rowContaining('Idle order'));
    await settle();
    expect(dialogTitle()).toBe('Fetched 9');
  });

  it('offers Mark Lost on a deal that is NOT in top_deals — the pipeline rule inverted', async () => {
    // `stageWritable` is unconditional here by design. Copy the board's `stageWritable={onBoard}`
    // across and the close buttons disappear from the very list that exists to prompt a close.
    render();
    await settle();
    click(rowContaining('Idle order'));
    await settle();
    expect(buttonByText('Mark Lost')).not.toBeNull();
    expect(buttonByText('Mark Won')).not.toBeNull();
  });
});

// ── Saving ───────────────────────────────────────────────────────────────────────────────────

describe('the inline save', () => {
  it('PUTs the patch to that deal and then reloads the page behind it', async () => {
    // There is no board here to reconcile a row into, so the reload IS the reconciliation: skip it
    // and the panel shows the new value while every panel around it keeps the old one.
    render();
    await settle();
    const dashboardLoadsBefore = callsTo('/api/crm/dashboard').length;

    click(rowContaining('Wholesale order'));
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
    expect(callsTo('/api/crm/dashboard').length).toBe(dashboardLoadsBefore + 1);
    expect(callsTo('/api/crm/analytics').length).toBeGreaterThan(1);
  });

  it('shows the saved value immediately, without waiting for the reload to answer', async () => {
    // `DealDetailBody`'s `view` deliberately lets the HOST row win over its own detail fetch for a
    // deal the host holds canonically — and a `top_deals` row is exactly that. So the reload is not
    // enough on its own: until it lands, the panel repaints the PRE-SAVE row the moment edit mode
    // closes, and if the reload never lands it does so permanently. The PUT's own response is what
    // closes that window.
    //
    // The reload here never resolves, which is what makes the assertion about the patch and not
    // about the refetch.
    let dashboardLoads = 0;
    api.mockImplementation((path: string, options?: ApiCallOptions) => {
      if (path === '/api/crm/dashboard') {
        dashboardLoads += 1;
        return dashboardLoads === 1 ? Promise.resolve(DASHBOARD) : new Promise(() => {});
      }
      if (options?.method === 'PUT') return Promise.resolve({ ...TOP_DEAL, title: 'Renamed' });
      return routeApi(path, options);
    });

    render();
    await settle();
    click(rowContaining('Wholesale order'));
    await settle();
    click(buttonByText('Edit'));
    await settle();
    setField('deal-title', 'Renamed');
    click(buttonByText('Save'));
    await settle();

    // The shell's title comes from the row the host holds (`items`), so this asserts the patch
    // itself rather than anything the body could have kept from its own draft.
    expect(dialogTitle()).toBe('Renamed');
    expect(container.querySelector('[role="dialog"] h3')?.textContent).toBe('Renamed');
  });
});
