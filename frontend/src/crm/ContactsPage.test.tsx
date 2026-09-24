// @vitest-environment jsdom
/**
 * The route contract behind #77's optional `:id?` segment.
 *
 * `/crm/contacts` and `/crm/contacts/:id` are ONE route rendering ContactsPage, so the
 * element type the router renders never changes — `usePageAssembly` therefore stays
 * mounted, and opening a contact and coming back does not re-sweep the whole corpus.
 *
 * Precisely: it is the element TYPE that has to hold, not the single route. Two separate
 * <Route>s both rendering ContactsPage would reconcile to the same instance and behave
 * identically; what breaks it is routing `:id` at a DIFFERENT component, which is exactly
 * the pre-#77 shape (`contacts/:id` → ContactDetailPage). Restoring that makes the last
 * test fail with four sweeps where there should be two — verified, not assumed.
 *
 * A lifecycle property like this survives type-checks and smoke clicks unnoticed, which
 * is why it is pinned here rather than left to the PR description.
 *
 * Navigation goes through a real `useNavigate` rather than re-rendering the router with
 * different `initialEntries` — those apply only on first mount, so a re-render would move
 * nothing and the no-re-sweep assertion would pass vacuously.
 */
import { act, StrictMode, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes, useNavigate, type NavigateFunction } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CRM_LIST_PAGE_SIZE } from './assemblyPage';

const apiMock = vi.fn();
vi.mock('../core/api/client', () => ({
  api: (...args: unknown[]) => apiMock(...args),
  ApiError: class extends Error { status = 0; detail = ''; },
}));
vi.mock('./useOwnerOptions', () => ({
  useOwnerOptions: () => ({ options: null, loading: false }),
}));
// The detail page is a heavy, separately-tested surface; this test is about which
// component the ROUTER mounts, not what the detail renders.
vi.mock('./ContactDetailPage', () => ({
  ContactDetailPage: () => <div data-testid="detail" />,
}));

const { ContactsPage } = await import('./ContactsPage');

let container: HTMLDivElement;
let root: Root;
const nav: { current: NavigateFunction | null } = { current: null };

/** Every corpus-sweep request, by the URL it asked for. */
function listCalls(): string[] {
  return apiMock.mock.calls
    .map(c => String(c[0]))
    .filter(url => url.startsWith('/api/crm/contacts?'));
}

/** Captures a real navigate handle — mutated in an effect, never during render. */
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
            <Route path="/crm/contacts/:id?" element={<ContactsPage />} />
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
  // jsdom implements no media queries; useIsMobile calls matchMedia in an effect.
  // Always-desktop is the right answer for a routing test.
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
  apiMock.mockResolvedValue({ contacts: [{ id: 1, name: 'Ada', status: 'active', tags: '' }] });
  sessionStorage.clear();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

describe('the contacts route', () => {
  it('sweeps the corpus on the immutable id key when the list is shown', async () => {
    mountAt('/crm/contacts');
    await settle();
    expect(listCalls().length).toBeGreaterThan(0);
    // Keyset, never OFFSET: the sweep must ask for the immutable order.
    expect(listCalls()[0]).toContain('sort=id');
    expect(listCalls()[0]).not.toContain('offset=');
  });

  it('does NOT sweep at all on a cold deep link to a contact', async () => {
    // A shared /crm/contacts/42 link should open the record, not pull every contact in
    // the install first. This is usePageAssembly's `enabled` gate doing its job.
    mountAt('/crm/contacts/42');
    await settle();
    expect(listCalls()).toHaveLength(0);
    expect(detailShown()).toBe(true);
  });

  it('renders the detail for :id and the collection without it, from ONE route', async () => {
    mountAt('/crm/contacts');
    await settle();
    expect(detailShown()).toBe(false);

    await goTo('/crm/contacts/42');
    expect(detailShown()).toBe(true);

    await goTo('/crm/contacts');
    expect(detailShown()).toBe(false);
  });

  it('does not re-sweep when a contact is opened and closed again', async () => {
    mountAt('/crm/contacts');
    await settle();
    // A DELTA, not an absolute count: StrictMode deliberately double-invokes effects, so
    // the initial number of requests is not the property under test — the property is that
    // navigating adds none.
    const afterFirstLoad = listCalls().length;
    expect(afterFirstLoad).toBeGreaterThan(0);

    await goTo('/crm/contacts/1');
    expect(detailShown()).toBe(true);   // we really did navigate

    await goTo('/crm/contacts');
    expect(detailShown()).toBe(false);  // …and really did come back

    // Two separate <Route> elements would unmount ContactsPage on the way in and re-fetch
    // the entire corpus on the way back.
    expect(listCalls().length).toBe(afterFirstLoad);
  });
});

