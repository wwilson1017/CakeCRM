// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActiveRecordProvider } from './RecordContext';
import { WarmViewerContext } from './warmCache';
import { headerCollapsedKey } from './pipelineBoard';
import { STAGE_ORDER } from './constants';
import type { CrmDeal } from '../core/types';

const mocks = vi.hoisted(() => ({ api: vi.fn(), mobile: false }));
vi.mock('../core/api/client', () => ({ api: mocks.api, ApiError: class extends Error {} }));
vi.mock('../core/auth/AuthContext', () => ({ useAuth: () => ({ currentUser: null }) }));
vi.mock('../shared/useIsMobile', () => ({ useIsMobile: () => mocks.mobile }));
const { PipelinePage } = await import('./PipelinePage');
const DEAL: CrmDeal = {
  id: 1, contact_id: null, company_id: null, title: 'Alpha contract', stage: 'lead',
  value: 1000, notes: '', expected_close_date: '', probability: 50, currency: 'USD',
  created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
};
const VIEWER_A = 'a@example.com';
const VIEWER_B = 'b@example.com';
let deals: CrmDeal[];
let container: HTMLDivElement;
let root: Root;
const heading = () => container.querySelector('h1');
const arrow = () => container.querySelector<HTMLButtonElement>('button[aria-label="Hide header"], button[aria-label="Show header"]');
function button(text: string): HTMLButtonElement {
  const found = Array.from(document.querySelectorAll('button'))
    .find(b => b.textContent?.trim() === text || b.getAttribute('aria-label') === text);
  if (!found) throw new Error(`Missing button: ${text}`);
  return found;
}
async function click(target: HTMLButtonElement | null) {
  if (!target) throw new Error('Missing header arrow');
  await act(async () => target.click());
}
async function mount(viewer: string | null = VIEWER_A) {
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={['/crm/pipeline']}>
        <WarmViewerContext.Provider value={viewer}>
          <ActiveRecordProvider><PipelinePage /></ActiveRecordProvider>
        </WarmViewerContext.Provider>
      </MemoryRouter>,
    );
  });
  await act(async () => { await Promise.resolve(); });
}
async function remount() {
  act(() => root.unmount());
  root = createRoot(container);
  await mount();
}
function blockStorage() {
  for (const method of ['getItem', 'setItem', 'removeItem'] as const) {
    vi.spyOn(Storage.prototype, method).mockImplementation(() => { throw new Error('Storage blocked'); });
  }
}
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  vi.stubGlobal('IntersectionObserver', class {
    observe() {}
    disconnect() {}
  });
  localStorage.clear();
  sessionStorage.clear();
  mocks.mobile = false;
  deals = [DEAL];
  Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.scrollTo = vi.fn();
  mocks.api.mockReset();
  mocks.api.mockImplementation((url: string) => {
    if (url.startsWith('/api/crm/fields?')) return Promise.resolve([]);
    if (url === '/api/users') return Promise.resolve({ users: [] });
    if (url.startsWith('/api/crm/deals?sort=id')) return Promise.resolve({ deals });
    return Promise.resolve({});
  });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  Reflect.deleteProperty(document, 'visibilityState');
});

describe('Pipeline header collapse', () => {
  it('defaults expanded and remembers collapse and expansion across mounts', async () => {
    await mount();
    expect(heading()?.textContent).toBe('Pipeline');
    expect(arrow()?.getAttribute('aria-label')).toBe('Hide header');
    expect(arrow()?.getAttribute('aria-expanded')).toBe('true');
    await click(arrow());
    expect(heading()).toBeNull();
    expect(arrow()?.getAttribute('aria-label')).toBe('Show header');
    expect(arrow()?.getAttribute('aria-expanded')).toBe('false');
    expect(button('Add Deal')).toBeTruthy();
    expect(localStorage.getItem('cakecrm_pipeline_header_collapsed:a@example.com')).toBe('1');
    expect(container.querySelector('[data-stage="lead"]')).not.toBeNull();
    expect(container.querySelector('input[placeholder="Search deals, contacts, companies..."]')).not.toBeNull();
    await remount();
    expect(heading()).toBeNull();
    await click(arrow());
    expect(localStorage.getItem(headerCollapsedKey(VIEWER_A))).toBeNull();
    await remount();
    expect(heading()?.textContent).toBe('Pipeline');
  });
  it('keeps the arrow reachable on an empty board in both states', async () => {
    deals = [];
    await mount();
    expect(arrow()).not.toBeNull();
    await click(arrow());
    expect(heading()).toBeNull();
    expect(arrow()).not.toBeNull();
    await click(arrow());
    expect(button('Show archived deals')).toBeTruthy();
  });
  it('toggles both ways for this mount when storage throws', async () => {
    blockStorage();
    await mount();
    expect(heading()).not.toBeNull();
    await click(arrow());
    expect(heading()).toBeNull();
    await click(arrow());
    expect(heading()).not.toBeNull();
  });
  it('isolates each viewer and restores their stored choice', async () => {
    await mount();
    await click(arrow());
    await mount(VIEWER_B);
    expect(heading()).not.toBeNull();
    await mount(VIEWER_A);
    expect(heading()).toBeNull();
  });
  it('does not carry a click to another viewer when storage is blocked', async () => {
    blockStorage();
    await mount();
    await click(arrow());
    expect(heading()).toBeNull();
    await mount(VIEWER_B);
    expect(heading()).not.toBeNull();
  });
  it('recovers all hidden stage columns through the empty-board arrow', async () => {
    sessionStorage.setItem('crm_pipeline_hidden_stages', JSON.stringify(STAGE_ORDER));
    await mount();
    expect(container.querySelector('[data-stage]')).toBeNull();
    await click(arrow());
    expect(heading()).toBeNull();
    expect(arrow()).not.toBeNull();
    await click(arrow());
    await click(button('Show all'));
    expect(container.querySelectorAll('[data-stage]')).toHaveLength(STAGE_ORDER.length);
    expect(container.textContent).toContain(DEAL.title);
  });
  it('opens the create form from the relocated Add Deal button', async () => {
    await mount();
    await click(arrow());
    await click(button('Add Deal'));
    expect(document.body.textContent).toContain('New Deal');
  });
  it('keeps the phone header and Add action unchanged despite a stored collapse', async () => {
    mocks.mobile = true;
    localStorage.setItem(headerCollapsedKey(VIEWER_A), '1');
    await mount();
    expect(heading()?.textContent).toBe('Pipeline');
    expect(arrow()).toBeNull();
    expect(button('Add')).toBeTruthy();
  });
});
