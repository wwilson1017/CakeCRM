// @vitest-environment jsdom
//
// Issue #126 merged this form's two company controls — a free-text input bound to the
// legacy `contacts.company` column, and a <select> over a capped 200-row fetch — into one
// searchable `RecordCombobox` with inline quick-create.
//
// `RecordCombobox.test.tsx` already covers the widget. What is worth pinning HERE is the
// thing the merge made possible to get wrong: WHICH company keys the form sends, and when.
// A contact can hold free text with no company row behind it (a pre-#35 import the backfill
// migration could not match), and that text is the only record of the name. `PUT
// /contacts/:id` reads its body with `model_dump(exclude_unset=True)`, so the difference
// between omitting `company` and sending `company: ''` is the difference between preserving
// that name and destroying it — for someone who only opened the form to fix a phone number.
// Nothing on screen distinguishes the two, which is exactly why it needs a test.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '../../core/api/client';
import type { CrmContact } from '../../core/types';

const api = vi.hoisted(() => vi.fn());
vi.mock('../../core/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../core/api/client')>()),
  api,
}));
const toast = vi.hoisted(() => ({ error: vi.fn(), info: vi.fn(), success: vi.fn() }));
vi.mock('../../shared/toast', () => ({ toast }));

const { ContactForm } = await import('./ContactForm');
// The form reads the signed-in account to default a new contact's owner. Provided rather
// than mocked: with no token in sessionStorage the provider settles without a request.
const { AuthProvider } = await import('../../core/auth/AuthContext');

interface CompanyRow { id: number; name: string; status: string; domain?: string }

function contact(over: Partial<CrmContact> = {}): CrmContact {
  return {
    id: 1, name: 'Existing Person', email: '', phone: '', company: '', company_id: null,
    title: '', source: '', status: 'active', tags: '', notes: '',
    created_at: '', updated_at: '', ...over,
  };
}

let container: HTMLDivElement;
let root: Root;

/** Routes every request the form makes. `companies` is what a company search returns. */
function mockApi(companies: CompanyRow[] = [], over: {
  resolve?: (name: string) => Promise<CompanyRow>;
} = {}) {
  api.mockImplementation(async (path: string, init?: RequestInit) => {
    const method = init?.method || 'GET';
    if (method === 'POST' && path === '/api/crm/companies/resolve') {
      const { name } = JSON.parse(init!.body as string) as { name: string };
      return over.resolve ? over.resolve(name) : { id: 22, name, status: 'active' };
    }
    if (method === 'POST' && path === '/api/crm/contacts') return contact({ id: 11 });
    if (method === 'PUT' && path.startsWith('/api/crm/contacts/')) return contact();
    if (path.startsWith('/api/crm/companies')) return { companies };
    if (path === '/api/users') return { users: [] };
    if (path.includes('/fields')) return [];
    return null;
  });
}

