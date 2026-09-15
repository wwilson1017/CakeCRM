// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { CrmToday, CrmTodayDealItem, CrmTodayItem } from '../../core/types';
import { TODAY_MAX_RETRIES } from '../todayPanel';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => vi.fn());
const navigate = vi.hoisted(() => vi.fn());
const users = vi.hoisted(() => ({ list: [{ id: 3 }, { id: 4 }] as { id: number }[] }));

const toast = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));

vi.mock('../../core/api/client', () => ({ api }));
vi.mock('../../shared/toast', () => ({ toast }));
vi.mock('react-router-dom', () => ({ useNavigate: () => navigate }));
vi.mock('../../core/auth/AuthContext', () => ({ useAuth: () => ({ currentUser: { id: 3 } }) }));
vi.mock('../useUsers', () => ({
  UNASSIGNED_LABEL: 'Unassigned',
  useUsers: () => ({ users: users.list }),
}));

const { TodayPanel } = await import('./TodayPanel');

function taskItem(id: number, over: Partial<CrmTodayItem> = {}): CrmTodayItem {
  return { kind: 'task', id, rank: 4, why: 'due_today', title: `Task ${id}`,
           due_date: '2026-06-05', owner_id: 3, ...over } as CrmTodayItem;
}

// Ids DESCEND while ranks ASCEND, so any client-side re-sort by id or rank would
// produce a different order than the server sent — that is what makes the
// "renders in payload order" assertion able to fail.
const PAYLOAD: CrmToday = {
  date: '2026-06-05',
  next_refresh_at: '2026-06-06T05:00:00Z',
  scope: { owner_id: 3 },
  items: [
    taskItem(90, { rank: 1, why: 'starred', title: 'Starred one' }),
    taskItem(80, { rank: 3, why: 'overdue', title: 'Overdue one', due_date: '2026-06-01' }),
    taskItem(70, { rank: 3, why: 'overdue', title: 'Overdue two', due_date: '2026-06-02' }),
    taskItem(60, { title: 'Due today one', owner_id: null }),
    taskItem(50, { title: 'Due today two' }),
    taskItem(40, { title: 'Due today three' }),
    taskItem(30, { title: 'Due today four' }),
  ],
};

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  api.mockReset();
  navigate.mockReset();
  toast.error.mockReset();
  vi.spyOn(console, 'error').mockImplementation(() => {});
  users.list = [{ id: 3 }, { id: 4 }];
  sessionStorage.clear();
  api.mockResolvedValue(PAYLOAD);
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

async function render(node: React.ReactElement) {
  await act(async () => root.render(node));
}

function rowTitles(): string[] {
  return [...container.querySelectorAll('button')]
    .map(b => b.textContent ?? '')
    .filter(t => t.includes('Task ') || t.includes('one') || t.includes('two')
              || t.includes('three') || t.includes('four'));
}

function buttonByText(text: string): HTMLButtonElement {
  const found = [...container.querySelectorAll('button')].find(b => b.textContent?.includes(text));
  if (!found) throw new Error(`no button containing ${text}: ${container.textContent}`);
  return found as HTMLButtonElement;
}

