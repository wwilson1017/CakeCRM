// @vitest-environment jsdom
/**
 * The two decisions in TodosPage that a compiler cannot check (#77).
 *
 * 1. Completing a REPEATING todo spawns its next occurrence server-side, so that path must
 *    re-sweep rather than patch — and the decision must read the SERVER's copy of `repeat`,
 *    not the pre-write one, because the response reflects the state the server actually
 *    used to decide whether to spawn.
 * 2. A failed write is triaged on its status: a definite 4xx wrote nothing (keep the
 *    corpus), anything else may have committed and been lost (re-sweep).
 *
 * Both would compile, and pass every other test, if inverted.
 */
import { act, StrictMode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const apiMock = vi.fn();
class FakeApiError extends Error {
  status: number;
  detail = '';
  constructor(status: number) { super(`http ${status}`); this.status = status; }
}
vi.mock('../core/api/client', () => ({
  api: (...args: unknown[]) => apiMock(...args),
  ApiError: FakeApiError,
}));
vi.mock('./useOwnerOptions', () => ({
  useOwnerOptions: () => ({ options: null, loading: false }),
}));

const { TodosPage } = await import('./TodosPage');

let container: HTMLDivElement;
let root: Root;

const todo = (over: Record<string, unknown> = {}) => ({
  id: 1, contact_id: null, deal_id: null, title: 'Call Ada', description: '',
  due_date: '', completed: 0, priority: 'medium', repeat: '',
  created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', ...over,
});

const listCalls = () => apiMock.mock.calls.filter(c => String(c[0]).startsWith('/api/crm/todos?'));

/** The row checkbox — the control that drives toggleComplete. */
function completeButton(): HTMLButtonElement {
  const el = container.querySelector('button[aria-label^="Mark "]');
  if (!el) throw new Error('no complete button rendered');
  return el as HTMLButtonElement;
}

async function settle(rounds = 10): Promise<void> {
  for (let i = 0; i < rounds; i++) {
    await act(async () => { await Promise.resolve(); });
  }
}

async function mountWith(row: ReturnType<typeof todo>): Promise<void> {
  apiMock.mockImplementation(async (url: string) => {
    if (String(url).startsWith('/api/crm/todos?')) return { todos: [row] };
    throw new Error(`unexpected request ${url}`);
  });
  act(() => { root.render(<StrictMode><TodosPage /></StrictMode>); });
  await settle();
}

/** Point the mock at a write outcome, keeping the list response intact. */
function onWrite(handler: () => Promise<unknown>, row: ReturnType<typeof todo>): void {
  apiMock.mockImplementation(async (url: string) => {
    if (String(url).startsWith('/api/crm/todos?')) return { todos: [row] };
    return handler();
  });
}

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
  sessionStorage.clear();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

describe('completing a todo', () => {
  it('RE-SWEEPS when the server says the todo repeats', async () => {
    const row = todo({ repeat: 'weekly' });
    await mountWith(row);
    const before = listCalls().length;

    // The spawned next occurrence is a row only the server knows about, so a local patch
    // could not produce it.
    onWrite(async () => todo({ completed: 1, repeat: 'weekly' }), row);
    await act(async () => { completeButton().click(); });
    await settle();

    expect(listCalls().length).toBeGreaterThan(before);
  });

  it('does NOT re-sweep for an ordinary todo — it patches from the response', async () => {
    const row = todo({ repeat: '' });
    await mountWith(row);
    const before = listCalls().length;

    onWrite(async () => todo({ completed: 1, repeat: '' }), row);
    await act(async () => { completeButton().click(); });
    await settle();

    expect(listCalls().length).toBe(before);
  });

  it('reads `repeat` from the SERVER response, not the pre-write row', async () => {
    // The row on screen says it does not repeat; the server's answer says it does — e.g.
    // someone else made it recurring between the sweep and the click. Trusting the stale
    // local copy would silently hide the spawned occurrence until a manual refresh.
    const stale = todo({ repeat: '' });
    await mountWith(stale);
    const before = listCalls().length;

    onWrite(async () => todo({ completed: 1, repeat: 'weekly' }), stale);
    await act(async () => { completeButton().click(); });
    await settle();

    expect(listCalls().length).toBeGreaterThan(before);
  });
});

describe('a failed complete', () => {
  it('keeps the corpus after a definite 4xx refusal', async () => {
    const row = todo();
    await mountWith(row);
    const before = listCalls().length;

    onWrite(async () => { throw new FakeApiError(400); }, row);
    await act(async () => { completeButton().click(); });
    await settle();

    // Nothing was written, so re-sweeping would be a slow way to render the same thing.
    expect(listCalls().length).toBe(before);
  });

  it('re-sweeps after a 5xx, whose outcome is genuinely unknown', async () => {
    const row = todo();
    await mountWith(row);
    const before = listCalls().length;

    onWrite(async () => { throw new FakeApiError(500); }, row);
    await act(async () => { completeButton().click(); });
    await settle();

    expect(listCalls().length).toBeGreaterThan(before);
  });

  it('re-sweeps after a transport failure, which carries no status at all', async () => {
    const row = todo();
    await mountWith(row);
    const before = listCalls().length;

    onWrite(async () => { throw new TypeError('Failed to fetch'); }, row);
    await act(async () => { completeButton().click(); });
    await settle();

    expect(listCalls().length).toBeGreaterThan(before);
  });
});
