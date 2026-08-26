// @vitest-environment jsdom
//
// A mount-level smoke test for the rewritten board (#74). It is deliberately about the seams
// the type checker cannot see: that the page mounts at all against the real collection layer,
// that the render-phase deep-link reset settles instead of looping, that stage visibility
// removes a column AND its deals from every derived number, and that Board/List both render.
// The pure modules have their own suites; this one exists because a full rewrite of a page
// with no test at all is how a render-phase loop or a bad config reaches production green.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

import type { CrmDeal } from '../core/types';

const api = vi.hoisted(() => vi.fn());
vi.mock('../core/api/client', () => ({
  api,
  ApiError: class ApiError extends Error {
    status?: number;
    detail?: string;
  },
}));

// The board is desktop-only for selection; pin it so the bulk affordances render.
vi.mock('../shared/useIsMobile', () => ({ useIsMobile: () => false }));

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
  deal({ id: 1, title: 'Alpha contract', stage: 'lead', value: 1000, lead_score: 80 }),
  deal({ id: 2, title: 'Beta renewal', stage: 'qualified', value: 2000, lead_score: 40 }),
  deal({ id: 3, title: 'Gamma expansion', stage: 'won', value: 5000 }),
];

let container: HTMLDivElement;
let root: Root;

function route(path: string) {
  return (
    <MemoryRouter initialEntries={[path]}>
      <PipelinePage />
    </MemoryRouter>
  );
}

/** Mount and flush the queueMicrotask-scheduled initial load. */
async function mount(path = '/crm/pipeline'): Promise<void> {
  await act(async () => {
    root.render(route(path));
  });
  await act(async () => { await Promise.resolve(); });
}

const text = () => document.body.textContent ?? '';
const buttonByText = (label: string) =>
  [...document.querySelectorAll('button')].find(b => (b.textContent ?? '').trim() === label);

beforeEach(() => {
  sessionStorage.clear();
  // jsdom implements no layout, so Element.scrollIntoView does not exist — the deep-link
  // and chip-bar paths both call it. Stubbing it keeps the test about the page's logic
  // rather than about jsdom's gaps.
  Element.prototype.scrollIntoView = vi.fn();
  api.mockReset();
  api.mockImplementation((url: string) => {
    if (url === '/api/users') return Promise.resolve({ users: [] });
    if (url === '/api/crm/deals') return Promise.resolve({ deals: DEALS });
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

describe('mounting', () => {
  it('renders the board with every deal and an open-pipeline total', async () => {
    await mount();
    expect(text()).toContain('Pipeline');
    expect(text()).toContain('Alpha contract');
    expect(text()).toContain('Beta renewal');
    expect(text()).toContain('Gamma expansion');
    // Open stages only: 1000 + 2000, with the won deal's 5000 excluded.
    expect(text()).toContain('$3.0K open'); // formatNumber abbreviates thousands
    expect(text()).toContain('2 open deals');
  });

  it('exposes both views through the switcher', async () => {
    await mount();
    expect(buttonByText('Board')).toBeDefined();
    expect(buttonByText('List')).toBeDefined();
  });

  it('renders the list view with its columns when switched', async () => {
    await mount();
    const list = buttonByText('List');
    expect(list).toBeDefined();
    await act(async () => { list!.click(); });
    // Column headers from pipelineListColumns.
    expect(text()).toContain('Deal');
    expect(text()).toContain('Stage');
    expect(text()).toContain('Value');
    expect(text()).toContain('Alpha contract');
  });
});

describe('the ?stage= deep link resets filters during render without looping', () => {
  it('settles on a single mount and still shows the board', async () => {
    // A restored filter envelope that would otherwise hide the target column entirely.
    sessionStorage.setItem(
      'collection_crm_pipeline_v1',
      JSON.stringify({ query: 'zzz-no-match', facets: { stage: ['lead'] }, voided: null, toggles: {} }),
    );
    await mount('/crm/pipeline?stage=won');
    // If the render-phase reset looped, React would have thrown "Too many re-renders"
    // and this mount would never have completed.
    expect(text()).toContain('Pipeline');
    // The reset cleared the query and the stage facet, so the won column is back.
    expect(text()).toContain('Gamma expansion');
  });

  it('un-hides a stage the user had put away, since the link explicitly asks for it', async () => {
    sessionStorage.setItem('crm_pipeline_hidden_stages', JSON.stringify(['won']));
    await mount('/crm/pipeline?stage=won');
    expect(text()).toContain('Gamma expansion');
  });
});

describe('stage visibility removes the column AND its deals from every number', () => {
  it('a hidden stage drops out of the board and the header total', async () => {
    // Hiding `qualified` must take Beta's 2000 out of the open total, not just its column.
    sessionStorage.setItem('crm_pipeline_hidden_stages', JSON.stringify(['qualified']));
    await mount();
    expect(text()).not.toContain('Beta renewal');
    expect(text()).toContain('$1.0K open');
    expect(text()).toContain('1 open deal');
    expect(text()).toContain('1 stage hidden');
  });

  it('a hidden stage is also absent from the list view', async () => {
    sessionStorage.setItem('crm_pipeline_hidden_stages', JSON.stringify(['qualified']));
    await mount();
    const list = buttonByText('List');
    await act(async () => { list!.click(); });
    expect(text()).toContain('Alpha contract');
    expect(text()).not.toContain('Beta renewal');
  });
});

describe('failure handling', () => {
  it('shows the load error rather than an empty board when the fetch fails', async () => {
    api.mockImplementation((url: string) => {
      if (url === '/api/users') return Promise.resolve({ users: [] });
      return Promise.reject(new Error('boom'));
    });
    await mount();
    expect(text()).toContain("Couldn't load pipeline");
  });
});
