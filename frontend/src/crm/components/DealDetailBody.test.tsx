// @vitest-environment jsdom
//
// What this pins is the deal detail's CONTRACT, not its markup.
//
// Four of these are the ones that would fail silently in production if they broke: the record
// context that feeds the assistant drawer (deals have no route, so this body is the only signal
// there is), the single PUT that carries stage and columns together, the close guard composing
// EVERY draft the body holds, and the copy-link building its URL from the id rather than the
// address bar — which is unverifiable by eye, because the parameter is stripped the instant it
// is read.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { CrmDeal } from '../../core/types';
import type { DealPatch } from './DealDetailBody';
import type { DetailCloseGuard, DetailCloseReason, DetailRenderContext } from '../../shared/collection';

const api = vi.hoisted(() => vi.fn());
vi.mock('../../core/api/client', () => ({
  api,
  ApiError: class ApiError extends Error {},
}));

const confirmDialog = vi.hoisted(() => vi.fn<() => Promise<boolean>>());
vi.mock('../../shared/confirm', () => ({ confirmDialog }));

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }));
vi.mock('../../shared/toast', () => ({ toast }));

const { DealDetailBody } = await import('./DealDetailBody');
const { ActiveRecordProvider, useActiveRecord } = await import('../RecordContext');

// ── Fixtures ─────────────────────────────────────────────────────────────────────────────────

function makeDeal(over: Partial<CrmDeal> = {}): CrmDeal {
  return {
    id: 7, title: 'Wholesale order', stage: 'qualified', value: 4200, notes: 'Follow up Tuesday',
    contact_id: 3, contact_name: 'Dana Reyes', company_id: 5, company_name: 'Northwind',
    expected_close_date: '2026-09-01', probability: 40, currency: 'USD',
    created_at: '2026-08-01T00:00:00Z', updated_at: '2026-08-20T00:00:00Z',
    owner_id: null, lead_score: 61, ai_touch_count: null,
    ...over,
  };
}

/** The detail GET's payload — `activity` present is what marks a record as already detailed. */
function detailResponse(over: Partial<CrmDeal> = {}): CrmDeal {
  return {
    ...makeDeal(),
    activity: [{ id: 1, activity: 'call', note: 'Left a voicemail', created_at: '2026-08-19T00:00:00Z' }],
    ...over,
  } as CrmDeal;
}

/** Route-based mock so the composed children (chatter, custom fields, provenance, users) run for
 *  real rather than being stubbed out — the point is that this body composes them correctly. */
function routeApi(detail: CrmDeal = detailResponse()) {
  return (path: string, options?: { method?: string }) => {
    const method = options?.method ?? 'GET';
    if (method === 'GET' && /^\/api\/crm\/deals\/\d+$/.test(path)) return Promise.resolve(detail);
    if (path.startsWith('/api/crm/provenance/')) return Promise.resolve({ provenance: [] });
    if (path.startsWith('/api/crm/chatter/')) return Promise.resolve({ notes: [] });
    if (/\/fields$/.test(path)) return Promise.resolve([]);   // CrmFieldValue[], not an envelope
    if (path.startsWith('/api/users')) return Promise.resolve({ users: [] });
    if (path.startsWith('/api/crm/contacts')) {
      return Promise.resolve({ contacts: [{ id: 99, name: 'Someone Else', company: '', company_name: '' }] });
    }
    if (path.startsWith('/api/crm/companies')) {
      return Promise.resolve({ companies: [{ id: 88, name: 'Other Co', status: 'active' }] });
    }
    if (path === '/api/crm/activity') return Promise.resolve({ id: 500 });
    return Promise.resolve({});
  };
}

// ── Harness ──────────────────────────────────────────────────────────────────────────────────

let container: HTMLDivElement;
let root: Root;
let guard: DetailCloseGuard | null = null;
const published: Array<string | null> = [];

const ctx: DetailRenderContext = {
  registerCloseGuard: g => {
    guard = g;
    return () => { if (guard === g) guard = null; };
  },
};

function RecordProbe() {
  const { record } = useActiveRecord();
  published.push(record ? `${record.recordType}:${record.recordId}` : null);
  return null;
}

beforeEach(() => {
  // jsdom implements no media queries; `useIsMobile` (reached through the composed children)
  // reads one on mount. Desktop is the branch under test.
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: () => ({ matches: false, addEventListener: () => {}, removeEventListener: () => {} }),
  });
  api.mockReset();
  api.mockImplementation(routeApi());
  confirmDialog.mockReset();
  confirmDialog.mockResolvedValue(true);
  toast.success.mockReset();
  toast.error.mockReset();
  guard = null;
  published.length = 0;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

type BodyProps = Parameters<typeof DealDetailBody>[0];

function render(props: Partial<BodyProps> & { deal: CrmDeal }) {
  const full: BodyProps = {
    onBoard: true,
    stageWritable: true,
    ctx,
    onMarkWon: vi.fn(),
    onMarkLost: vi.fn(),
    // Resolves with the row a server would return: `_classify_deal_update` derives `probability`
    // from the stage, so a close writes 100/0 whatever the form sent. A mock resolving
    // `undefined` would make the body's fold a silent no-op and let every assertion about it
    // pass for the wrong reason.
    onSaveDeal: vi.fn((d: CrmDeal, patch: DealPatch) => Promise.resolve({
      ...d,
      ...patch,
      ...(patch.stage === 'won' ? { probability: 100 } : {}),
      ...(patch.stage === 'lost' ? { probability: 0 } : {}),
    })),
    ...props,
  };
  act(() => {
    root.render(
      <MemoryRouter initialEntries={['/crm/pipeline?foo=bar']}>
        <ActiveRecordProvider>
          <RecordProbe />
          <DealDetailBody {...full} />
        </ActiveRecordProvider>
      </MemoryRouter>,
    );
  });
  return full;
}

/** Drain the microtask queue the fetches and guards resolve on. */
async function settle(rounds = 4) {
  for (let i = 0; i < rounds; i++) {
    await act(async () => { await Promise.resolve(); });
  }
}

const buttonByText = (text: string) =>
  [...container.querySelectorAll('button')].find(b => b.textContent?.trim() === text) ?? null;
