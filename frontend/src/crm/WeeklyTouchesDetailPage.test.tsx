// @vitest-environment jsdom
//
// Scope: the drill-down page #146 added — what it asks the server for, how it survives
// navigation between reps (React Router reuses the instance), which failures are
// permanent, and that a row opens the deal sheet. The rendering of a row itself belongs
// to TouchDealRow and is covered through the card.
import { act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => vi.fn());
const toast = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));
// The real ApiError carries `status`, and the page's whole retryable-vs-permanent split
// reads it — so the mock has to be a real class the page can `instanceof`.
const { ApiError } = vi.hoisted(() => {
  class ApiError extends Error {
    status: number;
    detail: string;
    constructor(message: string, status: number, detail = '') {
      super(message);
      this.name = 'ApiError';
      this.status = status;
      this.detail = detail;
    }
  }
  return { ApiError };
});

vi.mock('../core/api/client', () => ({ api, ApiError }));
vi.mock('../shared/toast', () => ({ toast }));
// The sheet and the form are wired identically to the dashboard's; this page's job is
// only to hand them the right deal.
vi.mock('./components/DealDetailSheet', () => ({
  DealDetailSheet: ({ deal }: { deal: { id: number } }) => (
    <div data-testid="sheet">{deal.id}</div>
  ),
}));
vi.mock('./components/DealForm', () => ({ DealForm: () => <div data-testid="form" /> }));

const { MemoryRouter, Route, Routes, useNavigate } = await import('react-router-dom');
const { WeeklyTouchesDetailPage } = await import('./WeeklyTouchesDetailPage');

const deal = (id: number, touch_count: number | null) => ({
  id, title: `Deal ${id}`, value: 1000, stage: 'proposal', owner_id: 3,
  touch_count, touched_at: '2026-08-18T12:00:00+00:00',
  contact_name: 'Dana', company_name: 'Northwind',
});

const detail = {
  window: { start: '2026-08-15T00:00:00+00:00', end: '2026-08-22T00:00:00+00:00',
            label: 'Last 7 days' },
  rep: { user_id: 3, name: 'Dana Reyes', open_deals: 12, touches: 2 },
  deals: [deal(41, 4), deal(42, null)],
};

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  // jsdom implements no media queries; useIsMobile calls matchMedia in an effect.
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: false, media: query, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {},
    addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  }));
  api.mockReset();
  toast.error.mockReset();
  api.mockResolvedValue(detail);
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

// Captures the router's navigate so a test can move BETWEEN reps for real. Re-rendering
// a fresh <MemoryRouter> into the same root does not navigate — React reconciles it in
// place and `initialEntries` is only read on mount — so a test written that way would
// assert against a component whose params never changed.
const nav: { go: (to: string) => void } = { go: () => {} };

function NavCapture() {
  const navigate = useNavigate();
  // In an effect, not during render: assigning module state while rendering is impure and
  // the repo's eslint-plugin-react-hooks config rejects it.
  useEffect(() => { nav.go = navigate; }, [navigate]);
  return null;
}

function page(url: string) {
  return (
    <MemoryRouter initialEntries={[url]}>
      <NavCapture />
      <Routes>
        <Route path="/crm/touches/:owner" element={<WeeklyTouchesDetailPage />} />
      </Routes>
    </MemoryRouter>
  );
}

async function renderAt(url: string) {
  await act(async () => root.render(page(url)));
}

const detailCalls = () =>
  api.mock.calls.map(c => String(c[0])).filter(u => u.includes('weekly-touches/detail'));

