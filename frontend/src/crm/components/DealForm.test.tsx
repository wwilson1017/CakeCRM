// @vitest-environment jsdom
//
// `DealForm` lost its edit mode when the detail panel gained an inline one. Deleting the `deal?`
// prop makes the compiler catch any leftover edit CALLER, but nothing in the type system notices
// if the create path itself regressed while the branches were being unpicked — and create is now
// this component's only reason to exist. So the three behaviours that were entangled with
// `isEdit` are pinned here: the POST, the owner rule, and the contact-derived company default.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => vi.fn());
vi.mock('../../core/api/client', () => ({ api }));

const putCustomFields = vi.hoisted(() => vi.fn().mockResolvedValue(undefined));
vi.mock('./useCustomFieldsForm', () => ({
  putCustomFields,
  useCustomFieldsForm: () => ({
    editableFields: [],
    values: {},
    setValue: () => {},
    changedForSave: () => ({ 4: 'from the form' }),
  }),
}));

vi.mock('../../core/auth/AuthContext', () => ({
  useAuth: () => ({ currentUser: { id: 12, name: 'Sam', email: 's@x.test', role: 'admin', is_active: true } }),
}));

const { DealForm } = await import('./DealForm');

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  api.mockReset();
  putCustomFields.mockClear();
  api.mockImplementation((path: string) => {
    if (path.startsWith('/api/crm/contacts/')) return Promise.resolve({ id: 3, company_id: 77 });
    if (path.startsWith('/api/crm/contacts')) return Promise.resolve({ contacts: [] });
    if (path.startsWith('/api/crm/companies')) return Promise.resolve({ companies: [] });
    if (path.startsWith('/api/users')) return Promise.resolve({ users: [] });
    if (path === '/api/crm/deals') return Promise.resolve({ id: 501 });
    return Promise.resolve({});
  });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

async function settle(rounds = 4) {
  for (let i = 0; i < rounds; i++) {
    await act(async () => { await Promise.resolve(); });
  }
}

function render(props: Partial<Parameters<typeof DealForm>[0]> = {}) {
  const onSaved = vi.fn();
  act(() => root.render(<DealForm onClose={() => {}} onSaved={onSaved} {...props} />));
  return onSaved;
}

const setTitle = (value: string) => {
  const el = [...container.querySelectorAll('input')].find(i => i.type === 'text')!;
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
};
const submit = () => act(() => {
  container.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
});
const postBody = () => {
  const call = api.mock.calls.find(([p, o]) => p === '/api/crm/deals' && o?.method === 'POST');
  return call ? JSON.parse(call[1].body) : null;
};

describe('DealForm (create only)', () => {
  it('POSTs a new deal and saves its custom fields against the created id', async () => {
    const onSaved = render();
    await settle();
    setTitle('New wholesale order');
    submit();
    await settle();

    expect(postBody()).toMatchObject({ title: 'New wholesale order', stage: 'lead' });
    // The custom fields ride a SECOND request keyed on the id the POST returned — losing that
    // wiring would silently drop every custom value a new deal was created with.
    expect(putCustomFields).toHaveBeenCalledWith('deal', 501, { 4: 'from the form' }, 'Deal created');
    expect(onSaved).toHaveBeenCalled();
  });

  it('omits owner_id when the picker was never touched', async () => {
    // An untouched create lets the SERVER assign the caller, which is race-free — `currentUser`
    // can still be resolving right after login, and sending its id would be the wrong answer.
    render();
    await settle();
    setTitle('Untouched owner');
    submit();
    await settle();
    expect(postBody()).not.toHaveProperty('owner_id');
  });

  it('sends owner_id once the picker is touched, including a deliberate unassign', async () => {
    render();
    await settle();
    const select = container.querySelector('#deal-owner') as HTMLSelectElement;
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(select, '');
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });
    setTitle('Explicitly unassigned');
    submit();
    await settle();
    expect(postBody()).toMatchObject({ owner_id: null });
  });

  it('inherits the company from the contact a deal was opened against', async () => {
    // Fetched directly rather than searched in the capped 200-row list — an older linked contact
    // can fall outside that page, which would silently skip the default.
    render({ contactId: 3 });
    await settle();
    setTitle('From a contact');
    submit();
    await settle();
    expect(postBody()).toMatchObject({ contact_id: 3, company_id: 77 });
  });

  it('refuses to submit without a title', async () => {
    render();
    await settle();
    submit();
    await settle();
    expect(postBody()).toBeNull();
    expect(container.textContent).toContain('Title is required');
  });
});
