// @vitest-environment jsdom
//
// The pipeline's URL contract, which is the half of the Copy-link feature nobody can eyeball:
// the parameter is stripped the instant it is read, so "did the link open the right deal, and
// did the address bar come out clean" is only answerable from a test.
//
// The single-effect assertion is the load-bearing one. `?stage=` and `?deal=` are stripped by
// ONE effect on purpose — two would each compute their next params from the same pre-navigation
// snapshot, so the second `replace` would put back the key the first had just deleted. That bug
// is invisible in the common case (one parameter at a time) and only appears when a dashboard
// link carries both.
import { act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, useLocation, useNavigate, type NavigateFunction } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { CrmDeal } from '../core/types';

const api = vi.hoisted(() => vi.fn());
vi.mock('../core/api/client', () => ({
  api,
  ApiError: class ApiError extends Error {
    status: number;
    constructor(message: string, status = 500) { super(message); this.status = status; }
  },
}));

// The board itself is not under test here and drags nothing in jsdom; stubbing it keeps the
// suite about routing rather than about pointer sensors.
vi.mock('../shared/dnd', () => ({ KanbanBoard: () => null }));

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }));
vi.mock('../shared/toast', () => ({ toast }));

const { PipelinePage } = await import('./PipelinePage');
const { ActiveRecordProvider } = await import('./RecordContext');

function deal(id: number, over: Partial<CrmDeal> = {}): CrmDeal {
  return {
    id, title: `Deal ${id}`, stage: 'lead', value: 100, notes: '',
    contact_id: null, company_id: null, expected_close_date: '', probability: 0,
    currency: 'USD', created_at: '', updated_at: '', ...over,
  };
}

const BOARD = [deal(5, { title: 'Wholesale order' }), deal(6, { title: 'Retail order' })];

let container: HTMLDivElement;
let root: Root;

/** Probe state lives on ONE object, written from an effect. Assigning to a module-level
 *  variable during render is what `react-hooks/globals` forbids (and it would be a real
 *  render-phase side effect); mutating a holder in a commit-phase effect is the pattern the
 *  layer's own suites already use. */
const probe: { search: string; go: NavigateFunction } = { search: '', go: () => {} };

function LocationProbe() {
  const { search } = useLocation();
  // `go` lets a test change the URL WITHOUT remounting the page — the only way to exercise a
  // ref that survives across navigations.
  const navigate = useNavigate();
  useEffect(() => {
    probe.search = search;
    probe.go = navigate;
  });
  return null;
}

beforeEach(() => {
  sessionStorage.clear();
  probe.search = '';
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: () => ({ matches: false, addEventListener: () => {}, removeEventListener: () => {} }),
  });
  Element.prototype.scrollIntoView = vi.fn();
  api.mockReset();
  api.mockImplementation((path: string) => {
    if (path === '/api/crm/deals') return Promise.resolve({ deals: BOARD });
    if (/^\/api\/crm\/deals\/\d+$/.test(path)) {
      const id = Number(path.split('/').pop());
      return Promise.resolve({ ...deal(id, { title: `Fetched ${id}` }), activity: [] });
    }
    if (path.startsWith('/api/crm/provenance/')) return Promise.resolve({ provenance: [] });
    if (path.startsWith('/api/crm/chatter/')) return Promise.resolve({ notes: [] });
    if (/\/fields$/.test(path)) return Promise.resolve([]);
    if (path.startsWith('/api/users')) return Promise.resolve({ users: [] });
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

const dialogTitle = () => document.querySelector('[role="dialog"] h2')?.textContent ?? null;

describe('the deal deep link', () => {
  it('opens the linked deal and empties the address bar', async () => {
    renderAt('/crm/pipeline?deal=5');
    await settle();
    expect(dialogTitle()).toBe('Wholesale order');
    // The whole point of the Copy-link contract: by the time anyone could select the URL, the
    // parameter is gone, so the button is the only link handoff there is.
    expect(probe.search).toBe('');
  });

  it('opens nothing for a malformed id, and still cleans up after itself', async () => {
    renderAt('/crm/pipeline?deal=abc');
    await settle();
    expect(dialogTitle()).toBeNull();
    expect(probe.search).toBe('');
  });

  it('resolves a deal that is not on the board at all', async () => {
    // An archived deal, or one a shared link opened before this board holds it: the layer
    // fetches it through `loadById` rather than showing an empty panel.
    renderAt('/crm/pipeline?deal=404');
    await settle();
    expect(dialogTitle()).toBe('Fetched 404');
  });

  it('strips ?deal= even while the board is still loading', async () => {
    // The id already lives in state by then, and a board that never loads must still leave a
    // clean address bar rather than a link that re-fires on every refresh.
    let releaseBoard: (v: unknown) => void = () => {};
    const defaults = api.getMockImplementation()!;
    api.mockImplementation((path: string, options?: RequestInit) =>
      path === '/api/crm/deals'
        ? new Promise(res => { releaseBoard = res; })
        : defaults(path, options));
    renderAt('/crm/pipeline?deal=5');
    await settle();
    expect(probe.search).toBe('');

    await act(async () => { releaseBoard({ deals: BOARD }); await Promise.resolve(); });
    await settle();
    expect(dialogTitle()).toBe('Wholesale order');
  });
});

describe('the two deep-link params together', () => {
  it('strips both without either resurrecting the other', async () => {
    // ONE effect, ONE setSearchParams. Split them and this is the case that breaks: the deal
    // strip and the stage strip each start from the same params, so whichever replaces second
    // restores the other's key.
    renderAt('/crm/pipeline?stage=lead&deal=5');
    await settle();
    expect(probe.search).toBe('');
    expect(dialogTitle()).toBe('Wholesale order');
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

  // The SCROLL half of that guard is not asserted here: `KanbanBoard` is mocked away, so no
  // column refs are ever registered and `scrollIntoView` has nothing to call. The parameter
  // handling above is the part that broke and the part this suite owns; the scroll itself is
  // covered on the PR's evidence run.
});
