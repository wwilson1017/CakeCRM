// @vitest-environment jsdom
//
// The change-list card (#371): every change old → new from the server's batch, refused
// rows listed as not changed, and one Undo for the whole batch.

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { api } from '../core/api/client';
import { COLLAPSED_ROWS } from './bulkItems';
import { ChangeList } from './ChangeList';
import { MessageBubble } from './MessageBubble';

vi.mock('../core/api/client', () => ({
  api: vi.fn(),
  ApiError: class ApiError extends Error { detail = 'This change list is undone, so it cannot be undone.'; },
}));
const apiMock = vi.mocked(api);

let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  apiMock.mockReset();
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

const row = (id: number, over: Record<string, unknown> = {}) => ({
  record_type: 'deal', id, field: 'stage', label: `Deal ${id}`, old: 'lead', new: 'proposal', state: 'ready', ...over,
});
const batch = (status: string, rows: unknown[], reason = '') => ({ id: 'chg_1', status, reason, rows });
const items = (testid: string) =>
  [...container.querySelectorAll(`[data-testid="${testid}"] li`)].map((li) => li.textContent ?? '');
const button = (text: string) =>
  [...container.querySelectorAll('button')].find((b) => b.textContent?.includes(text));

async function render(result: unknown, fallbackLines?: string[]) {
  if (result instanceof Error) apiMock.mockRejectedValueOnce(result);
  else apiMock.mockResolvedValueOnce(result);
  await act(async () => { root.render(<ChangeList batchId="chg_1" fallbackLines={fallbackLines} />); });
}

it('lists every change old → new and every refused row with its reason', async () => {
  await render(batch('proposed', [
    row(1),
    row(2, { field: 'value', old: 1000, new: 1500, currency: ['USD', 'CAD'] }),
    row(3, { field: 'value', old: 2000, new: 2500.5, currency: ['EUR', 'EUR'] }),
    row(4, { field: 'expected_close_date', old: '', new: '2026-11-30' }),
    row(5, { record_type: 'todo', field: 'star', label: 'Call the venue', old: false, new: true }),
    row(6, { state: 'refused', reason: 'archived deal; restore it first', old: null }),
  ], 'Quarter-end push'));
  expect(apiMock).toHaveBeenCalledWith('/api/assistant/change-batches/chg_1');
  const text = container.textContent ?? '';
  expect(text).toContain('Why: Quarter-end push');
  expect(text).toContain('5 changes');
  expect(items('change-list')).toEqual([
    'Deal 1 · stage: lead → proposal',
    'Deal 2 · value: 1,000 USD → 1,500 CAD',
    'Deal 3 · value: 2,000 EUR → 2,500.5 EUR',
    'Deal 4 · expected close date: (empty) → 2026-11-30',
    'Call the venue · star: no → yes',
    'Deal 6 · stage: archived deal; restore it first',
  ]);
  expect(text).toContain('Will not be changed (1)');
  expect(items('change-list-refused')).toEqual(['Deal 6 · stage: archived deal; restore it first']);
  // Money is shown per row in its own currency and never added up.
  expect(text).not.toMatch(/total/i);
  expect(text).not.toContain('4,000');
  expect(button('Undo')).toBeUndefined(); // nothing is applied yet
});

it('collapses a 50-row list and shows all on request', async () => {
  await render(batch('proposed', Array.from({ length: 50 }, (_, i) => row(i + 1))));
  expect(items('change-list')).toHaveLength(COLLAPSED_ROWS);
  act(() => button('Show all 50')!.click());
  expect(items('change-list')).toHaveLength(50);
});

it('undoes an applied batch once and then shows it as undone', async () => {
  await render(batch('applied', [
    row(1, { state: 'applied' }), row(2, { state: 'applied' }),
    row(3, { state: 'skipped', reason: 'changed since the card was shown' }),
  ]));
  expect(container.textContent).toContain('Applied 2 changes');
  expect(container.textContent).toContain('Not changed (1)');
  apiMock.mockResolvedValueOnce(batch('undone', [
    row(1, { state: 'undone' }),
    row(2, { state: 'kept', reason: 'changed since the card was shown' }),
    row(3, { state: 'skipped', reason: 'changed since the card was shown' }),
  ]));
  await act(async () => { button('Undo')!.click(); });
  expect(apiMock).toHaveBeenLastCalledWith('/api/assistant/change-batches/chg_1/undo', { method: 'POST' });
  expect(container.textContent).toContain('Undone: 1 restored, 1 left as edited since');
  expect(container.textContent).toContain('(not restored: changed since the card was shown)');
  expect(button('Undo')).toBeUndefined();
});

