// @vitest-environment jsdom
//
// The Dashboard is what starts the background warm-up (#281): only once its own read has settled,
// whether that read succeeded or failed, so it never competes with a whole-corpus sweep.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => vi.fn());
vi.mock('../core/api/client', () => ({
  api,
  ApiError: class ApiError extends Error { status = 500; },
}));
vi.mock('../shared/toast', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));
const startWarmUp = vi.hoisted(() => vi.fn(async () => {}));
vi.mock('./warmQueue', () => ({ startWarmUp }));

const { CrmDashboardPage } = await import('./CrmDashboardPage');
const { ActiveRecordProvider } = await import('./RecordContext');
const { AuthProvider } = await import('../core/auth/AuthContext');
const { WarmViewerContext } = await import('./warmCache');

const EMAIL = 'ana@example.com';
const DASHBOARD = {
  total_contacts: 0, total_companies: 0, contacts_by_status: {}, pipeline_by_stage: [],
  total_pipeline_value: 0, overdue_todos: 0, pending_todos: 0, recent_activity: [], top_deals: [],
};
let container: HTMLDivElement;
let root: Root;

/** Hold the dashboard read so "still loading" is a state the test can look at. */
function holdDashboard() {
  let settle!: { resolve: () => void; reject: () => void };
  const pending = new Promise((resolve, reject) => {
    settle = { resolve: () => resolve(DASHBOARD), reject: () => reject(new Error('500')) };
  });
  api.mockImplementation((path: string) => (path === '/api/crm/dashboard' ? pending : new Promise(() => {})));
  return settle;
}

beforeEach(() => {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: false, media: query, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {},
    addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  }));
  api.mockReset();
  startWarmUp.mockClear();
  sessionStorage.clear();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

async function render() {
  await act(async () => root.render(
    <AuthProvider>
      <MemoryRouter>
        <WarmViewerContext.Provider value={EMAIL}>
          <ActiveRecordProvider><CrmDashboardPage /></ActiveRecordProvider>
        </WarmViewerContext.Provider>
      </MemoryRouter>
    </AuthProvider>,
  ));
}

describe('the Dashboard starts the warm-up (#281)', () => {
  it('waits for its own read, then starts the queue for the signed-in user', async () => {
    const read = holdDashboard();
    await render();
    expect(startWarmUp).not.toHaveBeenCalled();
    await act(async () => read.resolve());
    expect(startWarmUp).toHaveBeenCalledWith(EMAIL);
  });

  it('starts it after a FAILED read too', async () => {
    const read = holdDashboard();
    await render();
    await act(async () => read.reject());
    expect(startWarmUp).toHaveBeenCalledWith(EMAIL);
  });
});
