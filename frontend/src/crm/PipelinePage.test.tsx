// @vitest-environment jsdom
//
// A mount-level smoke test for the rewritten board (#74). It is deliberately about the seams
// the type checker cannot see: that the page mounts at all against the real collection layer,
// that the render-phase deep-link reset settles instead of looping, that stage visibility
// removes a column AND its deals from every derived number, and that Board/List both render.
// The pure modules have their own suites; this one exists because a full rewrite of a page
// with no test at all is how a render-phase loop or a bad config reaches production green.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { ActiveRecordProvider } from './RecordContext';

import type { CrmDeal } from '../core/types';
import { installLocalStorage } from './testStorage';

const api = vi.hoisted(() => vi.fn());
vi.mock('../core/api/client', () => ({
  api,
  ApiError: class ApiError extends Error {
    status?: number;
    detail?: string;
  },
}));

// The board is desktop-only for selection; pin it so the bulk affordances render.
vi.mock('../shared/useIsMobile', () => ({ useIsMobile: () => false }));

// The detail BODY is stubbed: what these tests are about is whether the PAGE routes a
// selection into the collection layer's detail panel, not what the body renders. The real one
// pulls in the record context, the activity timeline, chatter, custom fields and the
// touch-count drill-down — a dependency tree with its own suites, and mocking all of it would
// test the mocks. `PipelinePage.detail.test.tsx` is the sibling that drives the real body;
// `vi.mock` is file-scoped, which is why they cannot share a file.
vi.mock('./components/DealDetailBody', () => ({
  DealDetailBody: ({ deal }: { deal: { id: number; title: string } }) => (
    <div data-testid="deal-sheet" data-deal-id={deal.id}>{deal.title}</div>
  ),
}));

const { PipelinePage } = await import('./PipelinePage');
const { saveShowClosedStages } = await import('./pipelineBoard');

function deal(over: Partial<CrmDeal> & { id: number }): CrmDeal {
  return {
    contact_id: null, company_id: null, title: `Deal ${over.id}`, stage: 'lead',
    value: 100, notes: '', expected_close_date: '', probability: 50, currency: 'USD',
    created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    ...over,
  };
}

const DEALS: CrmDeal[] = [
  deal({ id: 1, title: 'Alpha contract', stage: 'lead', value: 1000, lead_score: 80 }),
  deal({ id: 2, title: 'Beta renewal', stage: 'qualified', value: 2000, lead_score: 40 }),
  deal({ id: 3, title: 'Gamma expansion', stage: 'won', value: 5000 }),
];

let container: HTMLDivElement;
let root: Root;

function route(path: string) {
  // The deal sheet publishes itself as the active record (#14), so opening one — which the
  // list-row test does — needs the provider the real app mounts above the CRM routes.
  return (
    <MemoryRouter initialEntries={[path]}>
      <ActiveRecordProvider>
        <PipelinePage />
      </ActiveRecordProvider>
    </MemoryRouter>
  );
}

/** Mount and flush the queueMicrotask-scheduled initial load. */
async function mount(path = '/crm/pipeline'): Promise<void> {
  await act(async () => {
    root.render(route(path));
  });
  await act(async () => { await Promise.resolve(); });
}

const text = () => document.body.textContent ?? '';
const buttonByText = (label: string) =>
  [...document.querySelectorAll('button')].find(b => (b.textContent ?? '').trim() === label);

let restoreLocalStorage: () => void = () => {};

beforeEach(() => {
  sessionStorage.clear();
  // A fresh localStorage per test, and it is load-bearing rather than hygiene. Left to the
  // HOST the two runners disagree: with none, `saveShowClosedStages`' write is swallowed and
  // the preference case below proves only half of what it claims; with one, that same case
  // seeds every later test in this file and the hide-a-column test reads the wrong stored set.
  // Both were live at once — green locally, red on CI. See `testStorage.ts`.
  restoreLocalStorage = installLocalStorage();
  // jsdom implements no layout, so Element.scrollIntoView does not exist — the deep-link
  // and chip-bar paths both call it. Stubbing it keeps the test about the page's logic
  // rather than about jsdom's gaps.
  Element.prototype.scrollIntoView = vi.fn();
  api.mockReset();
  api.mockImplementation((url: string, init: { body: string }) => {
    if (url === '/api/users') return Promise.resolve({ users: [] });
    // #59 turned the board's one GET into a keyset sweep, so the URL carries the page-0
    // cursor params. Every fixture here is well under one page, so a load is still
    // exactly one request — matching on the prefix keeps this suite about the PAGE
    // rather than about pipelineAssembly's wire format, which has its own tests.
    if (url.startsWith('/api/crm/deals?sort=id')) return Promise.resolve({ deals: DEALS });
    // A bulk move must answer in the real envelope: `{}` reads as ok:false, which
    // classifyBulkMove correctly calls a REFUSAL — a silently wrong premise for any test
    // asserting on what happens after a successful move.
    if (url === '/api/crm/deals/bulk-move') {
      const ids = JSON.parse(init.body).deal_ids as number[];
      return Promise.resolve({ ok: true, updated: ids.length, updated_ids: ids, errors: [] });
    }
    return Promise.resolve({});
  });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  restoreLocalStorage();
});

