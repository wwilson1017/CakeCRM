// @vitest-environment jsdom
/**
 * The route contract for Companies (#77).
 *
 * CompaniesPage re-implements ContactsPage's pattern rather than sharing code with it, so
 * the invariant needs pinning on both: `companies/:id?` must render list and detail from
 * ONE element type, or the corpus sweep restarts on every Back. See ContactsPage.test.tsx
 * for the full reasoning and the falsification against the pre-#77 shape.
 */
import { act, StrictMode, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes, useNavigate, type NavigateFunction } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const apiMock = vi.fn();
vi.mock('../core/api/client', () => ({
  api: (...args: unknown[]) => apiMock(...args),
  ApiError: class extends Error { status = 0; detail = ''; },
}));
vi.mock('./useOwnerOptions', () => ({
  useOwnerOptions: () => ({ options: null, loading: false }),
}));
vi.mock('./CompanyDetailPage', () => ({
  CompanyDetailPage: () => <div data-testid="detail" />,
}));

const { CompaniesPage } = await import('./CompaniesPage');

let container: HTMLDivElement;
let root: Root;
const nav: { current: NavigateFunction | null } = { current: null };

const listCalls = () =>
  apiMock.mock.calls.map(c => String(c[0])).filter(u => u.startsWith('/api/crm/companies?'));

function NavHandle() {
  const navigate = useNavigate();
  useEffect(() => { nav.current = navigate; });
  return null;
}

function mountAt(path: string): void {
  act(() => {
    root.render(
      <StrictMode>
        <MemoryRouter initialEntries={[path]}>
          <NavHandle />
          <Routes>
            <Route path="/crm/companies/:id?" element={<CompaniesPage />} />
          </Routes>
        </MemoryRouter>
      </StrictMode>,
    );
  });
}

async function settle(rounds = 8): Promise<void> {
  for (let i = 0; i < rounds; i++) {
    await act(async () => { await Promise.resolve(); });
  }
}

async function goTo(path: string): Promise<void> {
  act(() => { nav.current!(path); });
  await settle();
}

const detailShown = () => container.querySelector('[data-testid="detail"]') !== null;

beforeEach(() => {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: false, media: query, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {},
    addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  }));
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  nav.current = null;
  apiMock.mockReset();
  apiMock.mockResolvedValue({ companies: [{ id: 1, name: 'Acme', status: 'active' }] });
  sessionStorage.clear();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

describe('the companies route', () => {
  it('sweeps on the immutable id key, with no OFFSET', async () => {
    mountAt('/crm/companies');
    await settle();
    expect(listCalls().length).toBeGreaterThan(0);
    expect(listCalls()[0]).toContain('sort=id');
    expect(listCalls()[0]).not.toContain('offset=');
  });

  it('does NOT sweep on a cold deep link to a company', async () => {
    mountAt('/crm/companies/42');
    await settle();
    expect(listCalls()).toHaveLength(0);
    expect(detailShown()).toBe(true);
  });

  it('does not re-sweep when a company is opened and closed again', async () => {
    mountAt('/crm/companies');
    await settle();
    const afterFirstLoad = listCalls().length;
    expect(afterFirstLoad).toBeGreaterThan(0);

    await goTo('/crm/companies/1');
    expect(detailShown()).toBe(true);

    await goTo('/crm/companies');
    expect(detailShown()).toBe(false);

    expect(listCalls().length).toBe(afterFirstLoad);
  });

  it('renders the name cell as a real link, so a row can be opened in a new tab (#148)', async () => {
    // See the matching test on Contacts: the anchor carries the new-tab and link-semantics
    // half of #148 that a row click handler cannot.
    mountAt('/crm/companies');
    await settle();
    const link = container.querySelector('tbody a') as HTMLAnchorElement | null;
    expect(link?.getAttribute('href')).toBe('/crm/companies/1');
    expect(link?.textContent).toContain('Acme');
  });
});
