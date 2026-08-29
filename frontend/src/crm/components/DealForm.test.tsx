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

import type { CrmDeal } from '../../core/types';

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