describe('the corpus sweep across multiple pages', () => {
  /** A page of `n` contacts starting at `startId`. */
  const page = (startId: number, n: number) =>
    Array.from({ length: n }, (_, i) => ({
      id: startId + i, name: `C${startId + i}`, status: 'active', tags: '',
    }));

  it('threads the cursor from the last KEPT row and merges every page', async () => {
    // The mock answers the CURSOR rather than the call order — StrictMode mounts twice,
    // so a sequence of mockResolvedValueOnce would be consumed by the second mount's
    // first page and the sweep would never reach page two.
    const TOTAL = CRM_LIST_PAGE_SIZE + 3;
    apiMock.mockReset();
    apiMock.mockImplementation(async (url: string) => {
      const after = Number(new URL(url, 'http://x').searchParams.get('after_id') ?? 0);
      // Serve ids after the cursor, up to the over-ask (SIZE + 1) the sweep requests.
      const start = after + 1;
      const n = Math.max(0, Math.min(CRM_LIST_PAGE_SIZE + 1, TOTAL - after));
      return { contacts: page(start, n) };
    });

    mountAt('/crm/contacts');
    await settle(14);

    // Asserted as a SET, not by position: StrictMode mounts twice, so the two mounts'
    // requests interleave and calls[1] is the other mount's page one.
    const cursors = listCalls().map(u => new URL(u, 'http://x').searchParams.get('after_id'));
    // A first page carries no cursor…
    expect(cursors).toContain(null);
    // …and the next resumes from the last row KEPT, not the probe row that was dropped —
    // so the probe returns as the head of page two rather than being skipped. Every
    // cursor sent must be that id; any other value would mean a skipped or repeated row.
    const sent = cursors.filter(c => c !== null);
    expect(sent.length).toBeGreaterThan(0);
    expect(new Set(sent)).toEqual(new Set([String(CRM_LIST_PAGE_SIZE)]));

    // Every row from both pages landed, exactly once — asserted on the corpus size the
    // bar reports rather than on rendered rows, because ListView renders its first 300 and
    // offers a "show all" for the remainder (so nothing is unreachable, just not yet in
    // the DOM).
    expect(container.textContent).toContain(String(TOTAL));
    // Rows that DID render are unique — a mis-threaded cursor would repeat the boundary.
    const ids = [...container.querySelectorAll('td')]
      .map(td => td.textContent ?? '')
      .filter(t => /^C\d+$/.test(t));
    expect(ids.length).toBeGreaterThan(0);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it('stops after one page when the response does not overflow', async () => {
    apiMock.mockReset();
    apiMock.mockResolvedValue({ contacts: page(1, CRM_LIST_PAGE_SIZE) });
    mountAt('/crm/contacts');
    await settle(14);
    // An exactly-full page is the case a length-based hasMore would get wrong, costing a
    // needless extra round-trip (and, on the last allowed page, a spurious failure).
    const perMount = listCalls().length;
    expect(perMount).toBeLessThanOrEqual(2);   // StrictMode double-invoke, one call each
    expect(listCalls().every(u => !u.includes('after_id'))).toBe(true);
  });

  it('renders the name cell as a real link, so a row can be opened in a new tab (#148)', async () => {
    // The consumer half of #148. The row is keyboard-openable on its own, but an anchor adds
    // what a click handler cannot hand-roll: link semantics for a screen reader, and native
    // Cmd/Ctrl-click, middle-click and right-click into a new tab — a middle click fires no
    // `click` event at all, so `onRowClick` never sees it. Contacts navigates on select, so
    // it takes the anchor; Todos opens an overlay and deliberately does not.
    mountAt('/crm/contacts');
    await settle();
    const link = container.querySelector('tbody a') as HTMLAnchorElement | null;
    expect(link?.getAttribute('href')).toBe('/crm/contacts/1');
    expect(link?.textContent).toContain('Ada');
  });
});
