// @vitest-environment jsdom
//
// Issue #129, item 3: a Won-stage card trades its two open-deal nudges — the lead score and the
// AI touch count — for a "last contact" line, because after the close the board is read for a
// different question: which accounts have gone quiet.
//
// The label itself is pure and pinned in `pipelineBoard.test.ts`. What this file adds is the part
// only the page can answer: that the swap is keyed off the COLUMN, that it happens on won cards
// and ONLY on won cards, and that a won deal with nothing logged still says so.
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

const DEALS: CrmDeal[] = [
  // An OPEN deal carrying both nudges, so the assertions below can tell "won cards lost them"
  // apart from "these pills never render in this harness at all".
  deal({
    id: 1, title: 'Alpha contract', stage: 'lead',
    lead_score: 80, ai_touch_count: 7, last_activity_at: '2026-08-19T12:00:00Z',
  }),
  // Won, with the same score and count — so only the STAGE can explain the difference.
  deal({
    id: 2, title: 'Gamma expansion', stage: 'won',
    lead_score: 80, ai_touch_count: 7, last_activity_at: '2026-08-19T12:00:00Z',
  }),
  // Won, and nobody has logged anything against it.
  deal({ id: 3, title: 'Delta upsell', stage: 'won', lead_score: 80, ai_touch_count: 7 }),
];

let container: HTMLDivElement;
let root: Root;

const text = () => document.body.textContent ?? '';

/**
 * The rendered card ROOT for one deal, found by its title.
 *
 * `DealBoardCard`'s root is the `role="button"` element — the card is what you click to open the
 * deal. Matching on that rather than on "the innermost div containing the title" is what makes
 * the negative assertions below mean anything: the title's own `<span>` contains no pills either,
 * so a looser selector would report every card as having lost them.
 */
function card(title: string): HTMLElement {
  const el = [...document.querySelectorAll<HTMLElement>('[role="button"]')]
    .find(d => (d.textContent ?? '').includes(title));
  if (!el) throw new Error(`no card for ${title}`);
  return el;
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(NOW);
  sessionStorage.clear();
  // Pin the stage columns OPEN rather than relying on the default. Won/Lost are hidden by
  // default on a sibling branch (#124), and a test whose subject is the WON column must fail
  // loudly if that column stops rendering — not quietly assert nothing.
  sessionStorage.setItem('crm_pipeline_hidden_stages', JSON.stringify([]));
  Element.prototype.scrollIntoView = vi.fn();
  api.mockReset();
  api.mockImplementation((url: string) => {
    if (url === '/api/users') return Promise.resolve({ users: [] });
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

async function mount(): Promise<void> {
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

describe('the Won card trades its staleness nudges for a last-contact line', () => {
  // `ScorePill` in compact mode renders the bare number, which appears in plenty of other places
  // on a board — so it is identified by its own tooltip. The string is quoted from
  // `components/badges.tsx` and matched case-sensitively on purpose: an approximation that
  // matches nothing would make every `toBeNull()` below pass for the wrong reason.
  const SCORE_PILL = '[title^="Computed lead score"]';

  it('shows the last contact on a won card, and neither pill', async () => {
    await mount();
    const won = card('Gamma expansion');
    expect(won.textContent).toContain('Last contact 3w ago');
    expect(won.textContent).not.toContain('7 touches');
    expect(won.querySelector(SCORE_PILL)).toBeNull();
  });

  it('leaves an open card exactly as it was', async () => {
    await mount();
    const open = card('Alpha contract');
    expect(open.textContent).toContain('7 touches');
    expect(open.textContent).not.toContain('Last contact');
    // The positive half of the pair above. Without it, a `SCORE_PILL` selector that matched
    // nothing anywhere would still make the won-card assertion pass.
    expect(open.querySelector(SCORE_PILL)).not.toBeNull();
  });

  it('says so outright when a won deal has nothing logged', async () => {
    await mount();
    // Never an empty slot: a won account nobody has followed up on is the strongest signal this
    // line exists to surface, so hiding it would invert the feature.
    expect(card('Delta upsell').textContent).toContain('No contact logged');
  });

  it('carries the absolute timestamp in a tooltip, since the line itself is only an age', async () => {
    await mount();
    const titled = card('Gamma expansion').querySelector('[title*="Most recent logged note"]');
    expect(titled).not.toBeNull();
  });

  it('renders the line on every won card the board shows', async () => {
    await mount();
    // Both won deals, neither open one.
    expect(text().match(/Last contact|No contact logged/g)).toHaveLength(2);
  });
});
