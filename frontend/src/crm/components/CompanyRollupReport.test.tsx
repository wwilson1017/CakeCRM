// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act, StrictMode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import type { CrmCompanyRollup } from '../../core/types';

const apiMock = vi.fn();
vi.mock('../../core/api/client', () => ({
  api: (...args: unknown[]) => apiMock(...args),
  ApiError: class extends Error {},
}));
vi.mock('./CompanyTimeline', () => ({
  CompanyTimeline: (p: { includeArchived: boolean }) =>
    <div data-testid="timeline">{`timeline archived=${p.includeArchived}`}</div>,
}));
vi.mock('../useUsers', () => ({ useUsers: () => ({ nameFor: () => 'Ada' }) }));

let container: HTMLDivElement;
let root: Root;

const COMPANY = {
  id: 7, name: 'Acme', domain: '', industry: '', phone: '', address: '', notes: '',
  source: '', status: 'active', created_at: '2026-01-01T00:00:00+00:00',
  updated_at: '2026-01-01T00:00:00+00:00',
};

function deal(over: Record<string, unknown> = {}) {
  return {
    id: 1, contact_id: null, title: 'Deal one', stage: 'lead', value: 100,
    expected_close_date: '', probability: 0, currency: 'USD', notes: '',
    created_at: '2026-01-01T00:00:00+00:00', updated_at: '2026-01-01T00:00:00+00:00',
    company_id: 7, lost_reason: '', archived_at: null,
    activities: [], activities_truncated: false, custom_fields: [],
    tasks: [], tasks_truncated: false, last_activity_at: null,
    ...over,
  };
}

function contact(over: Record<string, unknown> = {}) {
  return {
    id: 1, name: 'Ada', email: '', phone: '', company: '', title: '', source: '',
    status: 'active', tags: '', notes: '',
    created_at: '2026-01-01T00:00:00+00:00', updated_at: '2026-01-01T00:00:00+00:00',
    company_id: 7, activities: [], activities_truncated: false, custom_fields: [],
    ...over,
  };
}

function rollup(over: Partial<CrmCompanyRollup> = {}): CrmCompanyRollup {
  return {
    company: COMPANY,
    company_custom_fields: [],
    summary: { open_deal_count: 0, open_deal_value: 0, open_deal_currency: 'USD', contact_count: 0 },
    contacts: [], deals: [], contacts_truncated: false, deals_truncated: false,
    ...over,
  } as CrmCompanyRollup;
}

async function settle(rounds = 8): Promise<void> {
  for (let i = 0; i < rounds; i++) await act(async () => { await Promise.resolve(); });
}

async function mount(): Promise<void> {
  const { CompanyRollupReport } = await import('./CompanyRollupReport');
  act(() => {
    root.render(
      <StrictMode>
        <MemoryRouter>
          <CompanyRollupReport companyId={7} />
        </MemoryRouter>
      </StrictMode>,
    );
  });
  await settle();
}

const text = () => container.textContent ?? '';
const buttonSaying = (label: string) =>
  Array.from(container.querySelectorAll('button')).find(b => b.textContent?.trim() === label);
const expanders = () =>
  Array.from(container.querySelectorAll<HTMLButtonElement>('button[aria-expanded]'));
const urls = () => apiMock.mock.calls.map(c => String(c[0]));

