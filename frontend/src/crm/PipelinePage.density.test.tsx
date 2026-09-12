// @vitest-environment jsdom
//
// Issue #182: the desktop board's column width and its cards' field set move together, in three
// tiers keyed off how many stage columns are on screen. Hide stages to focus, and the freed
// width becomes wider columns carrying more at a glance.
//
// The tier arithmetic itself is pure and pinned in `pipelineBoard.test.ts`. What only the page
// can answer is the part this file covers: that the tier is derived from the RENDERED columns,
// that each tier's promotions actually reach the card, that the widest tier is a superset of the
// narrowest (nothing is ever taken away as the board narrows), and that #129's Won-card trade
// still holds at every tier.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { ActiveRecordProvider } from './RecordContext';

import type { CrmDeal } from '../core/types';

const api = vi.hoisted(() => vi.fn());
vi.mock('../core/api/client', () => ({
  api,
  ApiError: class ApiError extends Error {
    status?: number;
    detail?: string;
  },
}));

// Desktop. The whole feature is desktop-only — mobile keeps its fixed `85vw` snap column — so a
// mobile harness would assert the branch this issue deliberately does not touch.
vi.mock('../shared/useIsMobile', () => ({ useIsMobile: () => false }));

vi.mock('./components/DealDetailSheet', () => ({
  DealDetailSheet: ({ deal }: { deal: { id: number; title: string } }) => (
    <div data-testid="deal-sheet" data-deal-id={deal.id}>{deal.title}</div>
  ),
}));

const { PipelinePage } = await import('./PipelinePage');

function deal(over: Partial<CrmDeal> & { id: number }): CrmDeal {
  return {
    contact_id: null, company_id: null, title: `Deal ${over.id}`, stage: 'lead',
    value: 100, notes: '', expected_close_date: '', probability: 50, currency: 'USD',
    created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    ...over,
  };
}

// Frozen so "3w ago" is arithmetic rather than a race with the wall clock.
const NOW = new Date('2026-09-09T12:00:00Z');

const OWNER = { id: 5, email: 'dana@example.com', name: 'Dana Reed', role: 'member' as const, is_active: true };

const DEALS: CrmDeal[] = [
  // The subject. Open, owned, dated, and carrying both nudges, so every tier has something to
  // add and the negative assertions below can only be explained by the tier.
  deal({
    id: 1, title: 'Alpha contract', stage: 'negotiation', owner_id: OWNER.id,
    expected_close_date: '2026-10-01', lead_score: 80, ai_touch_count: 7,
    last_activity_at: '2026-08-19T12:00:00Z',
  }),
  // Same tier, no close date — the promotion has to render a state, not an empty slot.
  deal({ id: 2, title: 'Beta renewal', stage: 'negotiation', owner_id: null, lead_score: 40 }),
  // Won, so #129's trade can be re-checked at each tier. WON specifically, not merely closed:
  // that trade is keyed on the won COLUMN, and a lost card keeps its nudges like any other.
  deal({
    id: 3, title: 'Gamma expansion', stage: 'won', lead_score: 80, ai_touch_count: 7,
    last_activity_at: '2026-08-19T12:00:00Z',
  }),
];

let container: HTMLDivElement;
let root: Root;

/**
 * The rendered card ROOT for one deal. `DealBoardCard`'s root is the `role="button"` element —
 * matching on that rather than "the innermost div containing the title" is what makes the
 * negative assertions mean anything, since a looser selector would report every card as lacking
 * every field.
 */
function card(title: string): HTMLElement {
  const el = [...document.querySelectorAll<HTMLElement>('[role="button"]')]
    .find(d => (d.textContent ?? '').includes(title));
  if (!el) throw new Error(`no card for ${title}`);
  return el;
}

/** The column wrapper `renderColumn` produces, which is where #182's flex band is written. */
function column(stage: string): HTMLElement {
  const el = document.querySelector<HTMLElement>(`[data-stage="${stage}"]`);
  if (!el) throw new Error(`no column for ${stage}`);
  return el;
}

