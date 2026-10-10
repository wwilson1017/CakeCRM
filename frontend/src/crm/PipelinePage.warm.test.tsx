// @vitest-environment jsdom
//
// The Pipeline board's warm cache (#281): it opens on the last complete sweep while the real one
// runs, the real one replaces the cache wholesale, and — the rule this file exists for — a board
// seeded from the cache may OPEN a deal it holds but can never declare a deep-linked deal absent.
// Only a load that actually applied may do that (#145's verdict), because a cache can be days old.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CrmDeal } from '../core/types';

const api = vi.hoisted(() => vi.fn());
vi.mock('../core/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../core/api/client')>()),
  api,
}));
vi.mock('../shared/toast', () => ({ toast: { error: vi.fn(), info: vi.fn(), success: vi.fn() } }));

const warm = vi.hoisted(() => ({ readWarm: vi.fn(), writeWarm: vi.fn() }));
vi.mock('./warmCache', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./warmCache')>()),
  readWarm: warm.readWarm,
  writeWarm: warm.writeWarm,
}));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { PipelinePage } = await import('./PipelinePage');
const { ActiveRecordProvider } = await import('./RecordContext');
const { WarmViewerContext } = await import('./warmCache');
const { MemoryRouter } = await import('react-router-dom');

const EMAIL = 'ana@example.com';
const LIVE_PATH = '/api/crm/deals?sort=id&limit=501';

function deal(over: Partial<CrmDeal>): CrmDeal {
  return {
    id: 1, title: 'Untitled', stage: 'lead', value: 0, probability: 20,
    expected_close_date: '', notes: '', contact_id: null, company_id: null, currency: 'USD',
    archived_at: null,
    created_at: '2026-08-01T00:00:00+00:00', updated_at: '2026-08-01T00:00:00+00:00',
    ...over,
  };
}
const CACHED = deal({ id: 1, title: 'Cached renewal' });
const FRESH = deal({ id: 2, title: 'Fresh expansion' });

/** Hold the board GET until the test lands it, so "cached rows while the sweep runs" is a state
 *  the test can look at rather than a race. */
function holdBoard() {
  let settle!: { resolve: (deals: CrmDeal[]) => void; reject: (e: Error) => void };
  const pending = new Promise<{ deals: CrmDeal[] }>((resolve, reject) => {
    settle = { resolve: deals => resolve({ deals }), reject };
  });
  api.mockImplementation(async (path: string) => {
    if (path === LIVE_PATH) return pending;
    if (path === '/api/users') return { users: [] };
    return null;
  });
  return settle;
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  window.matchMedia = ((query: string) => ({
    matches: false, media: query, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {},
    addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
  Element.prototype.scrollIntoView = vi.fn();
  sessionStorage.clear();
  localStorage.clear();
  api.mockReset();
  warm.readWarm.mockReset();
  warm.writeWarm.mockReset();
  warm.readWarm.mockResolvedValue({ data: [CACHED], savedAt: Date.now() - 3_600_000 });
  warm.writeWarm.mockResolvedValue(undefined);
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

async function flush() {
  for (let i = 0; i < 5; i++) await act(async () => { await Promise.resolve(); });
}

async function render(url = '/crm/pipeline') {
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={[url]}>
        <WarmViewerContext.Provider value={EMAIL}>
          <ActiveRecordProvider><PipelinePage /></ActiveRecordProvider>
        </WarmViewerContext.Provider>
      </MemoryRouter>,
    );
  });
  await flush();
}

const text = () => container.textContent ?? '';
const boardGets = () => api.mock.calls.filter(([p]) => String(p).startsWith('/api/crm/deals?')).length;
const DEAD = "isn't on this board";

describe('Pipeline warm cache (#281)', () => {
  it('opens on the cached board while the sweep runs, then replaces it and the cache wholesale', async () => {
    const board = holdBoard();
    await render();
    expect(text()).toContain('Cached renewal');
    expect(text()).toContain('Refreshing…');
    await act(async () => board.resolve([FRESH]));
    await flush();
    expect(text()).toContain('Fresh expansion');
    expect(text()).not.toContain('Cached renewal');
    expect(text()).not.toContain('Refreshing…');
    expect(warm.writeWarm).toHaveBeenCalledTimes(1);
    expect(warm.writeWarm.mock.calls[0].slice(0, 3)).toEqual([EMAIL, 'pipeline', [FRESH]]);
  });

  it('never calls a deep-linked deal absent on the strength of the cache alone', async () => {
    const board = holdBoard();
    await render('/crm/pipeline?deal=2');
    // The cache lacks deal 2 and no load has applied: silent, and no second sweep fired.
    expect(text()).toContain('Cached renewal');
    expect(text()).not.toContain(DEAD);
    expect(boardGets()).toBe(1);
    await act(async () => board.resolve([CACHED]));
    await flush();
    // A real load applied without it: NOW it may say so.
    expect(text()).toContain(DEAD);
  });

  it('keeps the cached board up and says so when the refresh fails, accusing no deep link', async () => {
    const board = holdBoard();
    await render('/crm/pipeline?deal=2');
    await act(async () => board.reject(new Error('offline')));
    await flush();
    expect(text()).toContain('Cached renewal');
    expect(text()).toContain("Couldn't refresh");
    expect(text()).not.toContain(DEAD);
    expect(warm.writeWarm).not.toHaveBeenCalled();
  });

  it('writes no cache outside the CRM layout (no signed-in email)', async () => {
    const board = holdBoard();
    await act(async () => {
      root.render(
        <MemoryRouter initialEntries={['/crm/pipeline']}>
          <ActiveRecordProvider><PipelinePage /></ActiveRecordProvider>
        </MemoryRouter>,
      );
    });
    await flush();
    // `readWarm(null, …)` answers null itself (pinned in warmCache.test.ts); the page's half is
    // that it asks under no email and never writes.
    expect(warm.readWarm).toHaveBeenCalledWith(null, 'pipeline');
    await act(async () => board.resolve([FRESH]));
    await flush();
    expect(text()).toContain('Fresh expansion');
    expect(warm.writeWarm).not.toHaveBeenCalled();
  });
});