beforeEach(() => {
  apiMock.mockReset();
  vi.stubGlobal('matchMedia', () => ({
    matches: false, addEventListener: () => {}, removeEventListener: () => {},
  }));
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

describe('CompanyRollupReport', () => {
  it('renders the headline numbers from the server aggregate, not from the row lists', async () => {
    // The whole reason `summary` exists: the child lists are capped, so reducing THEM would
    // let a truncated page understate an account — and with archived deals included, could
    // make the open-deal count go DOWN as more history was requested.
    apiMock.mockResolvedValue(rollup({
      summary: { open_deal_count: 12, open_deal_value: 48000, open_deal_currency: 'USD', contact_count: 5 },
      deals: [deal()] as never,
    }));
    await mount();
    expect(text()).toContain('12');
    expect(text()).toContain('$48,000');
    expect(text()).toContain('5');
  });

  it('refuses to state one total when the open deals span several currencies', async () => {
    // `deals.currency` is user-writable, so a sum across currencies is a false number.
    // The server sends open_deal_currency = null in that case; the chip must not render
    // the sum as one figure.
    apiMock.mockResolvedValue(rollup({
      summary: {
        open_deal_count: 2, open_deal_value: 2000, open_deal_currency: null, contact_count: 0,
      },
    }));
    await mount();
    expect(text()).toContain('Mixed currencies');
    expect(text()).not.toContain('2,000');
  });

  it('renders each deal in its own currency, not a hardcoded dollar sign', async () => {
    apiMock.mockResolvedValue(rollup({
      summary: {
        open_deal_count: 1, open_deal_value: 1000, open_deal_currency: 'EUR', contact_count: 0,
      },
      deals: [deal({ id: 1, title: 'Euro deal', value: 1000, currency: 'EUR' })] as never,
    }));
    await mount();
    expect(text()).not.toContain('$1,000');
    expect(text()).toContain('€');
  });

  it('falls back to the raw code rather than blanking on an invalid currency', async () => {
    // Intl throws a RangeError on an unknown code, and the field is free text.
    apiMock.mockResolvedValue(rollup({
      deals: [deal({ id: 1, title: 'Odd', value: 500, currency: 'NOTACURRENCY' })] as never,
    }));
    await mount();
    expect(text()).toContain('NOTACURRENCY 500');
  });

  it('labels the contact chip as active, because the section below lists archived ones too', async () => {
    apiMock.mockResolvedValue(rollup({
      summary: { open_deal_count: 0, open_deal_value: 0, open_deal_currency: null, contact_count: 1 },
      contacts: [contact({ id: 1 }), contact({ id: 2, name: 'Bob', status: 'archived' })] as never,
    }));
    await mount();
    expect(text()).toContain('Active contacts');
    expect(text()).toContain('Contacts (2)');
  });

  it('retries by re-fetching, not merely re-rendering', async () => {
    apiMock.mockRejectedValueOnce(new Error('boom')).mockRejectedValueOnce(new Error('boom'));
    await mount();
    expect(text()).toContain("Couldn't load this company report");
    const before = apiMock.mock.calls.length;
    apiMock.mockResolvedValue(rollup());
    act(() => { buttonSaying('Retry')!.click(); });
    await settle();
    expect(apiMock.mock.calls.length).toBeGreaterThan(before);
    expect(text()).toContain('Acme');
  });

  it('lists archived deals after live ones and marks them', async () => {
    apiMock.mockResolvedValue(rollup({
      deals: [
        deal({ id: 1, title: 'Live deal' }),
        deal({ id: 2, title: 'Old deal', archived_at: '2026-02-01T00:00:00+00:00' }),
      ] as never,
    }));
    await mount();
    const body = text();
    expect(body.indexOf('Live deal')).toBeLessThan(body.indexOf('Old deal'));
    expect(body).toContain('archived');
  });

  it('shows every custom field on an expanded row, including the unset ones', async () => {
    // The issue's load-bearing requirement. A values-only payload cannot express an unset
    // field, which is why the server joins the definitions in.
    apiMock.mockResolvedValue(rollup({
      deals: [deal({
        custom_fields: [
          { field_key: 'region', name: 'Region', field_type: 'text', value: 'South' },
          { field_key: 'tier', name: 'Tier', field_type: 'text', value: null },
        ],
      })] as never,
    }));
    await mount();
    expect(text()).not.toContain('Region');
    act(() => { expanders()[0].click(); });
    await settle();
    expect(text()).toContain('Region');
    expect(text()).toContain('South');
    expect(text()).toContain('Tier');
  });

  it('shows a derived value together with the time it was derived', async () => {
    // A lead score or a touch count is only as trustworthy as how recently it was computed,
    // and both timestamps ride the payload already — omitting them made "every field" false.
    apiMock.mockResolvedValue(rollup({
      deals: [deal({
        id: 1, lead_score: 72, lead_score_at: '2026-03-01T10:00:00+00:00',
        ai_touch_count: 4, ai_touch_count_at: '2026-03-02T10:00:00+00:00',
        ai_touch_evidence_count: 9,
      })] as never,
    }));
    await mount();
    act(() => { expanders()[0].click(); });
    await settle();
    expect(text()).toContain('Lead score at');
    expect(text()).toContain('AI touches at');
    expect(text()).toContain('AI evidence lines');
  });

  it('expands a row without issuing any request', async () => {
    // Everything an expanded row needs rides the rollup payload, batched server-side. This
    // is what makes "Expand all" safe; per-row fetches would make one click ~150 requests.
    apiMock.mockResolvedValue(rollup({ deals: [deal(), deal({ id: 2, title: 'Deal two' })] as never }));
    await mount();
    const before = apiMock.mock.calls.length;
    act(() => { buttonSaying('Expand all')!.click(); });
    await settle();
    expect(text()).toContain('Deal ID');
    expect(apiMock.mock.calls.length).toBe(before);
  });

  it('collapses every row from one control', async () => {
    apiMock.mockResolvedValue(rollup({ deals: [deal(), deal({ id: 2, title: 'Deal two' })] as never }));
    await mount();
    act(() => { buttonSaying('Expand all')!.click(); });
    await settle();
    // `.every()` is vacuously true on an empty list, so a render that produced no rows at
    // all would pass both assertions below. Pin the count first.
    expect(expanders()).toHaveLength(2);
    expect(expanders().every(b => b.getAttribute('aria-expanded') === 'true')).toBe(true);
    act(() => { buttonSaying('Collapse all')!.click(); });
    await settle();
    expect(expanders().every(b => b.getAttribute('aria-expanded') === 'false')).toBe(true);
  });

  it('withholds Expand all above the ceiling but keeps per-row expansion working', async () => {
    const many = Array.from({ length: 51 }, (_, i) => deal({ id: i + 1, title: `Deal ${i + 1}` }));
    apiMock.mockResolvedValue(rollup({ deals: many as never }));
    await mount();
    expect(buttonSaying('Expand all')).toBeUndefined();
    act(() => { expanders()[0].click(); });
    await settle();
    expect(expanders()[0].getAttribute('aria-expanded')).toBe('true');
    expect(buttonSaying('Collapse all')).toBeDefined();
  });

  it('states each truncation separately, and only when its own flag is set', async () => {
    apiMock.mockResolvedValue(rollup({
      deals: [deal()] as never, contacts: [contact()] as never, deals_truncated: true,
    }));
    await mount();
    expect(text()).toContain('Showing the first 1 deals on this company');
    expect(text()).not.toContain('contacts on this company');
  });

  it('states a per-record truncation inside the row it belongs to', async () => {
    // Distinct from the section notice above it: this is the only signal that ONE deal's own
    // activity or task list was capped, and it renders only inside the expanded row.
    apiMock.mockResolvedValue(rollup({
      deals: [
        deal({ id: 1, title: 'Busy', activities_truncated: true, tasks_truncated: true }),
        deal({ id: 2, title: 'Quiet' }),
      ] as never,
    }));
    await mount();
    act(() => { expanders()[0].click(); });
    await settle();
    expect(text()).toContain('Only the most recent activity on this record is shown');
    expect(text()).toContain('Only the first open tasks on this deal are shown');

    act(() => { expanders()[0].click(); });
    act(() => { expanders()[1].click(); });
    await settle();
    expect(text()).not.toContain('Only the most recent activity on this record is shown');
    expect(text()).not.toContain('Only the first open tasks on this deal are shown');
  });

  it('refetches with include_archived and passes the flag to the timeline', async () => {
    apiMock.mockResolvedValue(rollup());
    await mount();
    expect(text()).toContain('timeline archived=false');
    const box = container.querySelector('input[type="checkbox"]') as HTMLInputElement;
    act(() => { box.click(); });
    await settle();
    expect(urls().some(u => u.includes('include_archived=true'))).toBe(true);
    expect(text()).toContain('timeline archived=true');
  });

  it('keeps the report on screen while the archive toggle reloads it', async () => {
    // Blanking to a bare loading line unmounts every section, the document collapses, and
    // the browser clamps scrollY to 0 — so ticking the checkbox threw the reader back to the
    // top of an account they were reading halfway down.
    let releaseSecond!: (v: unknown) => void;
    apiMock.mockImplementation((url: string) =>
      String(url).includes('include_archived=true')
        ? new Promise(r => { releaseSecond = r; })
        : Promise.resolve(rollup({ deals: [deal({ id: 1, title: 'Live deal' })] as never })));
    await mount();
    expect(text()).toContain('Live deal');

    const box = container.querySelector('input[type="checkbox"]') as HTMLInputElement;
    act(() => { box.click(); });
    await settle();
    expect(text()).toContain('Live deal');
    expect(text()).toContain('refreshing');
    expect(text()).not.toContain('Loading company report');

    await act(async () => { releaseSecond(rollup({ deals: [deal({ id: 2, title: 'Old deal' })] as never })); });
    await settle();
    expect(text()).toContain('Old deal');
    expect(text()).not.toContain('refreshing');
  });

  it('falls through to the error card when a reload fails, rather than showing stale data', async () => {
    apiMock.mockImplementation((url: string) =>
      String(url).includes('include_archived=true')
        ? Promise.reject(new Error('boom'))
        : Promise.resolve(rollup({ deals: [deal({ id: 1, title: 'Live deal' })] as never })));
    await mount();
    const box = container.querySelector('input[type="checkbox"]') as HTMLInputElement;
    act(() => { box.click(); });
    await settle();
    expect(text()).toContain("Couldn't load this company report");
    expect(text()).not.toContain('Live deal');
  });

  it('hides Collapse all when the open rows are no longer on screen', async () => {
    // Expanding an archived deal and then switching the filter off leaves its id in the
    // Set, which would otherwise show a control with nothing to collapse.
    apiMock.mockImplementation((url: string) =>
      String(url).includes('include_archived=true')
        ? Promise.resolve(rollup({
            deals: [deal({ id: 9, title: 'Old', archived_at: '2026-02-01T00:00:00+00:00' })] as never,
          }))
        : Promise.resolve(rollup({ deals: [] })));
    await mount();
    const box = container.querySelector('input[type="checkbox"]') as HTMLInputElement;
    act(() => { box.click(); });
    await settle();
    act(() => { expanders()[0].click(); });
    await settle();
    expect(buttonSaying('Collapse all')).toBeDefined();

    act(() => { box.click(); });
    await settle();
    expect(buttonSaying('Collapse all')).toBeUndefined();
  });

  it('shows the loading state before the first response rather than an empty report', async () => {
    apiMock.mockReturnValue(new Promise(() => {}));
    await mount();
    expect(text()).toContain('Loading company report…');
  });
});
