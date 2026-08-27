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
});
