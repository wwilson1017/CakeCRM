// @vitest-environment jsdom
//
// Scope: only the drill-down wiring #56 added — rows become activators that open the deal
// sheet (closing the drill-down #76 deferred), and stay inert when no handler is supplied.
// The card's window math and rendering are #76's and are not re-tested here.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => vi.fn());
vi.mock('../../core/api/client', () => ({ api }));

const { WeeklyTouchesCard } = await import('./WeeklyTouchesCard');

const payload = {
  window: { start: '2026-08-15', end: '2026-08-21' },
  total_touches: 7,
  total_open_deals: 2,
  computed_deals: 2,
  deals: [
    { id: 41, title: 'Northwind rollout', value: 42000, touch_count: 4,
      contact_name: 'Dana', company_name: 'Northwind' },
    { id: 42, title: 'Acme refresh', value: 9000, touch_count: 3,
      contact_name: null, company_name: 'Acme' },
  ],
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
  await act(async () => root.render(node));
}

function dealRows(): HTMLElement[] {
  return [...container.querySelectorAll('[role="button"]')] as HTMLElement[];
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
