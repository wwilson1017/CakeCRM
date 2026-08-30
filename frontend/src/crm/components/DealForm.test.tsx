// @vitest-environment jsdom
//
// Why the Stage lock on an archived deal (issue #83) is worth a test of its own: the
// server refuses a stage change on an archived deal by raising out of
// `_classify_deal_update`, and that rejects the WHOLE update. So if this guard regressed,
// a user editing an archived deal would not merely fail to re-stage it — they would lose
// the title, value, notes and every other field they had just typed, to an error message
// that names none of that.
//
// The lock is also deliberately narrow: an archived deal stays editable in every other
// respect, so the test pins both halves. Otherwise a future "just disable the form" fix
// would look like a pass.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { CrmContact, CrmDeal } from '../../core/types';

const api = vi.hoisted(() => vi.fn());
vi.mock('../../core/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../core/api/client')>()),
  api,
}));
const toast = vi.hoisted(() => ({ error: vi.fn(), info: vi.fn(), success: vi.fn() }));
vi.mock('../../shared/toast', () => ({ toast }));

const { DealForm } = await import('./DealForm');
// The form reads the signed-in account to default a new deal's owner. Provided rather
// than mocked: with no token in sessionStorage the provider settles without a request.
const { AuthProvider } = await import('../../core/auth/AuthContext');

function deal(over: Partial<CrmDeal> = {}): CrmDeal {
  return {
    id: 7, title: 'Wholesale order', stage: 'qualified', value: 1000, probability: 20,
    expected_close_date: '', notes: '', contact_id: null, company_id: null, currency: 'USD',
    archived_at: null,
    created_at: '2026-08-01T00:00:00+00:00', updated_at: '2026-08-01T00:00:00+00:00',
    ...over,
  };
}

