// @vitest-environment jsdom
//
// Issue #239: moving a contact or company INTO archived needs a reason, which rides the same
// PUT as the status and is recorded server-side as an "Archived — <reason>" note with the
// seat's name. Pinned here for both forms, because each decides on its own when the field
// shows, when Save is refused, and when `archive_reason` is sent — and a record cannot be
// CREATED archived at all, so the create form must not offer it.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { CrmCompany, CrmContact } from '../../core/types';

const api = vi.hoisted(() => vi.fn());
vi.mock('../../core/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../core/api/client')>()),
  api,
}));
const toast = vi.hoisted(() => ({ error: vi.fn(), info: vi.fn(), success: vi.fn() }));
vi.mock('../../shared/toast', () => ({ toast }));

const { ContactForm } = await import('./ContactForm');
const { CompanyForm } = await import('./CompanyForm');
const { AuthProvider } = await import('../../core/auth/AuthContext');

const contact = (status: string): CrmContact => ({
  id: 1, name: 'Existing Person', email: '', phone: '', company: '', company_id: null,
  title: '', source: '', status, tags: '', notes: '', created_at: '', updated_at: '',
});
const company = (status: string): CrmCompany => ({
  id: 1, name: 'Acme', domain: '', industry: '', phone: '', address: '', notes: '',
  source: '', status, created_at: '', updated_at: '',
});

const FORMS = [
  {
    label: 'contact',
    path: '/api/crm/contacts/1',
    reasonId: '#contact-archive-reason',
    render: (status?: string) => (
      <ContactForm contact={status ? contact(status) : undefined} onClose={() => {}} onSaved={() => {}} />
    ),
  },
  {
    label: 'company',
    path: '/api/crm/companies/1',
    reasonId: '#company-archive-reason',
    render: (status?: string) => (
      <CompanyForm company={status ? company(status) : undefined} onClose={() => {}} onSaved={() => {}} />
    ),
  },
];

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  api.mockClear();
  api.mockImplementation(async (path: string) => {
    if (path === '/api/users') return { users: [] };
    if (path.includes('/fields')) return [];
    if (path.startsWith('/api/crm/companies')) return { ...company('active'), companies: [] };
    return contact('active');
  });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

async function mount(el: React.ReactElement) {
  await act(async () => { root.render(<AuthProvider>{el}</AuthProvider>); });
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
}

function statusSelect(): HTMLSelectElement {
  const label = [...container.querySelectorAll('label')].find(l => l.textContent?.trim() === 'Status');
  const select = label?.parentElement?.querySelector('select');
  if (!select) throw new Error('no Status select rendered');
  return select;
}

/** Set a controlled field the way React can see (a bare `.value =` is invisible to it). */
async function setValue(el: HTMLSelectElement | HTMLTextAreaElement, value: string) {
  const proto = el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLTextAreaElement.prototype;
  await act(async () => {
    Object.getOwnPropertyDescriptor(proto, 'value')?.set?.call(el, value);
    el.dispatchEvent(new Event(el instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }));
  });
}

function submit() {
  return act(async () => {
    container.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  });
}

function putBody(path: string): Record<string, unknown> | null {
  const call = api.mock.calls.find(([p, i]) => p === path && i?.method === 'PUT');
  return call ? (JSON.parse(call[1].body as string) as Record<string, unknown>) : null;
}

describe.each(FORMS)('the $label form', form => {
  it('offers no Archived option on create — the server refuses a record born archived', async () => {
    await mount(form.render());
    expect([...statusSelect().options].map(o => o.value)).not.toContain('archived');
  });

  it('asks why when a live record is archived, and refuses to save without an answer', async () => {
    await mount(form.render('active'));
    expect(container.querySelector(form.reasonId)).toBeNull();
    await setValue(statusSelect(), 'archived');
    const reason = container.querySelector(form.reasonId) as HTMLTextAreaElement | null;
    expect(reason).toBeTruthy();

    await submit();
    expect(container.textContent).toContain('A reason is required to archive');
    expect(putBody(form.path)).toBeNull();

    await setValue(reason!, '  went dark  ');
    await submit();
    expect(putBody(form.path)).toMatchObject({ status: 'archived', archive_reason: 'went dark' });
  });

  it('sends no reason when the record was already archived or is moved back out', async () => {
    await mount(form.render('archived'));
    expect(container.querySelector(form.reasonId)).toBeNull();
    await setValue(statusSelect(), 'active');
    await submit();
    const body = putBody(form.path);
    expect(body).toMatchObject({ status: 'active' });
    expect(body).not.toHaveProperty('archive_reason');
  });
});