describe('mounting', () => {
  it('renders the open deals and an open-pipeline total, with Won put away (#124)', async () => {
    await mount();
    expect(text()).toContain('Pipeline');
    expect(text()).toContain('Alpha contract');
    expect(text()).toContain('Beta renewal');
    // Gamma is `won`, and #124 makes the closed stages hidden by DEFAULT.
    expect(text()).not.toContain('Gamma expansion');
    expect(text()).toContain('2 stages hidden');
    // Unchanged by the hide, and that is why it is asserted here: `openPipelineTotals` filters
    // to OPEN_STAGES, so the won deal's 5000 was never in this figure. A default that moved the
    // headline number would be a reporting change rather than a display one.
    expect(text()).toContain('$3.0K open'); // formatNumber abbreviates thousands
    expect(text()).toContain('2 open deals');
  });

  it('shows the closed stages when the personal preference is on (#124)', async () => {
    // The Settings card's write, replayed: `saveShowClosedStages` is exactly what the
    // PipelineBoardCard checkbox calls.
    saveShowClosedStages(true);
    await mount();
    expect(text()).toContain('Gamma expansion');
    expect(text()).not.toContain('stages hidden');
  });

  it('exposes both views through the switcher', async () => {
    await mount();
    expect(buttonByText('Board')).toBeDefined();
    expect(buttonByText('List')).toBeDefined();
  });

  it('renders the list view with its columns when switched', async () => {
    await mount();
    const list = buttonByText('List');
    expect(list).toBeDefined();
    await act(async () => { list!.click(); });
    // Column headers from pipelineListColumns.
    expect(text()).toContain('Deal');
    expect(text()).toContain('Stage');
    expect(text()).toContain('Value');
    expect(text()).toContain('Alpha contract');
  });
});

describe('the ?stage= deep link resets filters during render without looping', () => {
  it('settles on a single mount and still shows the board', async () => {
    // A restored filter envelope that would otherwise hide the target column entirely.
    sessionStorage.setItem(
      'collection_crm_pipeline_v1',
      JSON.stringify({ query: 'zzz-no-match', facets: { stage: ['lead'] }, voided: null, toggles: {} }),
    );
    await mount('/crm/pipeline?stage=won');
    // If the render-phase reset looped, React would have thrown "Too many re-renders"
    // and this mount would never have completed.
    expect(text()).toContain('Pipeline');
    // The reset cleared the query and the stage facet, so the won column is back.
    expect(text()).toContain('Gamma expansion');
  });

  it('un-hides a stage the user had put away, since the link explicitly asks for it', async () => {
    sessionStorage.setItem('crm_pipeline_hidden_stages', JSON.stringify(['won']));
    await mount('/crm/pipeline?stage=won');
    expect(text()).toContain('Gamma expansion');
  });
});

describe('stage visibility removes the column AND its deals from every number', () => {
  it('a hidden stage drops out of the board and the header total', async () => {
    // Hiding `qualified` must take Beta's 2000 out of the open total, not just its column.
    sessionStorage.setItem('crm_pipeline_hidden_stages', JSON.stringify(['qualified']));
    await mount();
    expect(text()).not.toContain('Beta renewal');
    expect(text()).toContain('$1.0K open');
    expect(text()).toContain('1 open deal');
    expect(text()).toContain('1 stage hidden');
  });

  it('a hidden stage is also absent from the list view', async () => {
    sessionStorage.setItem('crm_pipeline_hidden_stages', JSON.stringify(['qualified']));
    await mount();
    const list = buttonByText('List');
    await act(async () => { list!.click(); });
    expect(text()).toContain('Alpha contract');
    expect(text()).not.toContain('Beta renewal');
  });
});

