// @vitest-environment jsdom
//
// The list pages' warm cache (#281), through Contacts — Companies runs the same `useCrmCorpus`
// path. The page opens on the cached corpus while its sweep runs; ONLY a complete sweep replaces
// the cache; a sweep that fails part-way leaves the cached rows up, says so, and writes nothing.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CRM_LIST_PAGE_SIZE } from './assemblyPage';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const apiMock = vi.fn();
vi.mock('../core/api/client', () => ({
  api: (...args: unknown[]) => apiMock(...args),
  ApiError: class extends Error { status = 0; detail = ''; },
}));
vi.mock('./useOwnerOptions', () => ({ useOwnerOptions: () => ({ options: null, loading: false }) }));
vi.mock('./ContactDetailPage', () => ({ ContactDetailPage: () => <div data-testid="detail" /> }));
const warm = vi.hoisted(() => ({ readWarm: vi.fn(), writeWarm: vi.fn() }));
vi.mock('./warmCache', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./warmCache')>()),
  readWarm: warm.readWarm,
  writeWarm: warm.writeWarm,
}));

const { ContactsPage } = await import('./ContactsPage');
const { WarmViewerContext } = await import('./warmCache');

const EMAIL = 'ana@example.com';
const contact = (id: number, name: string) => ({ id, name, status: 'active', tags: '' });

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: false, media: query, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {},
    addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  }));
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  apiMock.mockReset();
  warm.readWarm.mockReset();
  warm.writeWarm.mockReset();
  warm.readWarm.mockResolvedValue({ data: [contact(1, 'Cached Ada')], savedAt: Date.now() - 60_000 });
  warm.writeWarm.mockResolvedValue(undefined);
  sessionStorage.clear();
  localStorage.clear();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

async function settle(rounds = 8) {
  for (let i = 0; i < rounds; i++) await act(async () => { await Promise.resolve(); });
}

async function mount() {
  act(() => {
    root.render(
      <MemoryRouter initialEntries={['/crm/contacts']}>
        <WarmViewerContext.Provider value={EMAIL}>
          <Routes><Route path="/crm/contacts/:id?" element={<ContactsPage />} /></Routes>
        </WarmViewerContext.Provider>
      </MemoryRouter>,
    );
  });
  await settle();
}

const text = () => container.textContent ?? '';

describe('Contacts warm cache (#281)', () => {
  it('shows the cached corpus while the sweep runs, then the fresh one, caching the raw sweep', async () => {
    let land!: (v: unknown) => void;
    apiMock.mockImplementation(() => new Promise(r => { land = r; }));
    await mount();
    expect(text()).toContain('Cached Ada');
    expect(text()).toContain('Refreshing…');
    await act(async () => land({ contacts: [contact(2, 'Fresh Bo')] }));
    await settle();
    expect(text()).toContain('Fresh Bo');
    expect(text()).not.toContain('Cached Ada');
    expect(text()).not.toContain('Refreshing…');
    expect(warm.writeWarm).toHaveBeenCalledTimes(1);
    expect(warm.writeWarm.mock.calls[0].slice(0, 3)).toEqual([EMAIL, 'contacts', [contact(2, 'Fresh Bo')]]);
  });

  it('never caches a partial sweep: page one lands, page two fails, the cache is untouched', async () => {
    const fullPage = Array.from({ length: CRM_LIST_PAGE_SIZE + 1 }, (_, i) => contact(i + 10, `Row ${i}`));
    apiMock.mockImplementation(async (url: string) => {
      if (url.includes('after_id=')) throw new Error('offline');
      return { contacts: fullPage };
    });
    await mount();
    expect(apiMock.mock.calls.some(([u]) => String(u).includes('after_id='))).toBe(true);
    expect(warm.writeWarm).not.toHaveBeenCalled();
    expect(text()).toContain('Cached Ada');
    expect(text()).toContain("Couldn't refresh");
  });
});
