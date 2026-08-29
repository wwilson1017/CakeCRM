// @vitest-environment jsdom
//
// What this pins is the archived-deal contract of the sheet (issue #83), not its markup:
// a live deal shows no banner and keeps its close buttons; an archived deal shows the
// banner and HIDES Mark Won/Lost (the server would refuse those); Edit hands the form the
// same re-fetched archived state the banner reads, so the two can't disagree; Restore POSTs
// to the right route and hands the server's row UP rather than trusting a refetch; and a
// failed restore leaves the sheet open so the user can try again.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { CrmDeal } from '../../core/types';

const api = vi.hoisted(() => vi.fn());
vi.mock('../../core/api/client', () => ({ api }));
const toast = vi.hoisted(() => ({ error: vi.fn(), info: vi.fn(), success: vi.fn() }));
vi.mock('../../shared/toast', () => ({ toast }));

const { DealDetailSheet } = await import('./DealDetailSheet');
const { ActiveRecordProvider } = await import('../RecordContext');
// The sheet's ActivityTimeline calls useNavigate (it deep-links to contacts), and
// usePublishActiveRecord needs the record context — both are ambient app scaffolding, so
// the test supplies them rather than the component being reshaped to avoid them.
const { MemoryRouter } = await import('react-router-dom');

function deal(over: Partial<CrmDeal> = {}): CrmDeal {
  return {
    id: 7, title: 'Wholesale order', stage: 'lead', value: 1000, probability: 20,
    expected_close_date: '', notes: '', contact_id: null, company_id: null, currency: 'USD',
    created_at: '2026-08-01T00:00:00+00:00', updated_at: '2026-08-01T00:00:00+00:00',
    ...over,
  } as CrmDeal;
}

/** Route every child component's fetch to an inert empty payload so the sheet mounts. */
function routeApi(detail: CrmDeal, over: (path: string) => unknown = () => undefined) {
  api.mockImplementation(async (path: string) => {
    const custom = over(path);
    if (custom !== undefined) return custom;
    if (path.includes('/provenance')) return { provenance: [] };
    if (path.includes('/chatter/')) return { notes: [] };
    if (path.includes('/fields')) return [];
    if (path.includes('/touch-count/')) return null;
    if (path === `/api/crm/deals/${detail.id}`) return detail;
    return null;
  });
}

let container: HTMLDivElement;
let root: Root;

