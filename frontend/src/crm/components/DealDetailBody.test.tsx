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
    onSaveDeal: vi.fn().mockResolvedValue(undefined),
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
    setField('deal-contact', '');
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
    let title = 'Before';
    api.mockImplementation((path: string, options?: { method?: string }) => {
      if ((options?.method ?? 'GET') === 'GET' && /^\/api\/crm\/deals\/\d+$/.test(path)) {
        return Promise.resolve(detailResponse({ title }));
      }
      return routeApi()(path, options);
    });
    render({ deal: makeDeal({ title: 'Before' }), onBoard: false, stageWritable: false });
    await settle();
    click(buttonByText('Edit'));
    setField('deal-title', 'After');
    title = 'After';
    click(buttonByText('Save'));
    await settle();
    expect(container.textContent).toContain('After');
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
  it('keeps a linked record selectable when it falls outside the fetched page', async () => {
    // #35 auto-creates a company per distinct imported name, so a link can easily sit past the
    // capped 200 rows — and a blank <select> reads as "no company", not as "not on this page".
    render({ deal: makeDeal() });
    await settle();
    click(buttonByText('Edit'));
    await settle();
    expect((input('deal-contact') as HTMLSelectElement).value).toBe('3');
    expect((input('deal-company') as HTMLSelectElement).value).toBe('5');
    expect(container.textContent).toContain('Dana Reyes');
    expect(container.textContent).toContain('Northwind');
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
