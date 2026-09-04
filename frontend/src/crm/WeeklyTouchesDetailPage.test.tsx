// @vitest-environment jsdom
//
// Scope: the drill-down page #146 added — what it asks the server for, how it survives
// navigation between reps, which failures are permanent, and that a row opens the deal
// sheet. The rendering of a row itself belongs to TouchDealRow and is covered through
// the card.
import { StrictMode, act, useEffect } from 'react';
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
// The sheet is wired the way the dashboard wires it, and every one of those callbacks
// is new glue on this page — so the stub exposes them as buttons rather than swallowing
// them, or a forgotten reload() would ship with the suite green.
vi.mock('./components/DealDetailSheet', () => ({
  DealDetailSheet: ({ deal, onClose, onEdit, onStageChange, onRestored }: {
    deal: { id: number };
    onClose: () => void;
    onEdit: (d: { id: number }) => void;
    onStageChange: (d: { id: number }, stage: string) => void;
    onRestored: () => void;
  }) => (
    <div data-testid="sheet">
      {deal.id}
      <button data-testid="sheet-close" onClick={onClose}>close</button>
      <button data-testid="sheet-edit" onClick={() => onEdit(deal)}>edit</button>
      <button data-testid="sheet-stage" onClick={() => onStageChange(deal, 'won')}>stage</button>
      <button data-testid="sheet-restore" onClick={onRestored}>restore</button>
    </div>
  ),
}));
vi.mock('./components/DealForm', () => ({
  DealForm: ({ deal, onSaved }: { deal: { id: number }; onSaved: () => void }) => (
    <div data-testid="form">
      {deal.id}
      <button data-testid="form-saved" onClick={onSaved}>saved</button>
    </div>
  ),
}));

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
  truncated: false,
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
  it('forwards a custom range verbatim and does no date arithmetic of its own', async () => {
    await renderAt('/crm/touches/3?start=2026-06-16&end=2026-06-20');

    expect(detailCalls()).toEqual([
      '/api/crm/dashboard/weekly-touches/detail?owner=3&start=2026-06-16&end=2026-06-20',
    ]);
  });

  it('asks for the rolling default when the link carries no range', async () => {
    // Which is the point of NOT forwarding the card's frozen instants: the server
    // re-resolves the same window kind, so a deal touched since the card rendered is
    // still in the list rather than having fallen past a frozen upper bound.
    await renderAt('/crm/touches/3');

    expect(detailCalls()).toEqual([
      '/api/crm/dashboard/weekly-touches/detail?owner=3',
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

  it.each(['0', '2147483648'])(
    'rejects the out-of-range owner %s as a bad rep, not a bad date range',
    async owner => {
      // The server 400s these. Left to the generic 4xx branch the page would blame the
      // window and send the user to fix the wrong half of the URL.
      await renderAt(`/crm/touches/${owner}`);
      expect(container.textContent).toContain('No such rep');
      expect(detailCalls()).toEqual([]);
    },
  );

  it('says so when the list is only a prefix', async () => {
    api.mockResolvedValue({ ...detail, truncated: true });
    await renderAt('/crm/touches/3');
    expect(container.textContent).toContain('Showing the first 2 touched deals');
  });

  it('shows no truncation notice on a complete list', async () => {
    await renderAt('/crm/touches/3');
    expect(container.textContent).not.toContain('Showing the first');
  });

  it('closes an open sheet when the route moves to another rep', async () => {
    // Otherwise one rep's deal stays on screen over another rep's list.
    await renderAt('/crm/touches/3');
    api.mockResolvedValueOnce({ id: 41, title: 'Deal 41', stage: 'proposal' });
    await act(async () => {
      (container.querySelector('[role="button"]') as HTMLElement).click();
    });
    expect(container.querySelector('[data-testid="sheet"]')).not.toBeNull();

    api.mockResolvedValue({ ...detail, rep: { ...detail.rep, user_id: 4, name: 'Sam' } });
    await act(async () => { nav.go('/crm/touches/4'); });
    expect(container.querySelector('[data-testid="sheet"]')).toBeNull();
  });

  it('does not resurrect the sheet on returning to the rep it was opened from', async () => {
    // The failure this catches is subtle: hiding the modal while the path differs leaves
    // it in state, so 3 → 4 → 3 makes it match again and it reappears unbidden. Only
    // destroying the state (remounting on the path) actually closes it.
    await renderAt('/crm/touches/3');
    api.mockResolvedValueOnce({ id: 41, title: 'Deal 41', stage: 'proposal' });
    await act(async () => {
      (container.querySelector('[role="button"]') as HTMLElement).click();
    });
    expect(container.querySelector('[data-testid="sheet"]')).not.toBeNull();

    await act(async () => { nav.go('/crm/touches/4'); });
    await act(async () => { nav.go('/crm/touches/3'); });

    expect(container.querySelector('[data-testid="sheet"]')).toBeNull();
    expect(container.textContent).toContain('Dana Reyes');
  });

  it('does not resurrect the edit form either', async () => {
    await renderAt('/crm/touches/3');
    api.mockResolvedValueOnce({ id: 41, title: 'Deal 41', stage: 'proposal' });
    await act(async () => {
      (container.querySelector('[role="button"]') as HTMLElement).click();
    });
    await act(async () => {
      (container.querySelector('[data-testid="sheet-edit"]') as HTMLElement).click();
    });
    expect(container.querySelector('[data-testid="form"]')).not.toBeNull();

    await act(async () => { nav.go('/crm/touches/4'); });
    await act(async () => { nav.go('/crm/touches/3'); });

    expect(container.querySelector('[data-testid="form"]')).toBeNull();
  });

  it('still opens deals under StrictMode\'s double effect cycle', async () => {
    // `main.tsx` renders the app inside <StrictMode>, whose development cycle is
    // setup → cleanup → setup. A liveness flag cleared only in cleanup stays false
    // afterwards and swallows every deal the user clicks — in dev only, which is exactly
    // where it would be met and mistaken for a broken endpoint.
    await act(async () => root.render(<StrictMode>{page('/crm/touches/3')}</StrictMode>));
    api.mockResolvedValueOnce({ id: 41, title: 'Deal 41', stage: 'proposal' });
    await act(async () => {
      (container.querySelector('[role="button"]') as HTMLElement).click();
    });

    expect(container.querySelector('[data-testid="sheet"]')?.textContent).toContain('41');
  });

  it('opens the deal that was clicked LAST when two fetches race', async () => {
    // Two deal requests race on one unchanged URL, so the list's path key cannot decide
    // between them — only request order can. Clicking A then B must show B even when A
    // answers second.
    await renderAt('/crm/touches/3');
    let resolveA: (v: unknown) => void = () => {};
    api.mockReturnValueOnce(new Promise(res => { resolveA = res; }));
    const rows = container.querySelectorAll('[role="button"]');
    await act(async () => { (rows[0] as HTMLElement).click(); });

    api.mockResolvedValueOnce({ id: 42, title: 'Deal 42', stage: 'proposal' });
    await act(async () => { (rows[1] as HTMLElement).click(); });
    expect(container.querySelector('[data-testid="sheet"]')?.textContent).toContain('42');

    await act(async () => { resolveA({ id: 41, title: 'Deal 41', stage: 'proposal' }); });
    expect(container.querySelector('[data-testid="sheet"]')?.textContent).toContain('42');
  });

  it('does not toast about a deal the user has already navigated away from', async () => {
    await renderAt('/crm/touches/3');
    let rejectA: (e: unknown) => void = () => {};
    api.mockReturnValueOnce(new Promise((_res, rej) => { rejectA = rej; }));
    await act(async () => {
      (container.querySelector('[role="button"]') as HTMLElement).click();
    });

    api.mockResolvedValue({ ...detail, rep: { ...detail.rep, user_id: 4, name: 'Sam' } });
    await act(async () => { nav.go('/crm/touches/4'); });
    await act(async () => { rejectA(new ApiError('gone', 404)); });

    expect(toast.error).not.toHaveBeenCalled();
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

  it('opens the deal sheet with the deal whose row was clicked', async () => {
    await renderAt('/crm/touches/3');
    api.mockResolvedValueOnce({ id: 42, title: 'Deal 42', stage: 'proposal' });
    // The SECOND row, and the fetched id is asserted — clicking the first row against an
    // argument-independent mock would pass even if the handler always opened deal 41.
    const rows = container.querySelectorAll('[role="button"]');
    await act(async () => { (rows[1] as HTMLElement).click(); });

    expect(api).toHaveBeenCalledWith('/api/crm/deals/42');
    expect(container.querySelector('[data-testid="sheet"]')?.textContent).toContain('42');
  });

  it('refetches the list after the sheet closes, restores, or changes a stage', async () => {
    // Each of these is a separate callback on this page; a missing reload() in any one
    // leaves the user looking at numbers their own action just invalidated.
    for (const trigger of ['sheet-close', 'sheet-restore']) {
      api.mockReset();
      api.mockResolvedValue(detail);
      await renderAt('/crm/touches/3');
      api.mockResolvedValueOnce({ id: 41, title: 'Deal 41', stage: 'proposal' });
      await act(async () => {
        (container.querySelector('[role="button"]') as HTMLElement).click();
      });
      const before = detailCalls().length;
      await act(async () => {
        (container.querySelector(`[data-testid="${trigger}"]`) as HTMLElement).click();
      });
      expect(detailCalls().length).toBe(before + 1);
    }
  });

  it('writes the stage change and then refetches', async () => {
    await renderAt('/crm/touches/3');
    api.mockResolvedValueOnce({ id: 41, title: 'Deal 41', stage: 'proposal' });
    await act(async () => {
      (container.querySelector('[role="button"]') as HTMLElement).click();
    });

    const before = detailCalls().length;
    await act(async () => {
      (container.querySelector('[data-testid="sheet-stage"]') as HTMLElement).click();
    });

    // A non-lost move is `PUT /api/crm/deals/:id` (stageWriteRequest reserves
    // mark-lost for a close carrying a reason), and the read path is the same URL —
    // so the METHOD is what separates the write from the fetch that opened the sheet.
    expect(api.mock.calls.some(
      c => String(c[0]) === '/api/crm/deals/41'
        && (c[1] as { method?: string } | undefined)?.method === 'PUT',
    )).toBe(true);
    expect(detailCalls().length).toBe(before + 1);
    expect(container.querySelector('[data-testid="sheet"]')).toBeNull();
  });

  it('opens the edit form from the sheet and refetches once it saves', async () => {
    await renderAt('/crm/touches/3');
    api.mockResolvedValueOnce({ id: 41, title: 'Deal 41', stage: 'proposal' });
    await act(async () => {
      (container.querySelector('[role="button"]') as HTMLElement).click();
    });

    await act(async () => {
      (container.querySelector('[data-testid="sheet-edit"]') as HTMLElement).click();
    });
    // The sheet gives way to the form, carrying the same deal.
    expect(container.querySelector('[data-testid="sheet"]')).toBeNull();
    expect(container.querySelector('[data-testid="form"]')?.textContent).toContain('41');

    const before = detailCalls().length;
    await act(async () => {
      (container.querySelector('[data-testid="form-saved"]') as HTMLElement).click();
    });
    expect(container.querySelector('[data-testid="form"]')).toBeNull();
    expect(detailCalls().length).toBe(before + 1);
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

  it('ignores a slow refetch that lost its race WITHIN one mount', async () => {
    // Navigating away and back remounts the view (it is keyed on the request), so a
    // stale response then lands on an unmounted instance and setState is a no-op — the
    // id guard is never consulted. The race it actually decides is two refetches of the
    // SAME url inside one mount, which every sheet close triggers.
    await renderAt('/crm/touches/3');

    // Reload #2: open a deal and close the sheet, holding the refetch open.
    api.mockResolvedValueOnce({ id: 41, title: 'Deal 41', stage: 'proposal' });
    await act(async () => {
      (container.querySelector('[role="button"]') as HTMLElement).click();
    });
    let resolveStale: (v: unknown) => void = () => {};
    api.mockReturnValueOnce(new Promise(res => { resolveStale = res; }));
    await act(async () => {
      (container.querySelector('[data-testid="sheet-close"]') as HTMLElement).click();
    });

    // Reload #3, same url, resolves FIRST with new numbers.
    api.mockResolvedValueOnce({ id: 41, title: 'Deal 41', stage: 'proposal' });
    await act(async () => {
      (container.querySelector('[role="button"]') as HTMLElement).click();
    });
    api.mockResolvedValueOnce({
      ...detail, rep: { ...detail.rep, touches: 9, open_deals: 9 },
    });
    await act(async () => {
      (container.querySelector('[data-testid="sheet-close"]') as HTMLElement).click();
    });
    expect(container.textContent).toContain('9 of 9 open deals touched');

    // Reload #2 finally answers, with the older payload and the same path.
    await act(async () => { resolveStale(detail); });
    expect(container.textContent).toContain('9 of 9 open deals touched');
    expect(container.textContent).not.toContain('2 of 12 open deals touched');
  });
});