it('reloads as undone with no Undo button, and offers none when nothing was applied', async () => {
  await render(batch('undone', [row(1, { state: 'undone' })]));
  expect(container.textContent).toContain('Undone: 1 restored');
  expect(button('Undo')).toBeUndefined();
  await render(batch('applied', [row(1, { state: 'skipped', reason: 'changed since the card was shown' })]));
  expect(button('Undo')).toBeUndefined();
});

it('falls back to the summary lines when the batch cannot be read', async () => {
  await render(new Error('offline'), ['deal 9 stage: archived deal; restore it first']);
  expect(container.querySelector('[data-testid="change-list"]')).toBeNull();
  expect(container.textContent).toContain('deal 9 stage: archived deal; restore it first');
});


it('cuts long values at 120 characters while keeping full text in a title', async () => {
  const old = 'Before '.repeat(30);
  const next = 'After '.repeat(40);
  await render(batch('proposed', [row(1, { field: 'notes', old, new: next })]));
  const before = container.querySelector<HTMLElement>('[title="' + old + '"]')!;
  const after = container.querySelector<HTMLElement>('[title="' + next + '"]')!;
  expect(before.textContent).toBe(old.slice(0, 119) + '…');
  expect(after.textContent).toBe(next.slice(0, 119) + '…');
  expect(before.textContent).toHaveLength(120);
});

it('renders tag lists as whole replacements and malformed row labels', async () => {
  await render(batch('proposed', [
    row(1, { record_type: 'todo', field: 'tags', old: ['first', 'second'], new: ['third'] }),
    row(2, { record_type: 'row', label: 'row 2', field: '', state: 'refused',
      reason: 'not a {record_type, id, field, value} row' }),
  ]));
  expect(items('change-list')).toEqual([
    'Deal 1 · tags: first,second → third',
    'row 2: not a {record_type, id, field, value} row',
  ]);
});

it('keeps Approve disabled during loading and failure, then enables it after Retry loads the list', async () => {
  let rejectLoad: (error: Error) => void = () => {};
  apiMock.mockReturnValueOnce(new Promise((_, reject) => { rejectLoad = reject; }));
  const onApprove = vi.fn();
  const onDeny = vi.fn();
  await act(async () => root.render(<MessageBubble
    message={{ id: 'm1', role: 'assistant', content: '', pendingConfirmations: [{
      tool: 'crm_apply_change_list', toolUseId: 't1', args: {}, status: 'pending', changeBatchId: 'chg_1',
    }] }} onApprove={onApprove} onDeny={onDeny}
  />));
  expect(container.textContent).toContain('Loading the change list…');
  expect(button('Approve')!.disabled).toBe(true);
  expect(button('Decline')!.disabled).toBe(false);
  act(() => button('Approve')!.click());
  expect(onApprove).not.toHaveBeenCalled();
  await act(async () => rejectLoad(new Error('offline')));
  expect(container.textContent).toContain('Could not load the change list.');
  expect(button('Approve')!.disabled).toBe(true);
  expect(button('Decline')!.disabled).toBe(false);
  apiMock.mockResolvedValueOnce(batch('proposed', [row(1)]));
  await act(async () => button('Retry')!.click());
  expect(apiMock).toHaveBeenCalledTimes(2);
  expect(button('Approve')!.disabled).toBe(false);
  act(() => button('Approve')!.click());
  expect(onApprove).toHaveBeenCalledWith('m1', 't1');
});

it('does not let a previous batch response enable a different pending card', async () => {
  let finishFirst: (value: unknown) => void = () => {};
  let finishSecond: (value: unknown) => void = () => {};
  apiMock.mockReturnValueOnce(new Promise(resolve => { finishFirst = resolve; }))
    .mockReturnValueOnce(new Promise(resolve => { finishSecond = resolve; }));
  const renderCard = async (id: string) => {
    await act(async () => root.render(<MessageBubble
      message={{ id: 'm1', role: 'assistant', content: '', pendingConfirmations: [{
        tool: 'crm_apply_change_list', toolUseId: 't1', args: {}, status: 'pending', changeBatchId: id,
      }] }} onApprove={() => {}} onDeny={() => {}}
    />));
  };
  await renderCard('chg_1');
  await renderCard('chg_2');
  await act(async () => finishFirst(batch('proposed', [row(1)])));
  expect(button('Approve')!.disabled).toBe(true);
  await act(async () => finishSecond({ ...batch('proposed', [row(2)]), id: 'chg_2' }));
  expect(button('Approve')!.disabled).toBe(false);
  expect(container.textContent).toContain('Deal 2');
  expect(container.textContent).not.toContain('Deal 1');
});
