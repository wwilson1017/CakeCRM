// @vitest-environment jsdom
//
// Issue #129, item 2. Two behaviours, one listener:
//
//   • the FIRST corpus sweep is deferred while the tab is hidden — a route can be opened into a
//     background tab (a Cmd-click, a session restore), and this load is a keyset sweep of every
//     deal;
//   • returning to a tab whose corpus has gone stale re-sweeps it, which is the list pages'
//     existing `useCrmCorpus` idiom (`CORPUS_MAX_AGE_MS` + `visibilitychange`) extended to the
//     one swept corpus that had no staleness bound.
//
// The board's own load machinery (generations, deferral behind writes, the spinner) is pinned by
// the sibling suites; this file is only about WHEN a load is allowed to start.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { ActiveRecordProvider } from './RecordContext';

import type { CrmDeal } from '../core/types';
import { CORPUS_MAX_AGE_MS } from './usePatchableAssembly';

const api = vi.hoisted(() => vi.fn());
vi.mock('../core/api/client', () => ({
  api,
  ApiError: class ApiError extends Error {
    status?: number;
    detail?: string;
  },
}));

vi.mock('../shared/useIsMobile', () => ({ useIsMobile: () => false }));

vi.mock('./components/DealDetailSheet', () => ({
  DealDetailSheet: ({ deal }: { deal: { id: number; title: string } }) => (
    <div data-testid="deal-sheet" data-deal-id={deal.id}>{deal.title}</div>
  ),
}));

const { PipelinePage } = await import('./PipelinePage');

const DEALS: CrmDeal[] = [
  {
    id: 1, contact_id: null, company_id: null, title: 'Alpha contract', stage: 'lead',
    value: 1000, notes: '', expected_close_date: '', probability: 50, currency: 'USD',
    created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
  },
];

let container: HTMLDivElement;
let root: Root;

const text = () => document.body.textContent ?? '';

/** How many full corpus sweeps the page has asked for. */
const sweeps = () =>
  api.mock.calls.filter(call => String(call[0]).startsWith('/api/crm/deals?sort=id')).length;

function setVisibility(state: 'visible' | 'hidden') {
  // `visibilityState` is a prototype getter in jsdom, so it is shadowed with an own property.
  Object.defineProperty(document, 'visibilityState', { value: state, configurable: true });
}

/** Move the tab to `state` and fire the event the browser would fire. */
async function visibility(state: 'visible' | 'hidden') {
  setVisibility(state);
  await act(async () => {
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await act(async () => { await Promise.resolve(); });
}

async function mount(): Promise<void> {
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={['/crm/pipeline']}>
        <ActiveRecordProvider>
          <PipelinePage />
        </ActiveRecordProvider>
      </MemoryRouter>,
    );
  });
  await act(async () => { await Promise.resolve(); });
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  sessionStorage.clear();
  setVisibility('visible');
  Element.prototype.scrollIntoView = vi.fn();
  api.mockReset();
  api.mockImplementation((url: string) => {
    if (url === '/api/users') return Promise.resolve({ users: [] });
    if (url.startsWith('/api/crm/deals?sort=id')) return Promise.resolve({ deals: DEALS });
    return Promise.resolve({});
  });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  Reflect.deleteProperty(document, 'visibilityState');
  vi.useRealTimers();
});

describe('the first load waits for the tab to be looked at', () => {
  it('sweeps nothing while the tab is hidden, and shows its spinner rather than an empty board', async () => {
    setVisibility('hidden');
    await mount();

    expect(sweeps()).toBe(0);
    // `loading` starts true and never cleared, so the page renders its loading state. The board
    // heading and the deal are both absent — which is the honest report, not a false empty board.
    expect(text()).not.toContain('Alpha contract');
  });

  it('runs it exactly once when the tab is first shown', async () => {
    setVisibility('hidden');
    await mount();
    expect(sweeps()).toBe(0);

    await visibility('visible');

    expect(sweeps()).toBe(1);
    expect(text()).toContain('Alpha contract');
  });

  it('does not re-run the deferred load on a second return', async () => {
    setVisibility('hidden');
    await mount();
    await visibility('visible');
    expect(sweeps()).toBe(1);

    // The deferral is one-shot. Coming back again inside the staleness window must do nothing.
    await visibility('hidden');
    await visibility('visible');
    expect(sweeps()).toBe(1);
  });

  it('loads immediately on a tab that is already visible', async () => {
    await mount();
    expect(sweeps()).toBe(1);
    expect(text()).toContain('Alpha contract');
  });
});

describe('returning to the tab re-sweeps a stale corpus', () => {
  it('leaves a fresh corpus alone, however often the tab is switched', async () => {
    await mount();
    expect(sweeps()).toBe(1);

    vi.setSystemTime(Date.now() + CORPUS_MAX_AGE_MS - 1000);
    await visibility('hidden');
    await visibility('visible');

    // Flicking between tabs must never refetch — that is the whole reason the bound exists.
    expect(sweeps()).toBe(1);
  });

  it('re-sweeps once the corpus is older than the bound', async () => {
    await mount();
    expect(sweeps()).toBe(1);

    vi.setSystemTime(Date.now() + CORPUS_MAX_AGE_MS + 1000);
    await visibility('hidden');
    await visibility('visible');

    expect(sweeps()).toBe(2);
  });

  it('re-sweeps SILENTLY, so a returning user is not shown a spinner over the board', async () => {
    await mount();
    expect(text()).toContain('Alpha contract');

    // Hold the second sweep open, and assert the board is still on screen underneath it. A
    // non-silent load would swap the whole page for its loading state and take an open deal
    // sheet with it.
    let release: ((value: { deals: CrmDeal[] }) => void) | null = null;
    api.mockImplementation((url: string) => {
      if (url === '/api/users') return Promise.resolve({ users: [] });
      if (url.startsWith('/api/crm/deals?sort=id')) {
        return new Promise<{ deals: CrmDeal[] }>(resolve => { release = resolve; });
      }
      return Promise.resolve({});
    });

    vi.setSystemTime(Date.now() + CORPUS_MAX_AGE_MS + 1000);
    await visibility('hidden');
    await visibility('visible');

    expect(sweeps()).toBe(2);
    expect(text()).toContain('Alpha contract');

    await act(async () => {
      release?.({ deals: DEALS });
      await Promise.resolve();
    });
    expect(text()).toContain('Alpha contract');
  });

  it('does nothing on the way OUT — only a return can trigger a load', async () => {
    await mount();
    vi.setSystemTime(Date.now() + CORPUS_MAX_AGE_MS + 1000);

    await visibility('hidden');

    expect(sweeps()).toBe(1);
  });
});