const click = (el: HTMLElement | null) => act(() => { (el as HTMLElement).click(); });
const input = (id: string) => container.querySelector(`#${id}`) as HTMLInputElement | HTMLSelectElement;
const setField = (id: string, value: string) => {
  const el = input(id);
  act(() => {
    const setter = Object.getOwnPropertyDescriptor(
      el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype,
      'value',
    )!.set!;
    setter.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  });
};
const dealGets = () =>
  api.mock.calls.filter(([p, o]) => /^\/api\/crm\/deals\/\d+$/.test(p) && (o?.method ?? 'GET') === 'GET');

// ── RecordCombobox helpers ───────────────────────────────────────────────────────────────────
//
// The inline editor's two link fields are `RecordCombobox`es (#123), not `<select>`s, so they are
// driven the way a user drives them: focus opens the list, the 250ms debounce and its fetch have
// to settle, and a row is chosen by clicking its option. Real timers — this file uses no fake
// ones, and mixing the two around `act()` is more fragile than waiting.
const picker = (which: 'contact' | 'company') =>
  container.querySelector(`#deal-${which}`) as HTMLInputElement;

async function settleSearch() {
  await act(async () => { await new Promise(r => setTimeout(r, 600)); });
}

async function openPicker(which: 'contact' | 'company') {
  await act(async () => {
    picker(which).dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
  await settleSearch();
}

async function clickOption(match: (text: string) => boolean) {
  const option = [...container.querySelectorAll('[role="option"]')]
    .find(o => match(o.textContent || ''));
  if (!option) throw new Error('no matching option rendered');
  await act(async () => { option.dispatchEvent(new MouseEvent('click', { bubbles: true })); });
}

const clearLink = (label: string) =>
  container.querySelector(`button[aria-label="${label}"]`) as HTMLButtonElement | null;

// ── Record context (#14) ─────────────────────────────────────────────────────────────────────

describe('record context', () => {
  it('publishes the open deal while mounted and clears it on unmount', async () => {
    render({ deal: makeDeal() });
    await settle();
    // Deals have no route, so this body IS the deal open/close signal for the assistant drawer.
    expect(published).toContain('deal:7');

    published.length = 0;
    act(() => root.render(<MemoryRouter><ActiveRecordProvider><RecordProbe /></ActiveRecordProvider></MemoryRouter>));
    expect(published.at(-1)).toBeNull();
  });
});

// ── The detail read ──────────────────────────────────────────────────────────────────────────

describe('the detail read', () => {
  it('fetches the detail for a board row and renders its activity', async () => {
    render({ deal: makeDeal() });
    await settle();
    expect(dealGets()).toHaveLength(1);
    expect(container.textContent).toContain('Left a voicemail');
  });

  it('does NOT re-fetch a record the layer already resolved through loadById', async () => {
    // A cold deep link arrives with the full payload; fetching again would double every shared
    // link's request count for exactly the same bytes.
    render({ deal: detailResponse(), onBoard: false });
    await settle();
    expect(dealGets()).toHaveLength(0);
    expect(container.textContent).toContain('Left a voicemail');
  });

  it('falls through to the fetched record for keys the host row does not carry', async () => {
    // The dashboard's top-deals query joins the contact name but not the company's. Discarding
    // the fetched copy whenever `onBoard` was true left that row permanently blank.
    api.mockImplementation(routeApi(detailResponse({ title: 'Stale copy' })));
    const listRow = makeDeal({ title: 'Fresh from the host', company_name: undefined });
    render({ deal: listRow, onBoard: true });
    await settle();
    expect(container.textContent).toContain('Northwind');       // from the fetch
    expect(container.textContent).toContain('Fresh from the host'); // canonical still wins
    expect(container.textContent).not.toContain('Stale copy');
  });
});

// ── The close guard ──────────────────────────────────────────────────────────────────────────

describe('the close guard', () => {
  it('never prompts for a reason the app guard will refuse anyway', async () => {
    render({ deal: makeDeal() });
    await settle();
    click(buttonByText('Edit'));
    setField('deal-title', 'Changed');
    await expect(guard!('escape' as DetailCloseReason)).resolves.toBe(true);
    expect(confirmDialog).not.toHaveBeenCalled();
  });

  it('prompts on a real leave while the form is dirty, and honours the answer', async () => {
    render({ deal: makeDeal() });
    await settle();
    click(buttonByText('Edit'));
    setField('deal-title', 'Changed');
    confirmDialog.mockResolvedValue(false);
    await expect(guard!('button' as DetailCloseReason)).resolves.toBe(false);
    expect(confirmDialog).toHaveBeenCalledTimes(1);
  });

  it('stays clean when Edit is opened over blank columns and nothing is changed', async () => {
    // The no-`fieldText` pin: form state and its baseline both come through `toDealForm`, which
    // normalises every blank to ''. Diff a raw record against a normalised form instead and
    // merely LOOKING at a deal with any empty column prompts on the way out.
    const blank = makeDeal({
      notes: '', expected_close_date: '', owner_id: undefined,
      contact_id: null, contact_name: undefined, company_id: null, company_name: undefined,
      probability: 0,
    });
    api.mockImplementation(routeApi(detailResponse(blank)));
    render({ deal: blank });
    await settle();
    click(buttonByText('Edit'));
    await expect(guard!('button' as DetailCloseReason)).resolves.toBe(true);
    expect(confirmDialog).not.toHaveBeenCalled();
  });

  it('composes EVERY draft into the one guard, not just the last one registered', async () => {
    // `registerCloseGuard` REPLACES rather than stacks, so a body that registered one guard per
    // source would silently keep only the second. Dirtying both and clearing one is the only
    // assertion that can tell the difference.
    render({ deal: makeDeal() });
    await settle();
    click(buttonByText('Edit'));
    setField('deal-title', 'Changed');
    click(buttonByText('call'));                       // second source: the quick-log draft
    click(buttonByText('Cancel'));                     // clear the FIRST source only
    await settle(1);

    confirmDialog.mockResolvedValue(false);
    await expect(guard!('button' as DetailCloseReason)).resolves.toBe(false);
    expect(confirmDialog).toHaveBeenCalledTimes(1);
  });

  it('lets a quick-log draft be cleared without discarding anything', async () => {
    // Otherwise picking a chip by accident leaves the body permanently dirty with no
    // non-destructive way back — every exit would prompt.
    render({ deal: makeDeal() });
    await settle();
    click(buttonByText('call'));
    await expect(guard!('button' as DetailCloseReason)).resolves.toBe(true);
    expect(confirmDialog).toHaveBeenCalledTimes(1);

    confirmDialog.mockClear();
    click(buttonByText('Clear'));
    await expect(guard!('button' as DetailCloseReason)).resolves.toBe(true);
    expect(confirmDialog).not.toHaveBeenCalled();
  });

  it('asks the same guard on Mark Won, which never reaches the layer at all', async () => {
    // Mark Won closes through the host callback, so `CollectionDetail.request()` is never
    // consulted — without this the body's own exits are unguarded holes. The dirty source here
    // is the quick-log draft, because the board-mutating buttons are hidden while editing.
    const props = render({ deal: makeDeal() });
    await settle();
    click(buttonByText('call'));
    setField('deal-log-note', 'Half-written');

    confirmDialog.mockResolvedValue(false);
    click(buttonByText('Mark Won'));
    await settle();
    expect(props.onMarkWon).not.toHaveBeenCalled();

    confirmDialog.mockResolvedValue(true);
    click(buttonByText('Mark Won'));
    await settle();
    expect(props.onMarkWon).toHaveBeenCalledTimes(1);
  });

  it('hides the board-mutating buttons while the inline form is open', async () => {
    // Editing and closing are different intents; offering both at once is how a half-typed edit
    // gets silently abandoned by a click two pixels away.
    render({ deal: makeDeal() });
    await settle();
    expect(buttonByText('Mark Won')).not.toBeNull();
    click(buttonByText('Edit'));
    expect(buttonByText('Mark Won')).toBeNull();
    expect(buttonByText('Mark Lost')).toBeNull();
  });
});

// ── Saving ───────────────────────────────────────────────────────────────────────────────────

describe('the inline save', () => {
  it('sends stage and the changed columns in ONE patch', async () => {
    // The backend settles `probability` to 100/0 inside the transaction that moves the stage, so
    // a stage write and a probability write split across two requests is a race whose loser
    // silently wins — a deal marked won at 50%.
    const props = render({ deal: makeDeal() });
    await settle();
    click(buttonByText('Edit'));
    setField('deal-stage', 'won');
    setField('deal-probability', '50');
    click(buttonByText('Save'));
    await settle();

    expect(props.onSaveDeal).toHaveBeenCalledTimes(1);
    const [, patch] = (props.onSaveDeal as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(patch).toEqual({ stage: 'won', probability: 50 });
  });

  it('sends only what changed, and clears a text column with an empty string', async () => {
    // The route applies `exclude_unset` and keeps `null` only for the three FK columns, so a
    // cleared date sent as null would be a silent no-op.
    const props = render({ deal: makeDeal() });
    await settle();
    click(buttonByText('Edit'));
    setField('deal-close', '');
    click(buttonByText('Save'));
    await settle();
    const [, patch] = (props.onSaveDeal as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(patch).toEqual({ expected_close_date: '' });
  });

  it('unlinks a contact with null rather than dropping the key', async () => {
    const props = render({ deal: makeDeal() });
    await settle();
    click(buttonByText('Edit'));
    await settle();
    await act(async () => { clearLink('Clear contact')!.click(); });
    click(buttonByText('Save'));
    await settle();
    const [, patch] = (props.onSaveDeal as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(patch).toEqual({ contact_id: null });
  });

  it('keeps the form and the draft on screen when the save is rejected', async () => {
    const onSaveDeal = vi.fn().mockRejectedValue(new Error('A bulk update is in progress'));
    render({ deal: makeDeal(), onSaveDeal });
    await settle();
    click(buttonByText('Edit'));
    setField('deal-title', 'Half-typed');
    click(buttonByText('Save'));
    await settle();

    expect(input('deal-title').value).toBe('Half-typed');   // the draft is the only copy
    expect(container.textContent).toContain('A bulk update is in progress');
  });

  it('re-reads the record after saving an off-board deal', async () => {
    // Off the board the prop is `loadById`'s one-shot result that no host array will ever
    // replace, so without the re-read a save repaints the panel with pre-save values.
    //
    // Asserted on a field the SAVE RESPONSE does not carry, because the body also folds that
    // response into its read channel: checking the saved title alone would be satisfied by the
    // fold and would pass with `loadDetail()` deleted outright. `lead_score` is derived
    // server-side, arrives only on the detail read, and is exactly the class of value the
    // re-read exists for.
    let title = 'Before';
    let score = 61;
    let detailReads = 0;
    api.mockImplementation((path: string, options?: { method?: string }) => {
      if ((options?.method ?? 'GET') === 'GET' && /^\/api\/crm\/deals\/\d+$/.test(path)) {
        detailReads += 1;
        return Promise.resolve(detailResponse({ title, lead_score: score }));
      }
      return routeApi()(path, options);
    });
    const onSaveDeal = vi.fn((d: CrmDeal, patch: DealPatch) => {
      // What the PUT answers: the row it wrote, with no recomputed score on it.
      const written: CrmDeal = { ...d, ...patch };
      delete written.lead_score;
      return Promise.resolve(written);
    });
    render({ deal: makeDeal({ title: 'Before', lead_score: 61 }), onBoard: false, stageWritable: false, onSaveDeal });
    await settle();
    expect(detailReads).toBe(1);
    click(buttonByText('Edit'));
    setField('deal-title', 'After');
    title = 'After';
    score = 88;
    click(buttonByText('Save'));
    await settle();

    expect(detailReads).toBe(2);
    expect(container.textContent).toContain('After');
    expect(container.textContent).toContain('88');
  });
});

// ── Off-board affordances ────────────────────────────────────────────────────────────────────

describe('board-position writes', () => {
  it('hides the stage field and the close buttons when the board is not showing this deal', async () => {
    render({ deal: makeDeal(), onBoard: false, stageWritable: false });
    await settle();
    expect(buttonByText('Mark Won')).toBeNull();
    expect(buttonByText('Mark Lost')).toBeNull();
    // Everything else stays editable — logging a call against an archived deal is not nonsense.
    expect(buttonByText('Edit')).not.toBeNull();
    expect(buttonByText('Copy link')).not.toBeNull();
    click(buttonByText('Edit'));
    expect(input('deal-stage')).toBeNull();
    expect(input('deal-title')).not.toBeNull();
  });

  it('hides the close buttons on a deal that is already closed', async () => {
    // The detail fetch has to agree: the close-out gate reads the SERVER's stage, not the host
    // row's, so that an ambiguous close reconciles rather than re-offering Mark Lost.
    api.mockImplementation(routeApi(detailResponse({ stage: 'won' })));
    render({ deal: makeDeal({ stage: 'won' }) });
    await settle();
    expect(buttonByText('Mark Won')).toBeNull();
    expect(buttonByText('Mark Lost')).toBeNull();
  });

  it('still offers every stage in the form, so a closed deal can be reopened', async () => {
    render({ deal: makeDeal({ stage: 'won' }) });
    await settle();
    click(buttonByText('Edit'));
    const options = [...(input('deal-stage') as HTMLSelectElement).options].map(o => o.value);
    expect(options).toContain('won');
    expect(options).toContain('lead');
  });
});

// ── Pickers ──────────────────────────────────────────────────────────────────────────────────

describe('the entity pickers', () => {
  it('shows a linked record the search never returns', async () => {
    // The hazard #123 removed: a capped page had no `<option>` for an out-of-page link, so the
    // control rendered BLANK and read as "none". The label is a prop now, taken from the row's
    // own joined names — which is what makes any deal-carrying query owe both of them.
    render({ deal: makeDeal() });
    await settle();
    click(buttonByText('Edit'));
    await settle();

    expect(picker('contact').value).toBe('Dana Reyes');
    expect(picker('company').value).toBe('Northwind');
  });

  it('searches the server rather than scanning a capped page', async () => {
    // The edit path used to fetch `?limit=200` once and filter in the browser, so a company past
    // the alphabetical first 200 was unreachable from here at all.
    render({ deal: makeDeal() });
    await settle();
    click(buttonByText('Edit'));
    await settle();
    await openPicker('contact');

    const links = api.mock.calls
      .map(([p]) => String(p))
      .filter(p => p.startsWith('/api/crm/contacts?') || p.startsWith('/api/crm/companies?'));
    expect(links.length).toBeGreaterThan(0);
    expect(links.every(p => p.includes('limit=20') && !p.includes('limit=200'))).toBe(true);
  });

  it('leaves both links intact when the search itself fails', async () => {
    // A failed search must not silently UNLINK. The widget turns the field into a query box
    // while it is open, so the visible text is not the assertion to make here — what matters is
    // that saving afterwards writes no link change at all.
    const defaults = api.getMockImplementation()!;
    api.mockImplementation((path: string, options?: { method?: string }) =>
      path.startsWith('/api/crm/contacts?') || path.startsWith('/api/crm/companies?')
        ? Promise.reject(new Error('offline'))
        : defaults(path, options));

    const props = render({ deal: makeDeal() });
    await settle();
    click(buttonByText('Edit'));
    await settle();
    await openPicker('contact');
    // Change something else, so a patch is produced at all: an empty one short-circuits before
    // `onSaveDeal`, and "never called" would pass for the wrong reason.
    setField('deal-title', 'Renamed');
    click(buttonByText('Save'));
    await settle();

    const [, patch] = (props.onSaveDeal as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(patch).toEqual({ title: 'Renamed' });
  });
});

describe('the contact→company inference', () => {
  it('fills an EMPTY company from the contact just picked', async () => {
    // `DealForm` has always done this, and losing it in the move to an inline editor would
    // quietly leave newly-linked deals out of their company's rollups — invisible until someone
    // wonders why a company page is short a deal.
    const defaults = api.getMockImplementation()!;
    api.mockImplementation((path: string, options?: { method?: string }) => {
      if (path.startsWith('/api/crm/contacts?')) {
        return Promise.resolve({ contacts: [{ id: 99, name: 'New Lead', company: '', company_name: 'Inherited Co', company_id: 42 }] });
      }
      return defaults(path, options);
    });
    const props = render({ deal: makeDeal({ contact_id: null, contact_name: undefined, company_id: null, company_name: undefined }) });
    await settle();
    click(buttonByText('Edit'));
    await settle();
    await openPicker('contact');
    await clickOption(t => t.includes('New Lead'));
    click(buttonByText('Save'));
    await settle();

    const [, patch] = (props.onSaveDeal as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(patch).toEqual({ contact_id: 99, company_id: 42 });
  });

  it('never overwrites a company the deal already has', async () => {
    // Deal↔company links are independent. Changing the contact must not drag the deal out of the
    // company someone deliberately put it in — a company already on the deal is a deliberate
    // choice too, just an earlier one.
    const defaults = api.getMockImplementation()!;
    api.mockImplementation((path: string, options?: { method?: string }) => {
      if (path.startsWith('/api/crm/contacts?')) {
        return Promise.resolve({ contacts: [{ id: 99, name: 'New Lead', company: '', company_name: 'Inherited Co', company_id: 42 }] });
      }
      return defaults(path, options);
    });
    const props = render({ deal: makeDeal() });   // company_id: 5 already set
    await settle();
    click(buttonByText('Edit'));
    await settle();
    await openPicker('contact');
    await clickOption(t => t.includes('New Lead'));
    click(buttonByText('Save'));
    await settle();

    const [, patch] = (props.onSaveDeal as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(patch).toEqual({ contact_id: 99 });
  });
});

// ── Copy link ────────────────────────────────────────────────────────────────────────────────

describe('copy link', () => {
  it('builds the link from the record id, never from the address bar', async () => {
    // The page strips `?deal=` the moment it reads it, so the address bar is never the link —
    // here it carries an unrelated `?foo=bar` to make reading it visibly wrong.
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    render({ deal: makeDeal() });
    await settle();
    click(buttonByText('Copy link'));
    await settle();
    expect(writeText).toHaveBeenCalledWith(`${window.location.origin}/crm/pipeline?deal=7`);
    expect(toast.success).toHaveBeenCalled();
  });
});

// ── Quick log ────────────────────────────────────────────────────────────────────────────────

describe('the quick-log row', () => {
  it('logs against the deal, then re-reads so the timeline shows it', async () => {
    render({ deal: makeDeal() });
    await settle();
    const before = dealGets().length;
    click(buttonByText('call'));
    setField('deal-log-note', 'Spoke to Dana');
    click(buttonByText('Log'));
    await settle();

    const post = api.mock.calls.find(([p, o]) => p === '/api/crm/activity' && o?.method === 'POST');
    expect(JSON.parse(post![1].body)).toEqual({
      activity: 'call', note: 'Spoke to Dana', contact_id: 3, deal_id: 7,
    });
    expect(dealGets().length).toBe(before + 1);
  });

  it('resets both fields after a successful log, so the body is clean again', async () => {
    render({ deal: makeDeal() });
    await settle();
    click(buttonByText('call'));
    setField('deal-log-note', 'Spoke to Dana');
    click(buttonByText('Log'));
    await settle();
    await expect(guard!('button' as DetailCloseReason)).resolves.toBe(true);
    expect(confirmDialog).not.toHaveBeenCalled();
  });
});

// ── Archived deals (#83) ─────────────────────────────────────────────────────────────────────
//
// Re-homed from `DealDetailSheet.test.tsx` when #75 replaced that component. What is pinned is
// the contract, not the markup: a live deal shows no banner and keeps its close-out actions; an
// archived one shows the banner and DROPS Mark Won/Lost, which the server would refuse outright;
// the inline editor drops its Stage field for the same reason; Restore POSTs to the right route
// and hands the SERVER's row up rather than trusting a refetch; and a failed restore leaves the
// panel usable so the user can try again.

/** Route every child's fetch to an inert payload, with the deal detail under test on top. */
function routeDetail(detail: CrmDeal, over: (path: string) => unknown = () => undefined) {
  api.mockImplementation(async (path: string) => {
    const custom = over(path);
    if (custom !== undefined) return custom;
    if (path.startsWith('/api/crm/provenance/')) return { provenance: [] };
    if (path.startsWith('/api/crm/chatter/')) return { notes: [] };
    if (/\/fields$/.test(path)) return [];
    if (path.startsWith('/api/users')) return { users: [] };
    if (/^\/api\/crm\/deals\/\d+$/.test(path)) return detail;
    return null;
  });
}

describe('archived deals', () => {
  it('shows no banner on a live deal and keeps the close-out actions', async () => {
    routeDetail(detailResponse());
    render({ deal: makeDeal() });
    await settle();

    expect(container.textContent).not.toContain('ARCHIVED');
    expect(buttonByText('Mark Won')).toBeTruthy();
    expect(buttonByText('Restore')).toBeNull();
  });

  it('banners an archived deal and drops Mark Won/Lost, which the server would refuse', async () => {
    const archived = detailResponse({ archived_at: '2026-08-20T00:00:00+00:00' });
    routeDetail(archived);
    render({ deal: makeDeal({ archived_at: '2026-08-20T00:00:00+00:00' }) });
    await settle();

    expect(container.textContent).toContain('ARCHIVED');
    expect(buttonByText('Restore')).toBeTruthy();
    expect(buttonByText('Mark Won')).toBeNull();
    expect(buttonByText('Mark Lost')).toBeNull();
    // Editing an archived deal's OTHER fields is still legal — the server refuses only the stage.
    expect(buttonByText('Edit')).toBeTruthy();
  });

  it('banners a deal archived AFTER the host row was loaded, using the fetched detail', async () => {
    // The board hands over a frozen row. If the assistant archived the deal in between, only the
    // re-fetched detail knows — and `get_deal` deliberately resolves an archived deal, so it
    // does. This is why `archivedAt` bypasses the canonical-host-row merge.
    routeDetail(detailResponse({ archived_at: '2026-08-25T00:00:00+00:00' }));
    render({ deal: makeDeal({ archived_at: null }) });
    await settle();

    expect(container.textContent).toContain('ARCHIVED');
    expect(buttonByText('Restore')).toBeTruthy();
  });

  it('drops the Stage field from the inline editor on a deal archived after the row loaded', async () => {
    // The same drift, one step further. The server refuses a stage change on an archived deal by
    // rejecting the WHOLE update, so an editable Stage here would let the user compose a save
    // that comes back rejected in full — losing every other field they had just typed.
    routeDetail(detailResponse({ archived_at: '2026-08-25T00:00:00+00:00' }));
    render({ deal: makeDeal({ archived_at: null }) });
    await settle();
    await click(buttonByText('Edit'));
    await settle();

    expect(container.querySelector('#deal-stage')).toBeNull();
    // …while every other field is still there to correct.
    expect(container.querySelector('#deal-title')).toBeTruthy();
  });

  it('restores through POST /restore and hands the SERVER row up to the host', async () => {
    const restored = detailResponse({ archived_at: null, stage: 'qualified' });
    routeDetail(
      detailResponse({ archived_at: '2026-08-20T00:00:00+00:00' }),
      path => (path === '/api/crm/deals/7/restore' ? restored : undefined),
    );
    const onRestored = vi.fn();
    render({ deal: makeDeal({ archived_at: '2026-08-20T00:00:00+00:00' }), onRestored });
    await settle();
    await click(buttonByText('Restore'));
    await settle();

    expect(api).toHaveBeenCalledWith('/api/crm/deals/7/restore', { method: 'POST' });
    // The authoritative row, not a re-fetch: a silent refresh can fail invisibly and leave the
    // host still showing the deal as archived after a restore the server actually performed.
    expect(onRestored).toHaveBeenCalledWith(restored);
  });

  it('clears its OWN banner on a restore, not just the host\'s row', async () => {
    // `archivedAt` reads the detail fetch when there is one, so a host that keeps the panel open
    // would otherwise show an ARCHIVED banner over a deal that is no longer archived.
    routeDetail(
      detailResponse({ archived_at: '2026-08-20T00:00:00+00:00' }),
      path => (path === '/api/crm/deals/7/restore'
        ? detailResponse({ archived_at: null })
        : undefined),
    );
    render({ deal: makeDeal({ archived_at: '2026-08-20T00:00:00+00:00' }) });
    await settle();
    await click(buttonByText('Restore'));
    await settle();

    expect(container.textContent).not.toContain('ARCHIVED');
    expect(buttonByText('Mark Won')).toBeTruthy();
  });

  it('keeps the panel usable and says so when a restore fails', async () => {
    routeDetail(detailResponse({ archived_at: '2026-08-20T00:00:00+00:00' }), path => {
      if (path === '/api/crm/deals/7/restore') throw new Error('boom');
      return undefined;
    });
    const onRestored = vi.fn();
    render({ deal: makeDeal({ archived_at: '2026-08-20T00:00:00+00:00' }), onRestored });
    await settle();
    await click(buttonByText('Restore'));
    await settle();

    expect(onRestored).not.toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalledWith('Failed to restore deal.');
    // Re-enabled, so the user can retry rather than being stuck on "Restoring…".
    expect((buttonByText('Restore') as HTMLButtonElement).disabled).toBe(false);
  });
});

// ── Mark Lost captures a reason (#128) ───────────────────────────────────────────────────────

const dialogButton = (label: string) =>
  [...document.body.querySelectorAll('button')]
    .find(b => b.textContent?.trim() === label && !container.contains(b)) ?? null;

describe('Mark Lost captures a reason', () => {
  it('opens the reason dialog instead of closing the deal immediately', async () => {
    routeDetail(detailResponse());
    const props = render({ deal: makeDeal() });
    await settle();

    expect(document.body.querySelector('[role="dialog"]')).toBeNull();
    await click(buttonByText('Mark Lost'));
    await settle();

    expect(document.body.querySelector('[role="dialog"]')).toBeTruthy();
    // The whole point: nothing is written until a reason has been asked for.
    expect(props.onMarkLost).not.toHaveBeenCalled();
  });

  it('hands the typed reason up as a SECOND argument, which is what selects the endpoint', async () => {
    routeDetail(detailResponse());
    const props = render({ deal: makeDeal() });
    await settle();
    await click(buttonByText('Mark Lost'));
    await settle();

    // Scoped to the dialog on purpose: this body's own NotesThread composer is also a textarea
    // and comes first in document order.
    const field = document.body.querySelector<HTMLTextAreaElement>('[role="dialog"] textarea')!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!
        .set!.call(field, 'Lost on price');
      field.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await click(dialogButton('Mark Lost'));
    await settle();

    expect(props.onMarkLost).toHaveBeenCalledWith(expect.objectContaining({ id: 7 }), 'Lost on price');
  });

  it('writes nothing when the dialog is cancelled', async () => {
    routeDetail(detailResponse());
    const props = render({ deal: makeDeal() });
    await settle();
    await click(buttonByText('Mark Lost'));
    await settle();
    await click(dialogButton('Cancel'));
    await settle();

    expect(props.onMarkLost).not.toHaveBeenCalled();
    expect(document.body.querySelector('[role="dialog"]')).toBeNull();
  });

  it('leaves Mark Won a direct, dialog-free stage change', async () => {
    routeDetail(detailResponse());
    const props = render({ deal: makeDeal() });
    await settle();
    await click(buttonByText('Mark Won'));
    await settle();

    // No reason argument at all, which is precisely what routes the write to the plain stage PUT
    // rather than the mark-lost verb — a won deal has no reason to record.
    expect(props.onMarkWon).toHaveBeenCalledWith(expect.objectContaining({ id: 7 }));
  });

  it('disables the close-out pair while a slow write is in flight', async () => {
    // A host that awaits the write and keeps the panel open on failure would otherwise let a
    // second Mark Lost land before the first settles — and `mark_deal_lost` appends its
    // "Deal lost —" note on EVERY call that finds the deal, a no-op write included. The dialog's
    // own latch cannot cover it: that modal unmounts on the first confirm.
    routeDetail(detailResponse());
    let release!: () => void;
    const inFlight = new Promise<void>(res => { release = res; });
    const onMarkWon = vi.fn(() => inFlight);
    render({ deal: makeDeal(), onMarkWon });
    await settle();

    await click(buttonByText('Mark Won'));
    expect(onMarkWon).toHaveBeenCalledTimes(1);
    expect((buttonByText('Mark Won') as HTMLButtonElement).disabled).toBe(true);
    expect((buttonByText('Mark Lost') as HTMLButtonElement).disabled).toBe(true);

    // A second click during the request must not reach the host. `disabled` is what enforces
    // that, which is also why it is asserted above rather than trusted.
    await click(buttonByText('Mark Won'));
    expect(onMarkWon).toHaveBeenCalledTimes(1);

    // …and they come back once it settles, so a host that keeps the panel open after a FAILED
    // write still lets the user retry.
    await act(async () => { release(); await inFlight; });
    await settle();
    expect((buttonByText('Mark Won') as HTMLButtonElement).disabled).toBe(false);
  });
});

// ── An ambiguous close reconciles before a retry (#128) ──────────────────────────────────────

describe('an ambiguous close reconciles before a retry', () => {
  it('re-reads the deal when the host leaves the panel open, and drops the close-out pair if the close landed', async () => {
    // The dangerous case: the POST commits, then the response is lost (a dropped connection, or
    // a 5xx after commit). A host that swallows the error keeps this panel open so the user can
    // retry — but the deal is ALREADY lost, and `mark_deal_lost` appends its note on every call
    // that finds the deal. Retrying would file a duplicate. Reconciling first prevents it.
    let detail = detailResponse({ stage: 'lead' });
    api.mockImplementation(async (path: string) => {
      if (path.startsWith('/api/crm/provenance/')) return { provenance: [] };
      if (path.startsWith('/api/crm/chatter/')) return { notes: [] };
      if (/\/fields$/.test(path)) return [];
      if (path.startsWith('/api/users')) return { users: [] };
      if (/^\/api\/crm\/deals\/\d+$/.test(path)) return detail;
      return null;
    });
    // The host resolves WITHOUT dismissing the panel — its failure path…
    const onMarkWon = vi.fn(async () => {
      // …while the server did in fact commit the close.
      detail = detailResponse({ stage: 'lost', lost_reason: 'price' });
    });
    render({ deal: makeDeal({ stage: 'lead' }), onMarkWon });
    await settle();
    expect(buttonByText('Mark Lost')).toBeTruthy();

    await click(buttonByText('Mark Won'));
    await settle();

    expect(buttonByText('Mark Lost')).toBeNull();
    expect(buttonByText('Mark Won')).toBeNull();
  });

  it('keeps the pair live when the close genuinely did not land', async () => {
    // The other half — a real failure must stay retryable, or the guard traps the user.
    routeDetail(detailResponse({ stage: 'lead' }));
    const onMarkWon = vi.fn(async () => {});
    render({ deal: makeDeal({ stage: 'lead' }), onMarkWon });
    await settle();
    await click(buttonByText('Mark Won'));
    await settle();

    expect((buttonByText('Mark Won') as HTMLButtonElement).disabled).toBe(false);
    expect(buttonByText('Mark Lost')).toBeTruthy();
  });
});

// ── The owner is visible (#128) ──────────────────────────────────────────────────────────────

describe('the owner row', () => {
  it('reads "Unassigned" on an unowned deal rather than disappearing', async () => {
    // Unconditional, unlike its neighbouring rows: hiding it is what made "unassigned"
    // indistinguishable from "not displayed".
    routeDetail(detailResponse({ owner_id: null }));
    render({ deal: makeDeal({ owner_id: null }) });
    await settle();

    expect(container.textContent).toContain('Owner');
    expect(container.textContent).toContain('Unassigned');
  });
});

// ── Exits and lifecycle authority (the settle review's findings) ─────────────────────────────

describe('every exit asks the same guard', () => {
  it('confirms before a Restore discards an open edit draft', async () => {
    // The banner renders OVER the edit form, and every host dismisses the panel on `onRestored`
    // — so "edit an archived deal, then restore it" is a real way to lose a draft. Restore is a
    // leave path like the contact link and Mark Won, and goes through the same `canLeave`.
    routeDetail(
      detailResponse({ archived_at: '2026-08-20T00:00:00+00:00' }),
      path => (path === '/api/crm/deals/7/restore'
        ? detailResponse({ archived_at: null })
        : undefined),
    );
    confirmDialog.mockResolvedValue(false);   // the user says "keep editing"
    const onRestored = vi.fn();
    render({ deal: makeDeal({ archived_at: '2026-08-20T00:00:00+00:00' }), onRestored });
    await settle();
    click(buttonByText('Edit'));
    await settle();
    setField('deal-title', 'Half-typed');
    await click(buttonByText('Restore'));
    await settle();

    expect(confirmDialog).toHaveBeenCalled();
    // Refused, so nothing was written and the draft is still on screen.
    expect(api).not.toHaveBeenCalledWith('/api/crm/deals/7/restore', { method: 'POST' });
    expect(onRestored).not.toHaveBeenCalled();
    expect(container.querySelector<HTMLInputElement>('#deal-title')!.value).toBe('Half-typed');
  });
});

describe('the company the user cleared', () => {
  it('is not resurrected by the next contact pick', async () => {
    // "Fill only when empty" cannot tell a CLEARED company from an unset one, so the link the
    // user just removed comes straight back — and gets saved. `DealForm` guards this with a
    // touched ref for the same reason; the inline editor needs its own.
    const defaults = api.getMockImplementation()!;
    api.mockImplementation((path: string, options?: { method?: string }) => {
      if (path.startsWith('/api/crm/contacts?')) {
        return Promise.resolve({ contacts: [{ id: 99, name: 'New Lead', company: '', company_name: 'Inherited Co', company_id: 42 }] });
      }
      return defaults(path, options);
    });
    const props = render({ deal: makeDeal() });   // company_id: 5
    await settle();
    click(buttonByText('Edit'));
    await settle();
    await act(async () => { clearLink('Clear company')!.click(); });
    await openPicker('contact');
    await clickOption(t => t.includes('New Lead'));
    click(buttonByText('Save'));
    await settle();

    const [, patch] = (props.onSaveDeal as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(patch).toEqual({ contact_id: 99, company_id: null });
  });
});

describe('the lifecycle columns govern the form too, not just the buttons', () => {
  it('opens the Stage select on the SERVER\'s stage, not the host row\'s', async () => {
    // After a close whose response was lost, the board row still says `lead` while the server
    // says `lost`. The close-out pair reads the fetch and disappears — so Edit is the only route
    // left — and a form seeded from the host row would show `lead`, where choosing `lead` (the
    // obvious "reopen this") matches its own baseline and sends NO patch at all.
    routeDetail(detailResponse({ stage: 'lost' }));
    render({ deal: makeDeal({ stage: 'lead' }) });
    await settle();
    click(buttonByText('Edit'));
    await settle();

    expect((container.querySelector('#deal-stage') as HTMLSelectElement).value).toBe('lost');
  });

  it('does not flash the pre-save stage after a stage change is written', async () => {
    // `archivedAt`/`closeStage` read the detail fetch, and `handleSave` refreshes it in the
    // background — so without folding the patch in first, the heading and the close-out pair
    // describe the stage the deal has just left until that request lands.
    // The post-save refetch is HELD for the whole test, which is what makes this about the fold
    // rather than about the refresh: if the assertion were allowed to wait for that request, it
    // would pass with the fold deleted.
    let detailReads = 0;
    routeDetail(detailResponse({ stage: 'proposal' }), path => {
      if (!/^\/api\/crm\/deals\/\d+$/.test(path)) return undefined;
      detailReads += 1;
      return detailReads === 1 ? undefined : new Promise(() => {});
    });
    render({ deal: makeDeal({ stage: 'proposal' }) });
    await settle();
    expect(buttonByText('Mark Won')).toBeTruthy();

    click(buttonByText('Edit'));
    await settle();
    setField('deal-stage', 'won');
    click(buttonByText('Save'));
    await settle();

    // Closed now, so the close-out pair is gone — and the refetch has not answered.
    expect(detailReads).toBe(2);
    expect(buttonByText('Mark Won')).toBeNull();
    expect(buttonByText('Mark Lost')).toBeNull();
  });
});

describe('an exit in flight closes the door behind it', () => {
  it('refuses to open the edit form while a close-out is still writing', async () => {
    // The host dismisses this panel when the write settles, and by then a draft started
    // underneath would be discarded WITHOUT the close guard ever seeing it — the guard ran at
    // click time, before the draft existed, and an unmount cannot be vetoed. The id check that
    // stops a settling write closing someone else's panel cannot help: it really is this deal.
    routeDetail(detailResponse());
    let release!: () => void;
    const inFlight = new Promise<void>(res => { release = res; });
    const onMarkWon = vi.fn(() => inFlight);
    render({ deal: makeDeal(), onMarkWon });
    await settle();

    await click(buttonByText('Mark Won'));
    expect((buttonByText('Edit') as HTMLButtonElement).disabled).toBe(true);

    // ...and it comes back once the write settles, for a host that keeps the panel open.
    await act(async () => { release(); await inFlight; });
    await settle();
    expect((buttonByText('Edit') as HTMLButtonElement).disabled).toBe(false);
  });

  it('refuses it while a restore is writing too', async () => {
    routeDetail(
      detailResponse({ archived_at: '2026-08-20T00:00:00+00:00' }),
      path => (path === '/api/crm/deals/7/restore' ? new Promise(() => {}) : undefined),
    );
    render({ deal: makeDeal({ archived_at: '2026-08-20T00:00:00+00:00' }) });
    await settle();
    await click(buttonByText('Restore'));

    expect((buttonByText('Edit') as HTMLButtonElement).disabled).toBe(true);
  });
});

describe('whose choice the company field is', () => {
  it('never overwrites a company the USER picked during this edit', async () => {
    // The gap the seeded ref alone leaves: on a deal with NO company the ref starts false, so a
    // company the user then picks themselves is still "untouched" unless the picker says
    // otherwise — and the next contact silently replaces it.
    const defaults = api.getMockImplementation()!;
    api.mockImplementation((path: string, options?: { method?: string }) => {
      if (path.startsWith('/api/crm/contacts?')) {
        return Promise.resolve({ contacts: [{ id: 99, name: 'New Lead', company: '', company_name: 'Inherited Co', company_id: 42 }] });
      }
      if (path.startsWith('/api/crm/companies?')) {
        return Promise.resolve({ companies: [{ id: 88, name: 'My Own Co', status: 'active' }] });
      }
      return defaults(path, options);
    });
    const props = render({ deal: makeDeal({ company_id: null, company_name: undefined }) });
    await settle();
    click(buttonByText('Edit'));
    await settle();
    await openPicker('company');
    await clickOption(t => t.includes('My Own Co'));
    await openPicker('contact');
    await clickOption(t => t.includes('New Lead'));
    click(buttonByText('Save'));
    await settle();

    const [, patch] = (props.onSaveDeal as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(patch).toEqual({ contact_id: 99, company_id: 88 });
  });

  it('lets an INFERRED company follow its contact to nothing', async () => {
    // The other half, and the one that goes wrong quietly: filling only when the new contact HAS
    // a company strands the PREVIOUS contact's company on the deal, linking it to an
    // organisation neither the user nor the current contact ever named. `DealForm` has always
    // cleared it; the port dropped that.
    const defaults = api.getMockImplementation()!;
    api.mockImplementation((path: string, options?: { method?: string }) => {
      if (path.startsWith('/api/crm/contacts?')) {
        return Promise.resolve({ contacts: [
          { id: 99, name: 'Linked Lead', company: '', company_name: 'Inherited Co', company_id: 42 },
          { id: 98, name: 'Unlinked Lead', company: '', company_name: '', company_id: null },
        ] });
      }
      return defaults(path, options);
    });
    const props = render({ deal: makeDeal({ company_id: null, company_name: undefined }) });
    await settle();
    click(buttonByText('Edit'));
    await settle();
    await openPicker('contact');
    await clickOption(t => t.includes('Linked Lead'));
    await openPicker('contact');
    await clickOption(t => t.includes('Unlinked Lead'));
    click(buttonByText('Save'));
    await settle();

    const [, patch] = (props.onSaveDeal as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(patch).toEqual({ contact_id: 98 });
  });
});

describe('the save folds the SERVER row, not the request', () => {
  it('shows the probability the server derived, not the one the form sent', async () => {
    // `_classify_deal_update` overrides `probability` to 100/0 inside the transaction that
    // changes the stage, so the patch that went out is not what was stored. Folding the request
    // would paint "Won · 50%" until the background re-read lands — and leave it there for good
    // if that request failed. The re-read is HELD here so the fold is what is being asserted.
    let detailReads = 0;
    routeDetail(detailResponse({ stage: 'proposal', probability: 50 }), path => {
      if (!/^\/api\/crm\/deals\/\d+$/.test(path)) return undefined;
      detailReads += 1;
      return detailReads === 1 ? undefined : new Promise(() => {});
    });
    // OFF the board, which is where this bites: there the body's own read channel is the WHOLE
    // record, so a folded request value is what the panel shows for every column, not just the
    // two lifecycle ones. On the board the host patches its row from the same response.
    render({ deal: makeDeal({ stage: 'proposal', probability: 50 }), onBoard: false });
    await settle();
    click(buttonByText('Edit'));
    await settle();
    setField('deal-stage', 'won');
    click(buttonByText('Save'));
    await settle();

    expect(detailReads).toBe(2);
    expect(container.textContent).toContain('100%');
    expect(container.textContent).not.toContain('50%');
  });
});

describe('the quick-log row during an exit', () => {
  it('accepts no new draft while a close-out is writing', async () => {
    // The other draft this body owns. The close guard ran at click time, before any of it
    // existed, and the host dismisses the panel when the write settles — so an activity note
    // typed underneath disappears with no prompt and no trace.
    routeDetail(detailResponse());
    let release!: () => void;
    const inFlight = new Promise<void>(res => { release = res; });
    const onMarkWon = vi.fn(() => inFlight);
    render({ deal: makeDeal(), onMarkWon });
    await settle();

    const chip = () => [...container.querySelectorAll('button')]
      .find(b => b.textContent?.trim() === 'call') as HTMLButtonElement;
    expect(chip().disabled).toBe(false);

    await click(buttonByText('Mark Won'));
    expect(chip().disabled).toBe(true);

    await act(async () => { release(); await inFlight; });
    await settle();
    expect(chip().disabled).toBe(false);
  });
});