beforeEach(() => {
  // Clear, not just re-stub: `api.mock.calls` otherwise accumulates across tests in this
  // file, and the assertions below SEARCH those calls for a PUT/POST — a later test would
  // happily find an earlier test's request and pass on it.
  api.mockClear();
  mockApi();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

async function render(c?: CrmContact, onWriteUncertain?: (err: unknown) => void) {
  await act(async () => {
    root.render(
      <AuthProvider>
        <ContactForm contact={c} onClose={() => {}} onSaved={() => {}} onWriteUncertain={onWriteUncertain} />
      </AuthProvider>,
    );
  });
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
}

function combobox(): HTMLInputElement {
  const el = container.querySelector('#contact-company');
  if (!el) throw new Error('no company combobox rendered');
  return el as HTMLInputElement;
}

function nameInput(): HTMLInputElement {
  const label = [...container.querySelectorAll('label')]
    .find(l => l.textContent?.trim() === 'Name *');
  const input = label?.parentElement?.querySelector('input');
  if (!input) throw new Error('no Name input rendered');
  return input as HTMLInputElement;
}

/** Type into a controlled input the way React can see. Assigning `.value` directly is
 *  invisible to React: it caches the last value on the DOM node, sees no change, and never
 *  re-runs onChange — so the assertion would silently be about nothing. */
function typeInto(el: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
  setter?.call(el, value);
  el.dispatchEvent(new Event('input', { bubbles: true }));
}

/** Let the picker's 250ms debounce and its fetch settle. Real timers, matching
 *  `DealForm.test.tsx`: the cushion is generous on purpose, because a tight margin between
 *  two real timers is exactly the shape that goes intermittently red on a loaded runner. */
async function settleSearch() {
  await act(async () => { await new Promise(r => setTimeout(r, 600)); });
}

async function openPicker() {
  await act(async () => {
    combobox().dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
  await settleSearch();
}

async function typeInPicker(value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
  await act(async () => {
    setter?.call(combobox(), value);
    combobox().dispatchEvent(new Event('input', { bubbles: true }));
  });
  await settleSearch();
}

async function clickOption(match: (text: string) => boolean) {
  const option = [...container.querySelectorAll('[role="option"]')]
    .find(o => match(o.textContent || ''));
  if (!option) throw new Error('no matching option rendered');
  await act(async () => { option.dispatchEvent(new MouseEvent('click', { bubbles: true })); });
}

function submit() {
  return act(async () => {
    container.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  });
}

function put(): Record<string, unknown> {
  const call = api.mock.calls.find(([p, i]) => p === '/api/crm/contacts/1' && i?.method === 'PUT');
  if (!call) throw new Error('no contact PUT issued');
  return JSON.parse(call[1].body as string) as Record<string, unknown>;
}

function post(): Record<string, unknown> {
  const call = api.mock.calls.find(([p, i]) => p === '/api/crm/contacts' && i?.method === 'POST');
  if (!call) throw new Error('no contact POST issued');
  return JSON.parse(call[1].body as string) as Record<string, unknown>;
}

const NOT_LINKED = 'Not linked to a company record';

describe('ContactForm — one company field', () => {
  it('renders a single company control, not the old free-text input beside a <select>', async () => {
    await render(contact());

    expect(container.querySelectorAll('#contact-company')).toHaveLength(1);
    // The old pair was labelled "Company" (free text) and "Linked Company" (<select>).
    const labels = [...container.querySelectorAll('label')].map(l => l.textContent?.trim());
    expect(labels).toContain('Company');
    expect(labels).not.toContain('Linked Company');
  });

  it('no longer bulk-fetches 200 companies to fill a <select>', async () => {
    // The capped page was the whole reason a linked company could render blank. The picker
    // asks the server per keystroke instead, so nothing should request the bulk page.
    await render(contact({ company_id: 9, company_name: 'Acme Corp' }));

    expect(api.mock.calls.some(([p]) => typeof p === 'string' && p.includes('limit=200'))).toBe(false);
  });

  it('shows a linked company that no bulk page would have contained', async () => {
    // The label is a PROP, so an out-of-page link displays by construction — this is the
    // hazard the old synthetic-<option> guard existed to paper over.
    await render(contact({ company_id: 4321, company_name: 'Zeta Industries' }));

    expect(combobox().value).toBe('Zeta Industries');
  });
});

describe('ContactForm — the legacy free-text column', () => {
  it('omits BOTH company keys when the user never touches the field', async () => {
    // The data-loss guard. `exclude_unset` means an omitted key is not written at all, so
    // omitting is the only way this contact's only record of "Acme Widgets" survives an
    // edit to an unrelated field. Sending `company: ''` here would erase it silently.
    await render(contact({ company: 'Acme Widgets', company_id: null }));
    await act(async () => { typeInto(nameInput(), 'Corrected Name'); });
    await submit();

    const body = put();
    expect(body).not.toHaveProperty('company');
    expect(body).not.toHaveProperty('company_id');
    // ...while the edit the user actually made still goes.
    expect(body.name).toBe('Corrected Name');
  });

  it('omits them for a LINKED contact too, so an unrelated edit cannot re-point the link', async () => {
    await render(contact({ company: 'Acme Corp', company_id: 9, company_name: 'Acme Corp' }));
    await act(async () => { typeInto(nameInput(), 'Corrected Name'); });
    await submit();

    const body = put();
    expect(body).not.toHaveProperty('company');
    expect(body).not.toHaveProperty('company_id');
  });

  it('shows unlinked free text and says it is not linked yet', async () => {
    // Rendering the field blank would state the contact has no company, which is a lie the
    // record itself contradicts.
    await render(contact({ company: 'Acme Widgets', company_id: null }));

    expect(combobox().placeholder).toBe('Acme Widgets');
    expect(container.textContent).toContain(NOT_LINKED);
  });

  it('stops advertising that text once the user speaks for the field', async () => {
    // Left in place, the empty label would keep naming a company the user had just cleared.
    mockApi([{ id: 9, name: 'Acme Corp', status: 'active' }]);
    await render(contact({ company: 'Acme Widgets', company_id: null }));
    await openPicker();
    await clickOption(t => t.includes('Acme Corp'));

    expect(container.textContent).not.toContain(NOT_LINKED);
    expect(combobox().placeholder).not.toBe('Acme Widgets');
  });

  it('says nothing about linking when there is no free text to explain', async () => {
    await render(contact());

    expect(container.textContent).not.toContain(NOT_LINKED);
  });

  it('lets an unlinked legacy name be REMOVED without linking a company first', async () => {
    // The picker's own × is gated on a non-null value, so an unlinked contact has no clear
    // action inside the widget — and `companyTouched` is set only by choosing or clearing.
    // Without the inline Remove button, deleting a wrong legacy name would require linking
    // some company to the contact first, which is a capability the free-text input had.
    await render(contact({ company: 'Wrong Name Ltd', company_id: null }));
    expect(container.querySelector('[aria-label="Clear company"]')).toBeNull();

    const remove = [...container.querySelectorAll('button')]
      .find(b => b.textContent?.trim() === 'Remove');
    if (!remove) throw new Error('no Remove action rendered');
    await act(async () => { remove.dispatchEvent(new MouseEvent('click', { bubbles: true })); });
    await submit();

    const body = put();
    expect(body.company).toBe('');
    expect(body.company_id).toBeNull();
  });
});

describe('ContactForm — picking, clearing and creating', () => {
  it('sends the id and the company NAME once a company is picked', async () => {
    mockApi([{ id: 9, name: 'Acme Corp', status: 'active' }]);
    await render(contact({ company: 'Acme Widgets', company_id: null }));
    await openPicker();
    await clickOption(t => t.includes('Acme Corp'));
    await submit();

    expect(put()).toMatchObject({ company_id: 9, company: 'Acme Corp' });
  });

  it('writes the plain name for an ARCHIVED company, not its decorated label', async () => {
    // The picker shows "Acme Corp (archived)" so the closed control stays honest. That
    // decoration is display only — writing it into `contacts.company` would put a
    // parenthetical into the column every export and legacy reader falls back to.
    mockApi([{ id: 9, name: 'Acme Corp', status: 'archived' }]);
    await render(contact());
    await openPicker();
    await clickOption(t => t.includes('Acme Corp'));
    await submit();

    expect(put()).toMatchObject({ company_id: 9, company: 'Acme Corp' });
  });

  it('unlinks and clears the text when the user clears the field', async () => {
    // Clearing IS the user speaking for the field, so both columns go — unlike the
    // untouched case above. `company_id: null` survives the router's null filter and means
    // "unlink"; the text goes with it, because the field on screen is now empty.
    await render(contact({ company: 'Acme Corp', company_id: 9, company_name: 'Acme Corp' }));
    const clear = container.querySelector('[aria-label="Clear company"]');
    if (!clear) throw new Error('no clear button rendered');
    await act(async () => { clear.dispatchEvent(new MouseEvent('click', { bubbles: true })); });
    await submit();

    const body = put();
    expect(body.company_id).toBeNull();
    expect(body.company).toBe('');
  });

  it('quick-creates a company through the resolver and links it to a new contact', async () => {
    // POST /companies INSERTs unconditionally and 400s on a case/whitespace duplicate,
    // which is the wrong answer for a picker whose job is to land you on the row.
    await render();
    await act(async () => { typeInto(nameInput(), 'Brand New Person'); });
    await openPicker();
    await typeInPicker('Newco');
    await clickOption(t => t.startsWith('Create '));
    await submit();

    expect(api.mock.calls.some(([p, i]) => p === '/api/crm/companies' && i?.method === 'POST')).toBe(false);
    const resolve = api.mock.calls.find(([p, i]) => p === '/api/crm/companies/resolve' && i?.method === 'POST');
    expect(JSON.parse(resolve![1].body as string)).toEqual({ name: 'Newco' });
    expect(post()).toMatchObject({ name: 'Brand New Person', company_id: 22, company: 'Newco' });
  });

  it('always sends both company keys on a CREATE, even untouched', async () => {
    // There is no prior value to protect, and `POST /contacts` dumps WITHOUT exclude_unset
    // — absent-vs-null is not expressible there — so the create path has no reason to omit.
    await render();
    await act(async () => { typeInto(nameInput(), 'Companyless Person'); });
    await submit();

    const body = post();
    expect(body.company_id).toBeNull();
    expect(body.company).toBe('');
    // ...while `owner_id` on the SAME request stays omitted. The two fields were given
    // opposite create-time defaults for different reasons — company has no absent-vs-null
    // semantics on create, owner needs the server to assign the caller — so pin that a
    // future refactor cannot couple them behind one shared "touched" notion.
    expect(body).not.toHaveProperty('owner_id');
  });

  it('searches the server with ?q= rather than scanning a capped page', async () => {
    mockApi([{ id: 9, name: 'Acme Corp', status: 'active' }]);
    await render();
    await openPicker();
    await typeInPicker('Acme');

    expect(api.mock.calls.some(([p]) => typeof p === 'string' && p.includes('/api/crm/companies?limit=20&q=Acme'))).toBe(true);
  });

  it('does NOT commit a name that was typed but never selected or created', async () => {
    // A deliberate consequence of reusing RecordCombobox unchanged, pinned so it cannot
    // change silently: the widget keeps its query private and tells the parent only on
    // choose / create / clear. The removed free-text input committed on Save instead. The
    // same is true of DealForm's two pickers since #123 — it is the component's contract,
    // not something this form introduced. `Create "…"` is the commit affordance.
    await render(contact({ company: 'Acme Widgets', company_id: null }));
    await openPicker();
    await typeInPicker('Typed But Never Committed');
    await submit();

    const body = put();
    expect(body).not.toHaveProperty('company');
    expect(body).not.toHaveProperty('company_id');
  });

  it('refuses to save while a quick-create is still in flight', async () => {
    // Clicking Save is itself a click OUTSIDE the picker, and dismissing the picker
    // deliberately does not abandon the create — so without this the contact would be
    // filed without a link that is about to exist, leaving the new company orphaned.
    let release!: (co: CompanyRow) => void;
    const pending = new Promise<CompanyRow>(res => { release = res; });
    mockApi([], { resolve: () => pending });

    await render();
    await act(async () => { typeInto(nameInput(), 'Impatient Person'); });
    await openPicker();
    await typeInPicker('Slowco');
    await clickOption(t => t.startsWith('Create '));
    await submit();

    expect(api.mock.calls.some(([p, i]) => p === '/api/crm/contacts' && i?.method === 'POST')).toBe(false);
    expect(container.textContent).toContain('Still creating the company');

    // ...and once it lands, the SAME Save now files the contact against the company that
    // was created. Refusing the submit is only correct if the work is not lost.
    await act(async () => { release({ id: 22, name: 'Slowco', status: 'active' }); await pending; });
    await submit();

    expect(post()).toMatchObject({ name: 'Impatient Person', company_id: 22, company: 'Slowco' });
  });

  it('will not let Remove fire while a quick-create is in flight', async () => {
    // Remove lives OUTSIDE RecordCombobox, so it cannot bump the widget's private intent
    // counter — the thing that tells a late-landing create it was superseded. Dismissing the
    // popover deliberately does not abandon the create, so an unguarded Remove could be
    // pressed after the click-away and then silently overwritten when the create resolved,
    // re-linking the company the user had just removed.
    let release!: (co: CompanyRow) => void;
    const pending = new Promise<CompanyRow>(res => { release = res; });
    mockApi([], { resolve: () => pending });

    await render(contact({ company: 'Wrong Name Ltd', company_id: null }));
    await openPicker();
    await typeInPicker('Slowco');
    await clickOption(t => t.startsWith('Create '));

    const remove = () => [...container.querySelectorAll('button')]
      .find(b => b.textContent?.trim() === 'Remove') as HTMLButtonElement | undefined;
    expect(remove()?.disabled).toBe(true);

    await act(async () => { release({ id: 22, name: 'Slowco', status: 'active' }); await pending; });
    // Once the create has landed the widget owns the value, so its own × is the clear
    // action — and that one DOES supersede.
    expect(container.querySelector('[aria-label="Clear company"]')).not.toBeNull();
  });
});

describe('ContactForm — a save that fails', () => {
  it('reports a refused write without asking the host to re-sweep', async () => {
    // A 4xx proves nothing was written, so the host's copy of the row is still correct.
    // Calling onWriteUncertain here would make every validation error re-fetch the corpus.
    const onWriteUncertain = vi.fn();
    api.mockImplementation(async (path: string, init?: RequestInit) => {
      if (init?.method === 'PUT') throw new ApiError('API error 400: bad', 400, 'Name is required');
      if (path.startsWith('/api/crm/companies')) return { companies: [] };
      if (path === '/api/users') return { users: [] };
      if (path.includes('/fields')) return [];
      return null;
    });

    await render(contact(), onWriteUncertain);
    await submit();

    expect(onWriteUncertain).not.toHaveBeenCalled();
    expect(container.textContent).toContain('API error 400');
  });

  it('tells the host to re-sweep when the outcome is genuinely unknown', async () => {
    // A dropped connection or a 5xx can land AFTER Postgres committed, so the row on screen
    // can no longer be vouched for. This is the #77 contract the merged payload rides on.
    const onWriteUncertain = vi.fn();
    api.mockImplementation(async (path: string, init?: RequestInit) => {
      if (init?.method === 'PUT') throw new Error('Failed to fetch');
      if (path.startsWith('/api/crm/companies')) return { companies: [] };
      if (path === '/api/users') return { users: [] };
      if (path.includes('/fields')) return [];
      return null;
    });

    await render(contact(), onWriteUncertain);
    await submit();

    expect(onWriteUncertain).toHaveBeenCalledTimes(1);
  });

  it('re-enables Save after a failure, so the user can correct and retry', async () => {
    // The catch path has to fall through to setSaving(false); if it did not, the form would
    // sit disabled on "Saving..." with an error and no way to act on it.
    api.mockImplementation(async (path: string, init?: RequestInit) => {
      if (init?.method === 'PUT') throw new ApiError('API error 400: bad', 400, 'nope');
      if (path.startsWith('/api/crm/companies')) return { companies: [] };
      if (path === '/api/users') return { users: [] };
      if (path.includes('/fields')) return [];
      return null;
    });

    await render(contact());
    await submit();

    const save = [...container.querySelectorAll('button')]
      .find(b => b.textContent?.trim() === 'Update') as HTMLButtonElement | undefined;
    expect(save?.disabled).toBe(false);
  });
});