// jsdom implements no CSS media queries at all, so `window.matchMedia` is simply absent
// and `useIsMobile` (used by the sheet's ActivityTimeline) throws on mount. Stub the
// desktop answer — this is a missing jsdom API, not a shim around our own code.
beforeEach(() => {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
  api.mockReset();
  toast.error.mockReset();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

async function render(node: React.ReactElement) {
  await act(async () => {
    root.render(
      <MemoryRouter><ActiveRecordProvider>{node}</ActiveRecordProvider></MemoryRouter>,
    );
  });
}

function button(label: string): HTMLButtonElement | undefined {
  return [...container.querySelectorAll('button')]
    .find(b => b.textContent?.trim() === label) as HTMLButtonElement | undefined;
}

const noop = () => {};

describe('DealDetailSheet — archived deals', () => {
  it('shows no banner on a live deal and keeps the close-out actions', async () => {
    const d = deal();
    routeApi(d);
    await render(
      <DealDetailSheet deal={d} isMobile={false} onClose={noop} onEdit={noop} onStageChange={noop} />,
    );
    expect(container.textContent).not.toContain('ARCHIVED');
    expect(button('Mark Won')).toBeTruthy();
    expect(button('Restore')).toBeUndefined();
  });

  it('banners an archived deal and hides Mark Won/Lost, which the server would refuse', async () => {
    const d = deal({ archived_at: '2026-08-20T00:00:00+00:00' });
    routeApi(d);
    await render(
      <DealDetailSheet deal={d} isMobile={false} onClose={noop} onEdit={noop} onStageChange={noop} />,
    );
    expect(container.textContent).toContain('ARCHIVED');
    expect(button('Restore')).toBeTruthy();
    expect(button('Mark Won')).toBeUndefined();
    expect(button('Mark Lost')).toBeUndefined();
    // Editing an archived deal's other fields is still legal.
    expect(button('Edit')).toBeTruthy();
  });

  it('banners a deal archived AFTER the board loaded, using the fetched detail', async () => {
    // The board hands over a frozen list row. If the assistant archived the deal in
    // between, only the re-fetched detail knows — and `get_deal` deliberately resolves an
    // archived deal, so it does know.
    const stale = deal({ archived_at: null });
    routeApi(deal({ archived_at: '2026-08-25T00:00:00+00:00' }));
    await render(
      <DealDetailSheet deal={stale} isMobile={false} onClose={noop} onEdit={noop} onStageChange={noop} />,
    );
    expect(container.textContent).toContain('ARCHIVED');
    expect(button('Restore')).toBeTruthy();
  });

  it('hands Edit the RE-FETCHED archived state, not the frozen board row', async () => {
    // The same drift as the test above, carried one step further. `DealForm` disables its
    // Stage select on an archived deal because the server refuses the stage change and
    // rejects the WHOLE update — so a form handed the stale row would leave that select
    // enabled and let the user compose an edit that comes back rejected in full, losing
    // every other field they just typed. The banner and the form must agree, and the
    // re-fetched detail is the only one of the two that knows.
    const stale = deal({ archived_at: null });
    const archivedAt = '2026-08-25T00:00:00+00:00';
    routeApi(deal({ archived_at: archivedAt }));
    const onEdit = vi.fn();
    await render(
      <DealDetailSheet deal={stale} isMobile={false} onClose={noop} onEdit={onEdit} onStageChange={noop} />,
    );
    await act(async () => { button('Edit')!.click(); });

    expect(onEdit).toHaveBeenCalledWith(
      expect.objectContaining({ id: stale.id, title: stale.title, archived_at: archivedAt }),
    );
  });

  it('restores through POST /restore and hands the SERVER row up to the host', async () => {
    const d = deal({ archived_at: '2026-08-20T00:00:00+00:00' });
    const restored = deal({ archived_at: null, stage: 'qualified' });
    routeApi(d, path => (path === '/api/crm/deals/7/restore' ? restored : undefined));
    const onRestored = vi.fn();
    await render(
      <DealDetailSheet deal={d} isMobile={false} onClose={noop} onEdit={noop}
        onStageChange={noop} onRestored={onRestored} />,
    );
    await act(async () => { button('Restore')!.click(); });

    expect(api).toHaveBeenCalledWith('/api/crm/deals/7/restore', { method: 'POST' });
    // The authoritative row, not a re-fetch: a silent refresh can fail invisibly and
    // leave the board still showing the deal as archived.
    expect(onRestored).toHaveBeenCalledWith(restored);
  });

  it('keeps the sheet open and says so when a restore fails', async () => {
    const d = deal({ archived_at: '2026-08-20T00:00:00+00:00' });
    routeApi(d, path => {
      if (path === '/api/crm/deals/7/restore') throw new Error('boom');
      return undefined;
    });
    const onRestored = vi.fn();
    await render(
      <DealDetailSheet deal={d} isMobile={false} onClose={noop} onEdit={noop}
        onStageChange={noop} onRestored={onRestored} />,
    );
    await act(async () => { button('Restore')!.click(); });

    expect(onRestored).not.toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalledWith('Failed to restore deal.');
    // Re-enabled, so the user can retry rather than being stuck on "Restoring…".
    expect(button('Restore')?.disabled).toBe(false);
  });
});

describe('DealDetailSheet — Mark Lost captures a reason (issue #128)', () => {
  it('opens the reason dialog instead of closing the deal immediately', async () => {
    const d = deal();
    routeApi(d);
    const onStageChange = vi.fn();
    await render(
      <DealDetailSheet deal={d} isMobile={false} onClose={noop} onEdit={noop}
        onStageChange={onStageChange} />,
    );

    expect(document.body.querySelector('[role="dialog"]')).toBeNull();
    await act(async () => { button('Mark Lost')!.click(); });

    expect(document.body.querySelector('[role="dialog"]')).toBeTruthy();
    // The whole point: nothing is written until a reason has been asked for.
    expect(onStageChange).not.toHaveBeenCalled();
  });

  it('hands the typed reason up as a THIRD argument, which is what selects the endpoint', async () => {
    const d = deal();
    routeApi(d);
    const onStageChange = vi.fn();
    await render(
      <DealDetailSheet deal={d} isMobile={false} onClose={noop} onEdit={noop}
        onStageChange={onStageChange} />,
    );
    await act(async () => { button('Mark Lost')!.click(); });

    // Scoped to the dialog on purpose: the sheet's own NotesThread composer is also a
    // textarea and comes first in document order.
    const field = document.body.querySelector<HTMLTextAreaElement>('[role="dialog"] textarea')!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!
        .set!.call(field, 'Lost on price');
      field.dispatchEvent(new Event('input', { bubbles: true }));
    });
    const confirm = [...document.body.querySelectorAll('button')]
      .find(b => b.textContent?.trim() === 'Mark Lost' && !container.contains(b))!;
    await act(async () => { confirm.click(); });

    expect(onStageChange).toHaveBeenCalledWith(d, 'lost', 'Lost on price');
  });

  it('writes nothing when the dialog is cancelled', async () => {
    const d = deal();
    routeApi(d);
    const onStageChange = vi.fn();
    await render(
      <DealDetailSheet deal={d} isMobile={false} onClose={noop} onEdit={noop}
        onStageChange={onStageChange} />,
    );
    await act(async () => { button('Mark Lost')!.click(); });
    const cancel = [...document.body.querySelectorAll('button')]
      .find(b => b.textContent?.trim() === 'Cancel' && !container.contains(b))!;
    await act(async () => { cancel.click(); });

    expect(onStageChange).not.toHaveBeenCalled();
    expect(document.body.querySelector('[role="dialog"]')).toBeNull();
  });

  it('leaves Mark Won a direct, dialog-free stage change', async () => {
    const d = deal();
    routeApi(d);
    const onStageChange = vi.fn();
    await render(
      <DealDetailSheet deal={d} isMobile={false} onClose={noop} onEdit={noop}
        onStageChange={onStageChange} />,
    );
    await act(async () => { button('Mark Won')!.click(); });

    // No third argument — a won deal has no reason to record.
    expect(onStageChange).toHaveBeenCalledWith(d, 'won');
  });
});

describe('DealDetailSheet — the owner is visible (issue #128)', () => {
  it('shows an Owner row reading "Unassigned" on an unowned deal', async () => {
    // Unconditional, unlike its neighbouring rows: hiding it is what made "unassigned"
    // indistinguishable from "not displayed".
    const d = deal({ owner_id: null });
    routeApi(d);
    await render(
      <DealDetailSheet deal={d} isMobile={false} onClose={noop} onEdit={noop} onStageChange={noop} />,
    );
    expect(container.textContent).toContain('Owner');
    expect(container.textContent).toContain('Unassigned');
  });
});
