// @vitest-environment jsdom
//
// Scope: the drill-down wiring #56 added — rows become activators that open the deal
// sheet — and the per-rep grouping #146 added on top of it. The card's window math is
// #76's and is not re-tested here.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => vi.fn());
vi.mock('../../core/api/client', () => ({ api }));

const { MemoryRouter } = await import('react-router-dom');
const { WeeklyTouchesCard } = await import('./WeeklyTouchesCard');

const window_ = { start: '2026-08-15T00:00:00+00:00', end: '2026-08-22T00:00:00+00:00',
                  label: 'Last 7 days', custom: false };

const deal = (id: number, owner_id: number | null, touch_count: number | null, title: string,
              company_name: string | null) => ({
  id, title, value: 42000, stage: 'proposal', owner_id, touch_count,
  touched_at: '2026-08-18T00:00:00+00:00', contact_name: 'Dana', company_name,
});

/** One rep, so the card renders flat — which is what keeps #56's assertions unchanged. */
const payload = {
  window: window_,
  total_touches: 7,
  total_open_deals: 2,
  computed_deals: 2,
  reps: [{
    user_id: 3, name: 'Dana Reyes', open_deals: 2, touches: 2,
    deals: [
      deal(41, 3, 4, 'Northwind rollout', 'Northwind'),
      deal(42, 3, 3, 'Acme refresh', 'Acme'),
    ],
  }],
};

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  api.mockReset();
  api.mockResolvedValue(payload);
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

async function render(node: React.ReactElement) {
  // The card renders <Link> since #146, so it needs a router in context.
  await act(async () => root.render(<MemoryRouter>{node}</MemoryRouter>));
}

function dealRows(): HTMLElement[] {
  return [...container.querySelectorAll('[role="button"]')] as HTMLElement[];
}

function repHeaders(): HTMLElement[] {
  return [...container.querySelectorAll('[aria-expanded]')] as HTMLElement[];
}

function detailLinks(): HTMLAnchorElement[] {
  return [...container.querySelectorAll('a[href^="/crm/touches/"]')] as HTMLAnchorElement[];
}

describe('WeeklyTouchesCard drill-down (issue #56)', () => {
  it('opens the deal sheet on click, passing that row\'s id', async () => {
    const opened: number[] = [];
    await render(<WeeklyTouchesCard onOpenDeal={id => opened.push(id)} />);
    const rows = dealRows();
    expect(rows).toHaveLength(2);
    await act(async () => { rows[1].click(); });
    expect(opened).toEqual([42]);
  });

  it('is keyboard reachable — Enter and Space both activate', async () => {
    const opened: number[] = [];
    await render(<WeeklyTouchesCard onOpenDeal={id => opened.push(id)} />);
    const row = dealRows()[0];
    expect(row.tabIndex).toBe(0);
    for (const key of ['Enter', ' ']) {
      await act(async () => {
        row.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
      });
    }
    expect(opened).toEqual([41, 41]);
  });

  it('ignores other keys so normal typing cannot open a deal', async () => {
    const opened: number[] = [];
    await render(<WeeklyTouchesCard onOpenDeal={id => opened.push(id)} />);
    await act(async () => {
      dealRows()[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'a', bubbles: true }));
    });
    expect(opened).toEqual([]);
  });

  it('stays inert with no handler — no activator role, no pointer affordance', async () => {
    await render(<WeeklyTouchesCard />);
    expect(dealRows()).toHaveLength(0);
    expect(container.textContent).toContain('Northwind rollout');
  });
});