describe('TodayPanel', () => {
  it('renders rows in the server\'s order and never re-sorts them', async () => {
    await render(<TodayPanel />);
    const titles = rowTitles();
    expect(titles[0]).toContain('Starred one');
    expect(titles[1]).toContain('Overdue one');
    expect(titles[2]).toContain('Overdue two');
    expect(titles[3]).toContain('Due today one');
  });

  it('collapses to five rows with a "+N more today" expander, and back', async () => {
    await render(<TodayPanel />);
    expect(rowTitles()).toHaveLength(5);
    const more = buttonByText('+2 more today');

    await act(async () => { more.click(); });
    expect(rowTitles()).toHaveLength(7);

    await act(async () => { buttonByText('Show less').click(); });
    expect(rowTitles()).toHaveLength(5);
  });

  it('renders one quiet line and no rows when nothing needs you', async () => {
    api.mockResolvedValue({ ...PAYLOAD, items: [] });
    await render(<TodayPanel />);
    expect(container.textContent).toContain('Nothing needs you today.');
    expect(rowTitles()).toHaveLength(0);
  });

  it('stays hidden entirely when the first load fails', async () => {
    api.mockRejectedValue(new Error('offline'));
    await render(<TodayPanel />);
    expect(container.textContent).toBe('');
  });

  it('completes a task through the normal endpoint without navigating', async () => {
    const onMutated = vi.fn();
    await render(<TodayPanel onMutated={onMutated} />);
    api.mockClear();
    api.mockResolvedValue(PAYLOAD);

    const checkbox = container.querySelector('button[aria-label="Complete Starred one"]');
    await act(async () => { (checkbox as HTMLButtonElement).click(); });

    expect(api).toHaveBeenCalledWith('/api/crm/tasks/90/complete', { method: 'PUT' });
    expect(onMutated).toHaveBeenCalled();
    expect(navigate).not.toHaveBeenCalled();  // sibling controls — no bubbling to the row
  });

  it('routes task rows to the tasks page', async () => {
    await render(<TodayPanel />);
    await act(async () => { buttonByText('Starred one').click(); });
    expect(navigate).toHaveBeenCalledWith('/crm/tasks');
  });

  it('badges an unassigned task and not an owned one', async () => {
    await render(<TodayPanel />);
    await act(async () => { buttonByText('+2 more today').click(); });
    expect(buttonByText('Due today one').textContent).toContain('Unassigned');
    expect(buttonByText('Due today two').textContent).not.toContain('Unassigned');
  });

  it('asks for my own items by default and drops the param for everyone', async () => {
    await render(<TodayPanel />);
    expect(api).toHaveBeenCalledWith('/api/crm/dashboard/today?owner_id=3');

    api.mockClear();
    api.mockResolvedValue({ ...PAYLOAD, scope: { owner_id: null } });
    await act(async () => { buttonByText('EVERYONE').click(); });
    expect(api).toHaveBeenCalledWith('/api/crm/dashboard/today');
  });

  it('hides the scope control on a single-seat install', async () => {
    users.list = [{ id: 3 }];
    await render(<TodayPanel />);
    expect(container.textContent).not.toContain('EVERYONE');
  });

  it('marks rows busy while the payload still describes the previous scope', async () => {
    await render(<TodayPanel />);
    expect(container.querySelector('[aria-busy="true"]')).toBeNull();

    // The response still says owner_id 3 while the UI has switched to everyone.
    api.mockResolvedValue(PAYLOAD);
    await act(async () => { buttonByText('EVERYONE').click(); });
    expect(container.querySelector('[aria-busy="true"]')).not.toBeNull();
  });

  it('tells the user when completing a task fails, and still reconciles', async () => {
    const onMutated = vi.fn();
    await render(<TodayPanel onMutated={onMutated} />);
    api.mockClear();
    api.mockRejectedValueOnce(new Error('gone'));   // the PUT
    api.mockResolvedValue(PAYLOAD);                 // the reconciling refetch

    const checkbox = container.querySelector('button[aria-label="Complete Starred one"]');
    await act(async () => { (checkbox as HTMLButtonElement).click(); });

    expect(toast.error).toHaveBeenCalled();
    // The write may still have landed, so the panel refetches rather than guessing.
    expect(api).toHaveBeenCalledWith('/api/crm/dashboard/today?owner_id=3');
    expect(onMutated).toHaveBeenCalled();
  });

  it('refetches when the page bumps refreshKey after a mutation elsewhere', async () => {
    await render(<TodayPanel refreshKey={1} />);
    api.mockClear();
    api.mockResolvedValue(PAYLOAD);

    await render(<TodayPanel refreshKey={2} />);
    expect(api).toHaveBeenCalledWith('/api/crm/dashboard/today?owner_id=3');
  });

  it('puts the scope control back when a scope switch fails, rather than lying', async () => {
    await render(<TodayPanel />);
    expect(buttonByText('EVERYONE').getAttribute('aria-pressed')).toBe('false');

    // The switch fails; the rows on screen still describe "mine".
    api.mockRejectedValueOnce(new Error('offline'));
    api.mockResolvedValue(PAYLOAD);
    await act(async () => { buttonByText('EVERYONE').click(); });

    expect(toast.error).toHaveBeenCalled();
    expect(buttonByText('MINE').getAttribute('aria-pressed')).toBe('true');
    expect(container.querySelector('[aria-busy="true"]')).toBeNull();
  });

  it('reloads at the SERVER boundary, which is NOT the browser midnight', async () => {
    vi.useFakeTimers();
    try {
      // The two boundaries must be genuinely different or this test proves nothing.
      // The browser is America/Chicago (pinned by vitest.config); the server is on UTC,
      // the DEFAULT install (TIMEZONE unset). From 18:00Z on 06-05:
      //   server boundary  2026-06-06T00:00:00Z  =  6h away
      //   browser midnight 2026-06-06T05:00:00Z  = 11h away
      // A panel keyed on the browser's day fires at 11h and so fails the 7h assertion.
      const utcServer = { ...PAYLOAD, next_refresh_at: '2026-06-06T00:00:00Z' };
      api.mockResolvedValue(utcServer);
      vi.setSystemTime(Date.parse('2026-06-05T18:00:00Z'));
      await render(<TodayPanel />);
      api.mockClear();
      api.mockResolvedValue(utcServer);

      await act(async () => { await vi.advanceTimersByTimeAsync(5 * 60 * 60 * 1000); });
      expect(api).not.toHaveBeenCalled();   // still before the server boundary

      await act(async () => { await vi.advanceTimersByTimeAsync(2 * 60 * 60 * 1000); });
      expect(api).toHaveBeenCalledWith('/api/crm/dashboard/today?owner_id=3');
    } finally {
      vi.useRealTimers();
    }
  });

  it('does not storm the server when the refetch at the boundary is slow', async () => {
    vi.useFakeTimers();
    try {
      const utcServer = { ...PAYLOAD, next_refresh_at: '2026-06-06T00:00:00Z' };
      api.mockResolvedValue(utcServer);
      vi.setSystemTime(Date.parse('2026-06-05T18:00:00Z'));
      await render(<TodayPanel />);

      // The boundary refetch never settles. Re-arming off the (still stale) payload
      // would clamp to the 30s floor and fire repeatedly, each tick invalidating the
      // in-flight response — a request storm that never updates the panel.
      api.mockClear();
      api.mockReturnValue(new Promise(() => {}));
      await act(async () => { await vi.advanceTimersByTimeAsync(6 * 60 * 60 * 1000); });
      expect(api).toHaveBeenCalledTimes(1);

      await act(async () => { await vi.advanceTimersByTimeAsync(10 * 60 * 1000); });
      expect(api).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it('retries a failed first load instead of staying hidden forever', async () => {
    vi.useFakeTimers();
    try {
      api.mockRejectedValue(new Error('blip'));
      await render(<TodayPanel />);
      expect(container.textContent).toBe('');

      api.mockResolvedValue(PAYLOAD);
      await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
      expect(container.textContent).toContain('Starred one');
    } finally {
      vi.useRealTimers();
    }
  });

  it('gives up after a bounded number of retries rather than polling forever', async () => {
    vi.useFakeTimers();
    try {
      api.mockRejectedValue(new Error('down'));
      await render(<TodayPanel />);
      api.mockClear();

      await act(async () => { await vi.advanceTimersByTimeAsync(24 * 60 * 60 * 1000); });
      expect(api.mock.calls.length).toBeLessThanOrEqual(TODAY_MAX_RETRIES);
    } finally {
      vi.useRealTimers();
    }
  });
});

// ── Hot deal rows (issue #131) ───────────────────────────────────────────────

function dealItem(id: number, over: Partial<CrmTodayDealItem> = {}): CrmTodayDealItem {
  return { kind: 'deal', id, rank: null, why: 'hot', title: `Deal ${id}`,
           value: 30000, days_since_touch: 12, owner_id: 3, ...over };
}

function withItems(items: CrmTodayItem[]): CrmToday {
  return { ...PAYLOAD, items };
}

function rowButton(text: string): HTMLButtonElement | undefined {
  return [...container.querySelectorAll('button')]
    .find(b => b.textContent?.includes(text)) as HTMLButtonElement | undefined;
}

describe('TodayPanel hot deals', () => {
  it('opens a deal on the board through the shared deep-link shape', async () => {
    api.mockResolvedValue(withItems([dealItem(42, { rank: 2, why: 'hot_stale' })]));
    await render(<TodayPanel />);

    await act(async () => { rowButton('Deal 42')!.click(); });
    // The one string both languages agree on, built by `dealDeepLink` rather than written
    // out here — `test_crm_deal_links.py` reads that module and pins it against Python.
    expect(navigate).toHaveBeenCalledWith('/crm/pipeline?deal=42');
  });

  it('offers no complete checkbox on a deal row', async () => {
    api.mockResolvedValue(withItems([dealItem(42, { rank: 2, why: 'hot_stale' })]));
    await render(<TodayPanel />);
    expect(container.querySelector('button[aria-label^="Complete"]')).toBeNull();
  });

  it('wears the temperature as a labelled glyph, not a colour alone', async () => {
    api.mockResolvedValue(withItems([dealItem(42, { rank: 2, why: 'hot_stale' })]));
    await render(<TodayPanel />);

    const glyph = container.querySelector('[role="img"]');
    expect(glyph?.getAttribute('aria-label')).toBe('Deal temperature: Hot');
    // Read-only: #125's control renders a <button> only when handed an onCycle, and this
    // panel deliberately hands it none — a tab stop here would sit inside the row's own
    // open-the-deal button.
    expect(glyph?.tagName).toBe('SPAN');
  });

  it('shows the idle days and the value the issue asks for', async () => {
    api.mockResolvedValue(withItems([dealItem(42, { rank: 2, why: 'hot_stale' })]));
    await render(<TodayPanel />);
    expect(rowButton('Deal 42')!.textContent).toContain('idle 12d · $30K');
  });

  it('keeps a recently touched hot deal out of the collapsed card entirely', async () => {
    // One commitment and one unranked deal — well under five rows, which is exactly the
    // case a sixth rank could not have kept apart.
    api.mockResolvedValue(withItems([
      taskItem(80, { rank: 3, why: 'overdue', title: 'Overdue one', due_date: '2026-06-01' }),
      dealItem(42),
    ]));
    await render(<TodayPanel />);

    expect(rowButton('Deal 42')).toBeUndefined();
    expect(rowButton('Overdue one')).toBeDefined();

    await act(async () => { rowButton('+1 more today')!.click(); });
    expect(rowButton('Deal 42')).toBeDefined();
  });

  it('swaps the expander for Show less on a panel of fewer than five rows', async () => {
    // The pre-#131 conditions were `hiddenCount > 0` and `items.length > 5`, and BOTH are
    // wrong once hidden rows can be unranked rather than merely past the fifth slot: the
    // first leaves "+N more today" on screen beside "Show less", and the second never
    // renders "Show less" at all, stranding the reader in the expanded view. Two items
    // here, so `items.length > 5` is false and the old guard cannot pass this.
    api.mockResolvedValue(withItems([
      taskItem(80, { rank: 3, why: 'overdue', title: 'Overdue one', due_date: '2026-06-01' }),
      dealItem(42),
    ]));
    await render(<TodayPanel />);

    await act(async () => { rowButton('+1 more today')!.click(); });
    expect(rowButton('Show less')).toBeDefined();
    expect(rowButton('+1 more today')).toBeUndefined();

    await act(async () => { rowButton('Show less')!.click(); });
    expect(rowButton('+1 more today')).toBeDefined();
    expect(rowButton('Show less')).toBeUndefined();
  });

  it('says nothing needs you, and still offers the expander, when every row is unranked', async () => {
    api.mockResolvedValue(withItems([dealItem(42), dealItem(43)]));
    await render(<TodayPanel />);

    expect(container.textContent).toContain('Nothing needs you today.');
    expect(rowButton('+2 more today')).toBeDefined();

    await act(async () => { rowButton('+2 more today')!.click(); });
    expect(container.textContent).not.toContain('Nothing needs you today.');
    expect(rowButton('Deal 42')).toBeDefined();
  });

  it('badges an unassigned deal, the same as an unassigned task', async () => {
    api.mockResolvedValue(withItems([
      dealItem(42, { rank: 2, why: 'hot_stale', owner_id: null }),
      dealItem(43, { rank: 2, why: 'hot_stale', owner_id: 3 }),
    ]));
    await render(<TodayPanel />);

    expect(rowButton('Deal 42')!.textContent).toContain('Unassigned');
    expect(rowButton('Deal 43')!.textContent).not.toContain('Unassigned');
  });

  it('hides the unassigned badge on a single-seat install', async () => {
    users.list = [{ id: 3 }];
    api.mockResolvedValue(withItems([dealItem(42, { rank: 2, why: 'hot_stale', owner_id: null })]));
    await render(<TodayPanel />);
    expect(rowButton('Deal 42')!.textContent).not.toContain('Unassigned');
  });
});