const STAGE_HINT = 'Restore the deal to change its stage.';

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  // Clear, not just re-stub: `api.mock.calls` otherwise accumulates across tests in
  // this file, and the assertions below search those calls for a PUT — a later test
  // would happily find an EARLIER test's request and pass on it.
  api.mockClear();
  api.mockImplementation(async (path: string) => {
    if (path.startsWith('/api/crm/contacts')) return { contacts: [] };
    if (path.startsWith('/api/crm/companies')) return { companies: [] };
    if (path === '/api/users') return { users: [] };
    if (path.includes('/fields')) return [];
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

async function render(d: CrmDeal) {
  await act(async () => {
    root.render(<AuthProvider><DealForm deal={d} onClose={() => {}} onSaved={() => {}} /></AuthProvider>);
  });
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
}

/** The Stage control, located through its own label rather than by index — the form has
 *  four `<select>`s and their order is not a contract. */
function stageSelect(): HTMLSelectElement {
  const label = [...container.querySelectorAll('label')]
    .find(l => l.textContent?.trim() === 'Stage');
  const select = label?.parentElement?.querySelector('select');
  if (!select) throw new Error('no Stage select rendered');
  return select as HTMLSelectElement;
}

function titleInput(): HTMLInputElement {
  const label = [...container.querySelectorAll('label')]
    .find(l => l.textContent?.trim() === 'Title *');
  const input = label?.parentElement?.querySelector('input');
  if (!input) throw new Error('no Title input rendered');
  return input as HTMLInputElement;
}

/** Type into a controlled input the way React can see. Assigning `.value` directly is
 *  invisible to React: it caches the last value on the DOM node, sees no change, and never
 *  re-runs onChange — so the form state would keep its original value and the assertion
 *  would silently be about nothing. */
function typeInto(el: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
  setter?.call(el, value);
  el.dispatchEvent(new Event('input', { bubbles: true }));
}

describe('DealForm — archived deals', () => {
  it('locks the Stage select on an archived deal and says why', async () => {
    await render(deal({ archived_at: '2026-08-20T00:00:00+00:00' }));

    expect(stageSelect().disabled).toBe(true);
    expect(container.textContent).toContain(STAGE_HINT);
  });

  it('leaves every OTHER field editable on an archived deal', async () => {
    // The server rejects only the stage change, so locking more than the stage would be a
    // regression of its own — an archived deal is still a record you can correct.
    await render(deal({ archived_at: '2026-08-20T00:00:00+00:00' }));

    expect(titleInput().disabled).toBe(false);
  });

  it('leaves the Stage select editable on a live deal', async () => {
    await render(deal());

    expect(stageSelect().disabled).toBe(false);
    expect(container.textContent).not.toContain(STAGE_HINT);
  });

  it('OMITS stage from an archived deal\'s update, so a stale value cannot sink the save', async () => {
    // Locking the control is not enough on its own. The form still sent `stage` from the
    // row it opened with, and that row can be stale: if the deal moved stage elsewhere
    // (the assistant, another tab) after the board loaded, the value behind the disabled
    // select no longer matches the server's — and the server refuses a stage CHANGE on an
    // archived deal by rejecting the whole update. The user would lose every field they
    // just typed, behind a control the UI had disabled and captioned as safe.
    //
    // A disabled select's value is by definition not user intent, so the field is simply
    // not sent. Omitted, not "sent unchanged": only omission is immune to the drift.
    await render(deal({ stage: 'lead', archived_at: '2026-08-20T00:00:00+00:00' }));
    await act(async () => { typeInto(titleInput(), 'Corrected title'); });
    await act(async () => {
      container.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });

    const put = api.mock.calls.find(([p, init]) => p === '/api/crm/deals/7' && init?.method === 'PUT');
    expect(put).toBeTruthy();
    const body = JSON.parse(put![1].body as string) as Record<string, unknown>;
    expect(body).not.toHaveProperty('stage');
    // ...while the edit the user actually made still goes.
    expect(body.title).toBe('Corrected title');
  });

  it('still sends stage for a LIVE deal, where the select IS user intent', async () => {
    await render(deal({ stage: 'qualified' }));
    await act(async () => {
      container.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });

    const put = api.mock.calls.find(([p, init]) => p === '/api/crm/deals/7' && init?.method === 'PUT');
    const body = JSON.parse(put![1].body as string) as Record<string, unknown>;
    expect(body.stage).toBe('qualified');
  });
});

// ── Inline quick-create (issue #123) ─────────────────────────────────────────
//
// The two link fields stopped being <select>s over a capped 200-row fetch and became
// server-searching comboboxes that can create the record they could not find. What is
// worth pinning at THIS level (RecordCombobox.test.tsx covers the widget itself) is the
// wiring: which endpoint each quick-create hits, that the ids reach the deal write, and
// that the contact→company auto-fill rule survived the rewrite intact.

function contact(over: Partial<CrmContact> = {}): CrmContact {
  return {
    id: 1, name: 'Existing Person', email: '', phone: '', company: '', company_id: null,
    title: '', source: '', status: 'active', tags: '', notes: '',
    created_at: '', updated_at: '', ...over,
  };
}

/** Let the picker's 250ms debounce and its fetch settle. Real timers: this file does not
 *  use fake ones, and mixing the two around React's act() is more fragile than waiting. */
async function settleSearch() {
  await act(async () => { await new Promise(r => setTimeout(r, 300)); });
}

function combobox(which: 'contact' | 'company'): HTMLInputElement {
  const el = container.querySelector(`#deal-${which}`);
  if (!el) throw new Error(`no ${which} combobox rendered`);
  return el as HTMLInputElement;
}

/** Open one picker. Only one list is ever open, so the open listbox is unambiguous. */
async function openPicker(which: 'contact' | 'company') {
  await act(async () => {
    combobox(which).dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
  await settleSearch();
}

async function typeInPicker(which: 'contact' | 'company', value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
  await act(async () => {
    setter?.call(combobox(which), value);
    combobox(which).dispatchEvent(new Event('input', { bubbles: true }));
  });
  await settleSearch();
}

async function clickOption(match: (text: string) => boolean) {
  const button = [...container.querySelectorAll('[role="option"] button')]
    .find(b => match(b.textContent || ''));
  if (!button) throw new Error('no matching option rendered');
  await act(async () => { button.dispatchEvent(new MouseEvent('click', { bubbles: true })); });
}

async function renderNew() {
  await act(async () => {
    root.render(<AuthProvider><DealForm onClose={() => {}} onSaved={() => {}} /></AuthProvider>);
  });
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
}

/** Route the picker traffic; `contacts` is what a contact search returns. */
function mockApi(contacts: CrmContact[] = []) {
  api.mockImplementation(async (path: string, init?: RequestInit) => {
    const method = init?.method || 'GET';
    if (method === 'POST' && path === '/api/crm/contacts') {
      return contact({ id: 11, name: JSON.parse(init!.body as string).name });
    }
    if (method === 'POST' && path === '/api/crm/companies/resolve') {
      return { id: 22, name: JSON.parse(init!.body as string).name, status: 'active' };
    }
    if (method === 'POST' && path === '/api/crm/deals') return { id: 5 };
    if (path.startsWith('/api/crm/contacts')) return { contacts };
    if (path.startsWith('/api/crm/companies')) return { companies: [] };
    if (path === '/api/users') return { users: [] };
    if (path.includes('/fields')) return [];
    return null;
  });
}

function submit() {
  return act(async () => {
    container.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  });
}

function dealPost(): Record<string, unknown> {
  const call = api.mock.calls.find(([p, init]) => p === '/api/crm/deals' && init?.method === 'POST');
  if (!call) throw new Error('no deal POST issued');
  return JSON.parse(call[1].body as string) as Record<string, unknown>;
}

describe('DealForm — inline quick-create', () => {
  it('creates a contact AND a company from the form and links both to the new deal', async () => {
    // The acceptance criterion: from New Deal, with nothing matching, land a saved deal
    // linked to a brand-new contact and company without ever leaving the form.
    mockApi();
    await renderNew();
    await act(async () => { typeInto(titleInput(), 'Brand new prospect'); });

    await openPicker('contact');
    await typeInPicker('contact', 'Jane Doe');
    await clickOption(t => t.startsWith('Create '));

    await openPicker('company');
    await typeInPicker('company', 'Newco');
    await clickOption(t => t.startsWith('Create '));

    await submit();

    expect(dealPost()).toMatchObject({ title: 'Brand new prospect', contact_id: 11, company_id: 22 });
  });

  it('quick-creates the CONTACT with the name only, so the server assigns the owner', async () => {
    // owner_id is omitted rather than sent: _create_payload distinguishes absent from
    // explicit null, and absent is what makes the record yours. Sending currentUser.id
    // would also race a login that has not resolved yet.
    mockApi();
    await renderNew();
    await openPicker('contact');
    await typeInPicker('contact', 'Jane Doe');
    await clickOption(t => t.startsWith('Create '));

    const post = api.mock.calls.find(([p, i]) => p === '/api/crm/contacts' && i?.method === 'POST');
    expect(JSON.parse(post![1].body as string)).toEqual({ name: 'Jane Doe' });
  });

  it('quick-creates the COMPANY through the resolver, not POST /companies', async () => {
    // POST /companies INSERTs unconditionally and 400s on a name that already exists
    // case/whitespace-insensitively, which is exactly the wrong answer for a picker.
    mockApi();
    await renderNew();
    await openPicker('company');
    await typeInPicker('company', 'Newco');
    await clickOption(t => t.startsWith('Create '));

    expect(api.mock.calls.some(([p, i]) => p === '/api/crm/companies' && i?.method === 'POST')).toBe(false);
    const post = api.mock.calls.find(([p, i]) => p === '/api/crm/companies/resolve' && i?.method === 'POST');
    expect(JSON.parse(post![1].body as string)).toEqual({ name: 'Newco' });
  });

  it('searches the server with ?q= rather than scanning a capped page', async () => {
    // The issue's own pointer said `?search=`, which list_contacts ignores — it would have
    // shipped a picker that always showed the unfiltered first page.
    mockApi([contact({ id: 3, name: 'Acme Person' })]);
    await renderNew();
    await openPicker('contact');
    await typeInPicker('contact', 'Acme');

    expect(api.mock.calls.some(([p]) => typeof p === 'string' && p.includes('/api/crm/contacts?limit=20&q=Acme'))).toBe(true);
  });
});

describe('DealForm — contact→company auto-fill', () => {
  it('fills the company from a picked contact when no company is set', async () => {
    mockApi([contact({ id: 3, name: 'Acme Person', company_id: 9, company_name: 'Acme Corp' })]);
    await renderNew();
    await act(async () => { typeInto(titleInput(), 'Auto-filled deal'); });
    await openPicker('contact');
    await clickOption(t => t.includes('Acme Person'));
    await submit();

    expect(dealPost()).toMatchObject({ contact_id: 3, company_id: 9 });
  });

  it('never overwrites a company the user chose deliberately', async () => {
    // deal↔company links are independent of the contact's, so changing the contact must
    // not silently re-point a company the user picked on purpose.
    mockApi([contact({ id: 3, name: 'Acme Person', company_id: 9, company_name: 'Acme Corp' })]);
    await renderNew();
    await act(async () => { typeInto(titleInput(), 'Deliberate company'); });
    await openPicker('company');
    await typeInPicker('company', 'Chosen Co');
    await clickOption(t => t.startsWith('Create '));      // company = 22
    await openPicker('contact');
    await clickOption(t => t.includes('Acme Person'));    // carries company 9

    await submit();

    expect(dealPost()).toMatchObject({ contact_id: 3, company_id: 22 });
  });
});

describe('DealForm — the linked record on an edit', () => {
  it('shows a linked contact and company the search never returns', async () => {
    // The capped-page hazard: the old <select> had no <option> for an out-of-page link, so
    // it rendered BLANK and read as "no contact". DealForm hand-patched that for the deal's
    // own company and never did for its contact; both are now structural.
    mockApi();
    await render(deal({
      contact_id: 4242, contact_name: 'Very Old Contact',
      company_id: 9999, company_name: 'Very Old Company Ltd',
    }));

    expect(combobox('contact').value).toBe('Very Old Contact');
    expect(combobox('company').value).toBe('Very Old Company Ltd');
  });
});