describe('failure handling', () => {
  it('shows the load error rather than an empty board when the fetch fails', async () => {
    api.mockImplementation((url: string) => {
      if (url === '/api/users') return Promise.resolve({ users: [] });
      return Promise.reject(new Error('boom'));
    });
    await mount();
    expect(text()).toContain("Couldn't load pipeline");
  });
});

describe('the bulk bar count and the bulk-move payload cannot disagree', () => {
  const checkbox = (label: string) =>
    document.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`);
  const bulkMoveCall = (): [string, { body: string }] | undefined =>
    api.mock.calls.find(c => c[0] === '/api/crm/deals/bulk-move') as
      | [string, { body: string }]
      | undefined;

  it('sends exactly the deals the bar said were selected', async () => {
    await mount();
    // Select two deals in different stages.
    await act(async () => { checkbox('Select Alpha contract')!.click(); });
    await act(async () => { checkbox('Select Beta renewal')!.click(); });
    expect(text()).toContain('2 deals selected');

    const select = document.querySelector<HTMLSelectElement>(
      'select[aria-label="Move selected deals to stage"]',
    )!;
    await act(async () => {
      select.value = 'proposal';
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await act(async () => { buttonByText('Apply')!.click(); });
    await act(async () => { await Promise.resolve(); });

    const call = bulkMoveCall();
    expect(call).toBeDefined();
    const body = JSON.parse(call![1].body);
    expect(new Set(body.deal_ids)).toEqual(new Set([1, 2]));
    expect(body.stage).toBe('proposal');
  });

  it('drops a selected deal whose stage was hidden after selecting it — bar and payload both', async () => {
    await mount();
    await act(async () => { checkbox('Select Alpha contract')!.click(); });
    await act(async () => { checkbox('Select Beta renewal')!.click(); });
    expect(text()).toContain('2 deals selected');

    // Put Beta's column away. Beta leaves `items` entirely, so it must leave both the
    // count the operator is shown AND the payload the server is sent.
    await act(async () => {
      document.querySelector<HTMLButtonElement>('button[aria-label="Hide Qualified column"]')!.click();
    });
    expect(text()).toContain('1 deal selected');

    const select = document.querySelector<HTMLSelectElement>(
      'select[aria-label="Move selected deals to stage"]',
    )!;
    await act(async () => {
      select.value = 'proposal';
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await act(async () => { buttonByText('Apply')!.click(); });
    await act(async () => { await Promise.resolve(); });

    expect(JSON.parse(bulkMoveCall()![1].body).deal_ids).toEqual([1]);
  });
});

describe('stage visibility is reachable and reversible through the UI', () => {
  it('the column Hide button removes the column and persists the preference', async () => {
    await mount();
    expect(text()).toContain('Beta renewal');
    await act(async () => {
      document.querySelector<HTMLButtonElement>('button[aria-label="Hide Qualified column"]')!.click();
    });
    expect(text()).not.toContain('Beta renewal');
    // Sorted, because the write ADDS to whatever this tab already held — and since #124 a
    // fresh tab starts holding the two closed stages.
    expect(JSON.parse(sessionStorage.getItem('crm_pipeline_hidden_stages')!).sort())
      .toEqual(['lost', 'qualified', 'won']);
  });

  it('board → Settings → board: the preference lands, a manual hide survives it', async () => {
    // The end-to-end shape of #124, and the trap it exists to avoid. The board persists its
    // hidden set on the first render, so by the time anyone reaches Settings their tab already
    // HAS a stored set — and a stored set outranks the preference. Without the reconciliation
    // inside `saveShowClosedStages` the toggle would look inert to the one person most likely
    // to check it. The manual hide is the other half of that contract: reconciliation touches
    // the closed stages and nothing else.
    await mount();
    await act(async () => {
      document.querySelector<HTMLButtonElement>('button[aria-label="Hide Qualified column"]')!.click();
    });
    expect(text()).not.toContain('Beta renewal');

    act(() => root.unmount());     // navigating away to /crm/settings
    saveShowClosedStages(true);    // what PipelineBoardCard's checkbox calls
    root = createRoot(container);  // and back again
    await mount();

    expect(text()).toContain('Gamma expansion');  // the closed stage came back...
    expect(text()).not.toContain('Beta renewal'); // ...and the manual hide did not
    expect(text()).toContain('1 stage hidden');
  });

  it('"Show all" restores every hidden stage — including when ALL of them are hidden', async () => {
    // The dead end this guards: hiding every stage empties `items`, and the collection layer
    // answers an empty `items` with a bare empty state rendered BEFORE its toolbar — so the
    // visibility checkboxes that would undo it are gone. The header's Show all must survive.
    sessionStorage.setItem(
      'crm_pipeline_hidden_stages',
      JSON.stringify(['lead', 'qualified', 'proposal', 'negotiation', 'won', 'lost']),
    );
    await mount();
    expect(text()).toContain('6 stages hidden');
    const showAll = buttonByText('Show all');
    expect(showAll).toBeDefined();
    await act(async () => { showAll!.click(); });
    expect(text()).toContain('Alpha contract');
    expect(text()).toContain('Beta renewal');
    expect(sessionStorage.getItem('crm_pipeline_hidden_stages')).toBe('[]');
  });
});

describe('a list row opens the deal, like a board card does', () => {
  it('clicking a row selects that deal', async () => {
    await mount();
    await act(async () => { buttonByText('List')!.click(); });

    // The row advertises a click (ListView gives every row a pointer cursor); it must land.
    const row = [...document.querySelectorAll('tbody tr')]
      .find(tr => (tr.textContent ?? '').includes('Beta renewal'));
    expect(row).toBeDefined();
    await act(async () => { (row as HTMLElement).click(); });
    await act(async () => { await Promise.resolve(); });

    expect(document.querySelector('[data-testid="deal-sheet"]')?.getAttribute('data-deal-id'))
      .toBe('2');
  });
});

describe('moving a deal into a hidden stage reveals that column', () => {
  it('a bulk move to a hidden stage un-hides it rather than vanishing the deals', async () => {
    sessionStorage.setItem('crm_pipeline_hidden_stages', JSON.stringify(['proposal']));
    await mount();

    const check = document.querySelector<HTMLInputElement>('input[aria-label="Select Alpha contract"]')!;
    await act(async () => { check.click(); });
    const select = document.querySelector<HTMLSelectElement>(
      'select[aria-label="Move selected deals to stage"]',
    )!;
    await act(async () => {
      select.value = 'proposal';
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await act(async () => { buttonByText('Apply')!.click(); });
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await Promise.resolve(); });

    // Proposal is visible again, so the moved deal has somewhere to land in view.
    expect(JSON.parse(sessionStorage.getItem('crm_pipeline_hidden_stages')!)).toEqual([]);
    expect(text()).not.toContain('1 stage hidden');
  });
});

describe('an empty board shows ONE explanation, not two', () => {
  it('does not stack the filter message on the layer\'s own empty state', async () => {
    api.mockImplementation((url: string) => {
      if (url === '/api/users') return Promise.resolve({ users: [] });
      if (url.startsWith('/api/crm/deals?sort=id')) return Promise.resolve({ deals: [] });
      return Promise.resolve({});
    });
    // A persisted query from a previous session, on an install that has no deals yet.
    sessionStorage.setItem(
      'collection_crm_pipeline_v1',
      JSON.stringify({ query: 'anything', facets: {}, voided: null, toggles: {} }),
    );
    await mount();
    expect(text()).toContain('No deals to show.');
    expect(text()).not.toContain('No deals match your filters.');
  });
});

describe('a selected card is styled without mixing border shorthand and longhand', () => {
  it('sets the border shorthand on selection, never the borderColor longhand', async () => {
    await mount();
    const card = [...document.querySelectorAll('[role="button"]')]
      .find(el => (el.textContent ?? '').includes('Alpha contract')) as HTMLElement;
    expect(card).toBeDefined();

    await act(async () => {
      document.querySelector<HTMLInputElement>('input[aria-label="Select Alpha contract"]')!.click();
    });

    // React warns on every selection toggle when a longhand lands in an object that already
    // carries the shorthand, and which wins becomes order-dependent. The selected card must
    // therefore restate `border`/`border-left`, and leave `border-color` alone.
    // Asserting on the raw attribute, and only on the part jsdom can actually represent:
    // its CSS parser drops a `border` SHORTHAND whose value contains `var()`, so the
    // shorthand is invisible here even though a browser applies it. What survives — and what
    // the React warning was actually about — is whether a `border-color` LONGHAND appears
    // alongside it. It must not.
    const style = card.getAttribute('style') ?? '';
    expect(style).not.toContain('border-color');
    // ...and the selected styling really did apply (accent left edge + ring).
    expect(style).toContain('border-left: 3px solid var(--color-ck-accent)');
    expect(style).toContain('box-shadow');
  });
});