describe('WeeklyTouchesCard per-rep grouping (issue #146)', () => {
  const twoReps = {
    ...payload,
    total_touches: 3,
    total_open_deals: 12,
    // Server order, with Unassigned already sunk last. The card must render this order
    // as given — the sinking itself is pinned by a backend test.
    reps: [
      { user_id: 3, name: 'Dana Reyes', open_deals: 6, touches: 2,
        deals: [deal(41, 3, 4, 'Northwind rollout', 'Northwind'),
                deal(42, 3, null, 'Acme refresh', 'Acme')] },
      { user_id: 4, name: 'Sam Okafor', open_deals: 4, touches: 0, deals: [] },
      { user_id: null, name: 'Unassigned', open_deals: 2, touches: 1,
        deals: [deal(43, null, 9, 'Orphan deal', null)] },
    ],
  };

  it('hides entirely when nothing has ever been computed, reps and touches notwithstanding', async () => {
    // The zero-keys gate is the card's whole degradation story and must stay
    // window-INDEPENDENT: real touches on real deals still render nothing when no
    // provider has ever run.
    api.mockResolvedValue({ ...twoReps, computed_deals: 0 });
    await render(<WeeklyTouchesCard />);
    expect(container.textContent).toBe('');
  });

  it('renders a single rep flat — no expander, but still its owner and its link', async () => {
    await render(<WeeklyTouchesCard onOpenDeal={() => {}} />);
    expect(repHeaders()).toHaveLength(0);
    expect(dealRows()).toHaveLength(2);
    // Hiding the owner here would break #128's unassigned-renders rule and, with no nav
    // entry, leave the detail page unreachable on a single-seat install.
    expect(container.textContent).toContain('Dana Reyes');
    expect(detailLinks()).toHaveLength(1);
  });

  it('collapses each rep until its header is clicked', async () => {
    api.mockResolvedValue(twoReps);
    await render(<WeeklyTouchesCard onOpenDeal={() => {}} />);

    const headers = repHeaders();
    expect(headers).toHaveLength(3);
    expect(headers.every(h => h.getAttribute('aria-expanded') === 'false')).toBe(true);
    expect(dealRows()).toHaveLength(0);

    await act(async () => { headers[0].click(); });
    expect(repHeaders()[0].getAttribute('aria-expanded')).toBe('true');
    expect(repHeaders()[2].getAttribute('aria-expanded')).toBe('false');
    expect(dealRows()).toHaveLength(2);
  });

  it('renders reps in the server\'s order without re-sorting them', async () => {
    api.mockResolvedValue(twoReps);
    await render(<WeeklyTouchesCard />);
    const names = repHeaders().map(h => h.textContent ?? '');

    expect(names[0]).toContain('Dana Reyes');
    expect(names[1]).toContain('Sam Okafor');
    expect(names[2]).toContain('Unassigned');
  });

  it('links Details only for reps with touches, spelling the null bucket "unassigned"', async () => {
    api.mockResolvedValue(twoReps);
    await render(<WeeklyTouchesCard />);
    const hrefs = detailLinks().map(a => a.getAttribute('href') ?? '');

    // Sam touched nothing, so there is nothing to drill into.
    expect(hrefs).toHaveLength(2);
    expect(hrefs[0]).toContain('/crm/touches/3?');
    expect(hrefs[1]).toContain('/crm/touches/unassigned?');
    // The exact window on screen is forwarded, with the offset's `+` encoded — sent raw
    // it would reach the server as a space and be rejected.
    expect(hrefs[0]).toContain('ws=2026-08-15T00%3A00%3A00%2B00%3A00');
  });

  it('shows a per-rep truncation line with a See all link, and only when truncated', async () => {
    api.mockResolvedValue({
      ...twoReps,
      reps: [{ ...twoReps.reps[0], touches: 12 }, twoReps.reps[2]],
    });
    await render(<WeeklyTouchesCard />);
    await act(async () => { repHeaders()[0].click(); });

    expect(container.textContent).toContain('Showing the top 2 of 12 touched deals');
    expect(container.textContent).toContain('See all');

    // The Unassigned rep has 1 touch and 1 deal, so it is complete — no line.
    await act(async () => { repHeaders()[1].click(); });
    expect(container.textContent?.match(/Showing the top/g)).toHaveLength(1);
  });

  it('shows the server\'s totals rather than a client-side sum of the reps', async () => {
    // The invariant that the totals ARE the bucket sums is the backend's; the card must
    // simply trust the envelope, or the two could disagree in different ways.
    api.mockResolvedValue({ ...twoReps, total_touches: 99 });
    await render(<WeeklyTouchesCard />);
    expect(container.textContent).toContain('99');
  });

  it('renders an uncomputed count as a dash rather than hiding the row', async () => {
    api.mockResolvedValue({
      ...payload,
      reps: [{ ...payload.reps[0],
               deals: [payload.reps[0].deals[0], deal(42, 3, null, 'Acme refresh', 'Acme')] }],
    });
    await render(<WeeklyTouchesCard />);
    // Scoped to the count cell: a page-level text check would pass off the explanatory
    // copy, which carries an em dash of its own.
    const cell = container.querySelector('[data-testid="touch-count-42"]');
    expect(cell?.textContent).toBe('—');
  });
});
