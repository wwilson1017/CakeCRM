// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { CrmDeal } from '../../core/types';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => vi.fn());
vi.mock('../../core/api/client', () => ({ api }));
vi.mock('../useUsers', () => ({
  useUsers: () => ({ nameFor: (id: number | null) => id ? 'Dana Reyes' : 'Unassigned' }),
}));
const { ClosedSalesCard } = await import('./ClosedSalesCard');
let container: HTMLDivElement;
let root: Root;
const opened = vi.fn();
const deal = (id: number, closed_on: string | null, currency = 'USD'): CrmDeal => ({
  id, closed_on, currency, title: `Sale ${id}`, stage: 'won', value: 100,
  owner_id: id === 1 ? 1 : null, contact_id: null, company_id: null,
  notes: '', probability: 100, expected_close_date: '', created_at: '', updated_at: '',
});
beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-11-01T18:00:00Z'));
  api.mockReset();
  opened.mockReset();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.useRealTimers();
});
async function render(refreshKey = 0) {
  await act(async () => root.render(<ClosedSalesCard refreshKey={refreshKey} onOpenDeal={opened} />));
}
it('uses the Pipeline window, sorts newest then id descending, and opens deals', async () => {
  api.mockResolvedValue({ deals: [deal(1, '2026-10-25'), deal(2, '2026-10-24'),
    deal(3, null), deal(4, '2026-11-01'), deal(5, '2026-11-01')] });
  await render();
  expect(api).toHaveBeenCalledWith('/api/crm/deals?stage=won&sort_by=closed_on&limit=1000');
  const rows = [...container.querySelectorAll('button')];
  expect(rows.map(r => r.textContent?.match(/Sale \d+/)?.[0])).toEqual(['Sale 5', 'Sale 4', 'Sale 1']);
  expect(container.textContent).toContain('3 sales');
  expect(container.textContent).toContain('Dana Reyes');
  expect(container.textContent).toContain('Unassigned');
  await act(async () => rows[0].click());
  expect(opened).toHaveBeenCalledWith(5);
});
it('shows an empty state without AI configuration', async () => {
  api.mockResolvedValue({ deals: [] });
  await render();
  expect(container.textContent).toContain('0 sales');
  expect(container.textContent).toContain('No sales closed in the last 7 days.');
});
it('keeps mixed currency totals separate', async () => {
  api.mockResolvedValue({ deals: [deal(1, '2026-11-01'), deal(2, '2026-11-01', 'EUR')] });
  await render();
  expect(container.textContent).toContain('$100');
  expect(container.textContent).toContain('€100');
  expect(container.textContent).not.toContain('200');
});
// Rendering the full 1000-row cap in jsdom needs more than the default five seconds.
it('marks a full in-window result as potentially truncated', async () => {
  api.mockResolvedValue({ deals: Array.from({ length: 1000 }, (_, id) => deal(id, '2026-10-25')) });
  await render();
  expect(container.textContent).toContain('1000+ sales');
  expect(container.textContent).toContain('at least $100,000');
}, 20000);
it('does not mark a full response when its oldest row is outside the window', async () => {
  api.mockResolvedValue({ deals: Array.from({ length: 1000 }, (_, id) => deal(id, id === 0 ? '2026-11-01' : '2026-10-24')) });
  await render();
  expect(container.textContent).toContain('1 sale');
  expect(container.textContent).not.toContain('at least');
});
it('shows loading and failure, then refetches after a mutation', async () => {
  api.mockReturnValue(new Promise(() => {}));
  await render();
  expect(container.textContent).toContain('Loading closed sales');
  api.mockRejectedValue(new Error('offline'));
  await render(1);
  expect(container.querySelector('[role="alert"]')?.textContent).toContain("Couldn't load closed sales");
  api.mockResolvedValue({ deals: [deal(1, '2026-11-01', 'invalid')] });
  await render(2);
  expect(container.textContent).toContain('1 sale');
  expect(container.textContent).toContain('invalid 100');
});
