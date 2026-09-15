// @vitest-environment jsdom
//
// Issue #176: what a screen reader announces when it lands on a pipeline board card.
//
// The card is a `role="button"` div, and an unlabelled button takes its accessible name from
// its own CONTENTS. The first thing inside this one is the bulk-select checkbox, whose own
// label is `Select <title>` — so the control that OPENS a deal announced as
// "Select Acme renewal, Acme renewal, $600, …": the wrong verb, the title twice, and the
// money dragged into the name. `selectable` is true for every live desktop card, so this was
// the default board rather than an edge.
//
// Every assertion below goes through `accessibleName`, never raw `textContent` — the #162
// convention, and here it is what makes the test falsifiable at all. `textContent` cannot
// see the fix (an `aria-label` adds no text) and cannot see the bug either (the checkbox's
// label is an attribute, not text), so a textContent assertion would be green in both
// directions while the announced name stayed wrong.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

import type { CrmDeal } from '../core/types';
import { ActiveRecordProvider } from './RecordContext';

const api = vi.hoisted(() => vi.fn());
vi.mock('../core/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../core/api/client')>()),
  api,
}));

// Desktop. `selectable` is `!isMobile && !archived`, so a mobile harness would render no
// checkbox at all and the bug under test would be unreachable — every assertion here would
// pass vacuously against the unfixed component.
vi.mock('../shared/useIsMobile', () => ({ useIsMobile: () => false }));

const { PipelinePage } = await import('./PipelinePage');

function deal(over: Partial<CrmDeal> & { id: number; title: string }): CrmDeal {
  return {
    stage: 'lead', value: 0, probability: 20, expected_close_date: '', notes: '',
    contact_id: null, company_id: null, currency: 'USD', archived_at: null,
    created_at: '2026-08-01T00:00:00+00:00', updated_at: '2026-08-01T00:00:00+00:00',
    ...over,
  };
}

// Deliberately money-bearing and metadata-bearing: the name must exclude both, and a $0
// deal with no probability would let a leaked subtree look clean.
const LIVE = deal({ id: 1, title: 'Acme renewal', value: 600, probability: 40 });
const ARCHIVED = deal({
  id: 2, title: 'Zebra rebuild', value: 99_999,
  archived_at: '2026-08-20T00:00:00+00:00',
});

// #59: the board sweeps keyset pages. Both fixtures sit well inside one page.
const LIVE_PATH = '/api/crm/deals?sort=id&limit=501';
const ARCHIVED_PATH = '/api/crm/deals?sort=id&limit=501&include_archived=true';

let container: HTMLDivElement;
let root: Root;

/**
 * What an assistive technology announces for this element: `aria-label` when the author set
 * one, otherwise everything the element renders minus every `aria-hidden` subtree.
 *
 * Both branches are load-bearing. Asserting the attribute alone would pin the fix rather
 * than the behaviour it buys, and would say nothing about what the card announced before it;
 * computing the name the way a reader does is what makes one expected string describe both
 * the bug (the checkbox's label, then the title, then the money) and the fix.
 */
function accessibleName(el: Element): string {
  const label = el.getAttribute('aria-label');
  if (label !== null) return label.replace(/\s+/g, ' ').trim();
  const clone = el.cloneNode(true) as Element;
  for (const hidden of clone.querySelectorAll('[aria-hidden="true"], [aria-hidden=""]')) {
    hidden.remove();
  }
  // A checkbox contributes its own accessible name to its container's, which is the whole
  // defect: `Select <title>` is an attribute on the input, so it reaches the name without
  // reaching `textContent`.
  const parts = [...clone.querySelectorAll('input[aria-label]')]
    .map(input => input.getAttribute('aria-label') ?? '');
  return [...parts, clone.textContent ?? ''].join(' ').replace(/\s+/g, ' ').trim();
}

/** The rendered card ROOT for one deal — `DealBoardCard`'s `role="button"` element. */
function card(title: string): HTMLElement {
  const el = [...container.querySelectorAll<HTMLElement>('[role="button"]')]
    .find(d => (d.textContent ?? '').includes(title));
  if (!el) throw new Error(`no card for ${title}`);
  return el;
}

function button(label: string): HTMLButtonElement | undefined {
  return [...container.querySelectorAll('button')]
    .find(b => b.textContent?.trim() === label || b.getAttribute('aria-label') === label) as
      HTMLButtonElement | undefined;
}

async function flush() {
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
}

async function click(el: Element | undefined, what: string) {
  if (!el) throw new Error(`nothing to click: ${what}`);
  await act(async () => { (el as HTMLElement).click(); });
  await flush();
}

/** Turn the Archived facet on, the way #83's own suite does: the toolbar's Filters
 *  disclosure, then the option chip. */
async function includeArchived() {
  const trigger = [...container.querySelectorAll('button')]
    .find(b => (b.textContent ?? '').trim().startsWith('Filters')) as HTMLButtonElement | undefined;
  if (!trigger) throw new Error('no Filters disclosure on the toolbar');
  if (trigger.getAttribute('aria-expanded') !== 'true') await click(trigger, 'Filters');
  await click(button('Include archived'), 'Include archived');
}

beforeEach(() => {
  window.matchMedia = ((query: string) => ({
    matches: false, media: query, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {},
    addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
  Element.prototype.scrollIntoView = vi.fn();
  sessionStorage.clear();
  api.mockReset();
  api.mockImplementation(async (path: string) => {
    if (path === LIVE_PATH) return { deals: [LIVE] };
    if (path === ARCHIVED_PATH) return { deals: [LIVE, ARCHIVED] };
    if (path === '/api/users') return { users: [] };
    return null;
  });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

async function render() {
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={['/crm/pipeline']}>
        <ActiveRecordProvider><PipelinePage /></ActiveRecordProvider>
      </MemoryRouter>,
    );
  });
  await flush();
}

describe('a pipeline board card announces what it does (#176)', () => {
  it('names the card for the action it performs, not for the checkbox inside it', async () => {
    await render();
    const el = card('Acme renewal');

    // The bug, stated as the thing that must not happen: the checkbox is really there, so
    // the name is only clean because the card claims one of its own.
    expect(el.querySelector('input[type="checkbox"]')).toBeTruthy();
    expect(accessibleName(el)).toBe('Open Acme renewal');
  });

  it('leaves the selection checkbox its own name', async () => {
    await render();
    // The card's label must not have been bought by relabelling the control beside it —
    // "Select <title>" is the right name for the checkbox and the wrong one for the card.
    const box = card('Acme renewal').querySelector('input[type="checkbox"]');
    expect(box && accessibleName(box)).toBe('Select Acme renewal');
  });

  it('carries the archived state into the name, since the chip leaves the name with it', async () => {
    await render();
    await includeArchived();

    const el = card('Zebra rebuild');
    // The ARCHIVED chip is ordinary text in the subtree, so an explicit label drops it along
    // with the title and the money. It is the one thing in there worth announcing, and this
    // card has no checkbox to be confused with in the first place — so the label says it.
    expect(el.textContent).toContain('ARCHIVED');
    expect(el.querySelector('input[type="checkbox"]')).toBeNull();
    expect(accessibleName(el)).toBe('Open Zebra rebuild (archived)');
  });
});