describe('WeeklyTouchesDetailPage (issue #146)', () => {
  it('forwards the owner and the exact window instants verbatim', async () => {
    await renderAt(
      '/crm/touches/3?ws=2026-08-15T00%3A00%3A00%2B00%3A00&we=2026-08-22T00%3A00%3A00%2B00%3A00',
    );

    // The client does no date arithmetic: re-resolving "last 7 days" here would list a
    // different week than the number that was clicked.
    expect(detailCalls()).toEqual([
      '/api/crm/dashboard/weekly-touches/detail?owner=3'
      + '&ws=2026-08-15T00%3A00%3A00%2B00%3A00&we=2026-08-22T00%3A00%3A00%2B00%3A00',
    ]);
  });

  it('asks for the unassigned bucket by its literal', async () => {
    await renderAt('/crm/touches/unassigned');
    expect(detailCalls()).toEqual([
      '/api/crm/dashboard/weekly-touches/detail?owner=unassigned',
    ]);
  });

  it('renders the rep, the window label and the ratio', async () => {
    await renderAt('/crm/touches/3');
    expect(container.textContent).toContain('Dana Reyes');
    expect(container.textContent).toContain('Last 7 days');
    expect(container.textContent).toContain('12 open deals touched');
  });

  it('renders an uncomputed count as a dash — no zero-keys gate on this page', async () => {
    await renderAt('/crm/touches/3');
    expect(container.querySelector('[data-testid="touch-count-42"]')?.textContent).toBe('—');
  });

  it('formats the touch date in UTC, matching the card\'s UTC window label', async () => {
    // The suite pins TZ=America/Chicago, so 02:00 UTC on the 19th is the 18th locally.
    // Formatting in the viewer's zone would put a touch outside the very window that
    // selected it.
    api.mockResolvedValue({
      ...detail,
      deals: [{ ...deal(41, 4), touched_at: '2026-08-19T02:00:00+00:00' }],
    });
    await renderAt('/crm/touches/3');
    expect(container.textContent).toContain('Aug 19');
  });

  it('rejects a malformed owner without ever issuing a request', async () => {
    await renderAt('/crm/touches/dana');
    expect(container.textContent).toContain('No such rep');
    expect(detailCalls()).toEqual([]);
  });

  it('treats a 404 as permanent — no Retry', async () => {
    api.mockRejectedValue(new ApiError('nf', 404, 'User not found'));
    await renderAt('/crm/touches/999');
    expect(container.textContent).toContain('No such rep');
    expect(container.textContent).not.toContain('Retry');
  });

  it('treats a 400 as a permanent bad link, not something to retry', async () => {
    // A malformed ws/we is 400. Classifying every non-404 as retryable would offer a
    // Retry that can only ever fail again.
    api.mockRejectedValue(new ApiError('bad', 400, 'we must be after ws'));
    await renderAt('/crm/touches/3?ws=x&we=y');
    expect(container.textContent).toContain("date range isn't valid");
    expect(container.textContent).not.toContain('Retry');
  });

  it('offers Retry on a 500 and refetches when it is pressed', async () => {
    api.mockRejectedValueOnce(new ApiError('boom', 500, ''));
    await renderAt('/crm/touches/3');
    const retry = [...container.querySelectorAll('button')]
      .find(b => b.textContent === 'Retry');
    expect(retry).toBeTruthy();

    await act(async () => { retry!.click(); });
    expect(detailCalls()).toHaveLength(2);
    expect(container.textContent).toContain('Dana Reyes');
  });

  it('opens the deal sheet with the fetched deal when a row is clicked', async () => {
    await renderAt('/crm/touches/3');
    api.mockResolvedValueOnce({ id: 41, title: 'Deal 41', stage: 'proposal' });
    const row = container.querySelector('[role="button"]') as HTMLElement;

    await act(async () => { row.click(); });
    expect(container.querySelector('[data-testid="sheet"]')?.textContent).toBe('41');
  });

  it('never shows one rep\'s rows under another rep\'s URL', async () => {
    // React Router reuses this component instance across /crm/touches/3 → /4, so state
    // keyed only by "is there data" would render Dana's rows under Sam's URL for a frame.
    await renderAt('/crm/touches/3');
    expect(container.textContent).toContain('Dana Reyes');

    let resolveSecond: (v: unknown) => void = () => {};
    api.mockReturnValueOnce(new Promise(res => { resolveSecond = res; }));
    await act(async () => { nav.go('/crm/touches/4'); });
    expect(container.textContent).not.toContain('Dana Reyes');
    expect(container.textContent).toContain('Loading');

    await act(async () => {
      resolveSecond({ ...detail, rep: { ...detail.rep, user_id: 4, name: 'Sam Okafor' } });
    });
    expect(container.textContent).toContain('Sam Okafor');
  });

  it('ignores a slow response that lost its race on the SAME url', async () => {
    // Leaving a rep and coming straight back leaves two requests in flight for the same
    // path, so the path key cannot separate them — this is the race only the monotonic
    // request id catches, and the one a user actually hits by double-clicking Back.
    let resolveStale: (v: unknown) => void = () => {};
    api.mockReturnValueOnce(new Promise(res => { resolveStale = res; }));
    await renderAt('/crm/touches/3');

    api.mockReturnValueOnce(new Promise(() => {}));       // rep 4, never settles
    await act(async () => { nav.go('/crm/touches/4'); });

    api.mockResolvedValueOnce({
      ...detail, rep: { ...detail.rep, touches: 9, open_deals: 9 },
    });
    await act(async () => { nav.go('/crm/touches/3'); });
    expect(container.textContent).toContain('9 of 9 open deals touched');

    // The very first request finally answers, with the OLD numbers and a matching path.
    await act(async () => { resolveStale(detail); });
    expect(container.textContent).toContain('9 of 9 open deals touched');
    expect(container.textContent).not.toContain('12 open deals touched');
  });
});