function columnCount(): number {
  return document.querySelectorAll('[data-stage]').length;
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(NOW);
  sessionStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
  api.mockReset();
  api.mockImplementation((url: string) => {
    if (url === '/api/users') return Promise.resolve({ users: [OWNER] });
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
  vi.useRealTimers();
});

/**
 * Mount the board with an explicit hidden-stage set, which is what decides the tier.
 *
 * Written to sessionStorage rather than driven through the UI on purpose: the point of the
 * feature is that the tier follows the RENDERED column count however it got there — #124's
 * durable default, a per-tab hide, or a stage facet — so the test states the count it wants
 * and asserts what the board does with it.
 */
async function mount(hidden: string[]): Promise<void> {
  sessionStorage.setItem('crm_pipeline_hidden_stages', JSON.stringify(hidden));
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

// STAGE_ORDER is lead / qualified / proposal / negotiation / won / lost.
const ALL_SIX: string[] = [];
const CLOSED_HIDDEN = ['won', 'lost'];                              // #124's default: 4 visible
const FOCUSED = ['lead', 'qualified', 'proposal', 'lost'];          // 2 visible: negotiation, won

describe('board density follows the number of visible stage columns', () => {
  it('leaves the full six-column board exactly as it was', async () => {
    await mount(ALL_SIX);
    expect(columnCount()).toBe(6);

    const open = card('Alpha contract');
    // The two nudges and the close date are the pre-#182 field set, and they stay.
    expect(open.textContent).toContain('7 touches');
    expect(open.textContent).toContain('2026-10-01');
    // Nothing the wider tiers add has leaked down into the default board.
    expect(open.textContent).not.toContain('Last contact');
    expect(open.textContent).not.toContain('Dana Reed');
    // A dateless card renders no close-date slot at all here, as it always did.
    expect(card('Beta renewal').textContent).not.toContain('No close date');
  });

  it('promotes the close date and the last-contact line at 3-4 visible stages', async () => {
    await mount(CLOSED_HIDDEN);
    expect(columnCount()).toBe(4);

    const open = card('Alpha contract');
    expect(open.textContent).toContain('Last contact 3w ago');
    // ADDED alongside the nudges, never swapped for them — that trade is the Won card's alone.
    expect(open.textContent).toContain('7 touches');
    // Owner belongs to the widest tier only.
    expect(open.textContent).not.toContain('Dana Reed');
    // An absent close date is now a state worth reading rather than an empty slot.
    expect(card('Beta renewal').textContent).toContain('No close date');
  });

  it('adds the owner at 1-2 visible stages, keeping everything the tier below shows', async () => {
    await mount(FOCUSED);
    expect(columnCount()).toBe(2);

    const open = card('Alpha contract');
    expect(open.textContent).toContain('Dana Reed');
    expect(open.textContent).toContain('Last contact 3w ago');
    expect(open.textContent).toContain('2026-10-01');
    expect(open.textContent).toContain('7 touches');
    // An unowned deal names the state rather than rendering a blank — #128's rule.
    expect(card('Beta renewal').textContent).toContain('Unassigned');
  });

  it('never renders a last-contact line twice on one card', async () => {
    await mount(FOCUSED);
    // The Won-stage branch and the wider-tier branch both draw this line, from one shared
    // expression. If the two were ever both taken, a won card would say it twice.
    const matches = card('Gamma expansion').textContent?.match(/Last contact|No contact logged/g);
    expect(matches).toHaveLength(1);
  });

  it("keeps #129's Won-card trade at the widest tier", async () => {
    await mount(FOCUSED);
    const closed = card('Gamma expansion');
    // Still no nudges: the close retired those questions, and having room does not revive them.
    expect(closed.textContent).not.toContain('7 touches');
    expect(closed.querySelector('[title^="Computed lead score"]')).toBeNull();
    // The tier's own additions still apply to it.
    expect(closed.textContent).toContain('Last contact 3w ago');
    expect(closed.textContent).toContain('No close date');
  });
});

describe('board columns flex to fill the row', () => {
  it('drops the fixed width for a flex band on desktop', async () => {
    await mount(ALL_SIX);
    const col = column('negotiation');
    // The fixed 288px is gone; the floor took its place, so nothing narrows.
    expect(col.style.width).toBe('');
    expect(col.style.minWidth).toBe('288px');
    expect(col.style.maxWidth).toBe('360px');
  });

  it('raises the ceiling at 3-4 visible stages, and never the floor', async () => {
    await mount(CLOSED_HIDDEN);
    expect(column('negotiation').style.maxWidth).toBe('440px');
    expect(column('negotiation').style.minWidth).toBe('288px');
  });

  it('raises it again at 1-2, and still never the floor', async () => {
    await mount(FOCUSED);
    expect(column('negotiation').style.maxWidth).toBe('560px');
    expect(column('negotiation').style.minWidth).toBe('288px');
  });

  it('keeps a mobile snap column out of the desktop band entirely', async () => {
    // Guard against the branches being merged into per-property ternaries: the desktop object
    // must never carry `width`/`flexShrink`, and the mobile one must never carry `flex`. This
    // harness is desktop, so the desktop half is what is observable here — and `flexShrink`
    // leaking back is exactly what would silently re-pin every column at its basis.
    await mount(ALL_SIX);
    expect(column('negotiation').style.flexShrink).toBe('');
    expect(column('negotiation').style.scrollSnapAlign).toBe('');
  });
});
