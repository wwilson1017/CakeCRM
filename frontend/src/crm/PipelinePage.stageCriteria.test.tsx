// @vitest-environment jsdom
//
// The pinned stage checklist on the REAL page (#289). The leaves are pinned in
// `components/StageCriteriaPanel.test.tsx`; this file pins the one rule only the page can own:
// the panel closes FOR GOOD when its column leaves the screen, and it does not reappear when the
// column comes back.
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
vi.mock('../shared/useIsMobile', () => ({ useIsMobile: () => false }));
vi.mock('../core/auth/AuthContext', () => ({ useAuth: () => ({ isAdmin: false }) }));

const { PipelinePage } = await import('./PipelinePage');

const DEALS: CrmDeal[] = [{
  id: 1, contact_id: null, company_id: null, title: 'Alpha contract', stage: 'lead',
  value: 1000, notes: '', expected_close_date: '', probability: 50, currency: 'USD',
  created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
}];
const CRITERIA = ['lead', 'qualified', 'proposal', 'negotiation', 'won', 'lost'].map(stage => ({
  stage, source: 'standard', summary: `About ${stage}.`, checklist: [`${stage} item`],
}));

let container: HTMLDivElement;
let root: Root;

const btn = (label: string) =>
  [...document.body.querySelectorAll('button')].find(b => b.getAttribute('aria-label') === label || b.textContent?.trim() === label);
const panel = () => document.body.querySelector('[role="dialog"][aria-label="Lead stage checklist"]');
const click = async (el: Element | undefined) => {
  expect(el, 'control is on screen').toBeTruthy();
  await act(async () => { el!.dispatchEvent(new MouseEvent('click', { bubbles: true })); });
};

beforeEach(async () => {
  sessionStorage.clear();
  localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
  api.mockReset();
  api.mockImplementation((url: string) => {
    if (url === '/api/users') return Promise.resolve({ users: [] });
    if (url === '/api/crm/stage-criteria') return Promise.resolve(CRITERIA);
    if (url.startsWith('/api/crm/deals?sort=id')) return Promise.resolve({ deals: DEALS });
    return Promise.resolve({});
  });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={['/crm/pipeline']}>
        <ActiveRecordProvider><PipelinePage /></ActiveRecordProvider>
      </MemoryRouter>,
    );
  });
  await act(async () => { await Promise.resolve(); });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe('the pinned checklist follows its column', () => {
  it('reads the merged criteria from the server and pins one panel', async () => {
    expect(api.mock.calls.filter(c => c[0] === '/api/crm/stage-criteria')).toHaveLength(1);
    await click(btn('Lead stage checklist'));
    expect(panel()?.textContent).toContain('lead item');
  });

  it('closes for good when its column is hidden, and stays closed when it is shown again', async () => {
    await click(btn('Lead stage checklist'));
    expect(panel()).not.toBeNull();

    await click(btn('Hide Lead column'));
    expect(panel()).toBeNull();

    await click(btn('Show all'));
    expect(btn('Lead stage checklist')).toBeTruthy();   // the column is back…
    expect(panel()).toBeNull();                         // …and the panel is not
  });

  it('closes when the board is swapped for the list view', async () => {
    await click(btn('Lead stage checklist'));
    await click(btn('List'));
    expect(panel()).toBeNull();
  });
});
