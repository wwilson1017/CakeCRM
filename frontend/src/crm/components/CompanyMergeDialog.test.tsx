// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ApiError } from '../../core/api/client';
import type { CrmCompany, CrmCompanyMergePlan } from '../../core/types';
import { CompanyMergeDialog } from './CompanyMergeDialog';

const { api, toastError } = vi.hoisted(() => ({ api: vi.fn(), toastError: vi.fn() }));
vi.mock('../../core/api/client', async importOriginal => ({
  ...await importOriginal<typeof import('../../core/api/client')>(), api,
}));
vi.mock('../../shared/toast', () => ({ toast: { error: toastError } }));

const absorbed: CrmCompany = { id: 2, name: 'Duplicate', domain: '', industry: '', phone: '',
  address: '', notes: '', source: '', status: 'active', created_at: '', updated_at: '' };
const kept = { ...absorbed, id: 1, name: 'Kept' };
const third = { ...absorbed, id: 3, name: 'Third' };
const plan: CrmCompanyMergePlan = {
  survivor: { id: 1, name: 'Kept' }, absorbed: { id: 2, name: 'Duplicate', status: 'active' },
  moves: { contacts: 3, deals: 2, archived_deals: 1, todos: 4, notes: 5, custom_fields: 2 },
  fields: [{ field: 'domain', old: '', new: 'dupe.example' }],
};
let container: HTMLDivElement;
let root: Root;
let trigger: HTMLButtonElement;
const onMerged = vi.fn();
const onCancel = vi.fn();
const onWriteUncertain = vi.fn();

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  vi.useFakeTimers();
  vi.clearAllMocks();
  api.mockImplementation(async (url: string) => url.includes('?limit=')
    ? { companies: [absorbed, kept, third] } : plan);
  trigger = document.createElement('button');
  document.body.appendChild(trigger);
  trigger.focus();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => root.render(<CompanyMergeDialog absorbed={absorbed} onMerged={onMerged}
    onCancel={onCancel} onWriteUncertain={onWriteUncertain} />));
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  trigger.remove();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function input() { return document.querySelector<HTMLInputElement>('[role="combobox"]')!; }
function mergeButton() {
  return [...document.querySelectorAll<HTMLButtonElement>('button')]
    .find(button => button.textContent === 'Merge and archive Duplicate')!;
}
async function settle() { await act(async () => { await vi.advanceTimersByTimeAsync(300); }); }
async function open() {
  await act(async () => { input().dispatchEvent(new MouseEvent('click', { bubbles: true })); });
  await settle();
}
async function pick(name = 'Kept') {
  await open();
  const option = [...document.querySelectorAll<HTMLElement>('[role="option"]')]
    .find(row => row.textContent?.includes(name));
  if (!option) throw new Error(`Missing ${name} option`);
  await act(async () => option.click());
}
async function reason(value = 'same org') {
  const textarea = document.querySelector('textarea')!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set?.call(textarea, value);
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
  });
}
async function clear() {
  const button = document.querySelector<HTMLButtonElement>('button[aria-label="Clear keep"]');
  if (!button) throw new Error('Missing clear button');
  await act(async () => button.click());
}

it('searches active survivors, excludes the absorbed row and offers no create', async () => {
  await open();
  expect(api).toHaveBeenCalledWith('/api/crm/companies?limit=20&status=active');
  const rows = [...document.querySelectorAll('[role="option"]')].map(row => row.textContent);
  expect(rows).toEqual(['Kept', 'Third']);
  expect(document.body.textContent).not.toContain('Create "');
});

it('shows counts and field fills; a nonblank reason is required', async () => {
  expect(mergeButton().disabled).toBe(true);
  await pick();
  expect(api).toHaveBeenCalledWith('/api/crm/companies/2/merge-preview?survivor_id=1');
  for (const text of ['3 contacts', '2 deals (1 archived)', '4 open todos follow them',
    '5 notes copied', '2 custom fields filled', 'Fills domain from the duplicate',
    'Current estimate; recomputed at merge.']) expect(document.body.textContent).toContain(text);
  expect(mergeButton().disabled).toBe(true);
  await reason('  ');
  expect(mergeButton().disabled).toBe(true);
  await reason();
  expect(mergeButton().disabled).toBe(false);
});

it('submits once and disables the inputs while pending', async () => {
  await pick();
  await reason();
  let finish: (value: unknown) => void = () => {};
  api.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  await act(async () => { mergeButton().click(); mergeButton().click(); });
  expect(api.mock.calls.filter(([, options]) => options?.method === 'POST')).toEqual([
    ['/api/crm/companies/2/merge', { method: 'POST', body: JSON.stringify({ survivor_id: 1, reason: 'same org' }) }],
  ]);
  expect(input().matches(':disabled')).toBe(true);
  expect(document.querySelector('textarea')!.disabled).toBe(true);
  expect(onMerged).not.toHaveBeenCalled();
  await act(async () => finish(plan));
  expect(onMerged).toHaveBeenCalledExactlyOnceWith(1);
});

it('ignores stale previews when changing or clearing the survivor', async () => {
  let finish: (value: CrmCompanyMergePlan) => void = () => {};
  api.mockImplementation(async (url: string) => {
    if (url.includes('?limit=')) return { companies: [kept, third] };
    if (url.endsWith('survivor_id=1')) return new Promise<CrmCompanyMergePlan>(resolve => { finish = resolve; });
    return { ...plan, survivor: { id: 3, name: 'Third' }, moves: { ...plan.moves, contacts: 7 } };
  });
  await pick();
  await pick('Third');
  await reason();
  expect(document.body.textContent).toContain('7 contacts');
  await act(async () => finish(plan));
  expect(document.body.textContent).not.toContain('3 contacts');
  expect(mergeButton().disabled).toBe(false);
  await clear();
  expect(mergeButton().disabled).toBe(true);
  expect(document.body.textContent).not.toContain('7 contacts');
});

it('does not restore a preview that resolves after clearing', async () => {
  let finish: (value: CrmCompanyMergePlan) => void = () => {};
  api.mockImplementation(async (url: string) => url.includes('?limit=') ? { companies: [kept] }
    : new Promise<CrmCompanyMergePlan>(resolve => { finish = resolve; }));
  await pick();
  await clear();
  await reason();
  await act(async () => finish(plan));
  expect(mergeButton().disabled).toBe(true);
  expect(document.body.textContent).not.toContain('3 contacts');
});

it('shows preview refusals inline and leaves merge disabled', async () => {
  api.mockImplementation(async (url: string) => {
    if (url.includes('?limit=')) return { companies: [kept] };
    throw new ApiError('refused', 400, 'Company is archived');
  });
  await pick();
  await reason();
  expect(document.querySelector('[role="alert"]')?.textContent).toBe('Company is archived');
  expect(mergeButton().disabled).toBe(true);
});

it('allows retry after a definite refusal', async () => {
  await pick();
  await reason();
  api.mockRejectedValueOnce(new ApiError('refused', 400, 'Cannot merge'));
  await act(async () => mergeButton().click());
  expect(toastError).toHaveBeenCalledWith('Cannot merge');
  expect(mergeButton().disabled).toBe(false);
  expect(onWriteUncertain).not.toHaveBeenCalled();
  await act(async () => mergeButton().click());
  expect(onMerged).toHaveBeenCalledExactlyOnceWith(1);
});

it('refreshes before closing on an uncertain write', async () => {
  await pick();
  await reason();
  let refreshed: () => void = () => {};
  onWriteUncertain.mockImplementationOnce(() => new Promise<void>(resolve => { refreshed = resolve; }));
  api.mockRejectedValueOnce(new TypeError('Network lost'));
  await act(async () => mergeButton().click());
  expect(onWriteUncertain).toHaveBeenCalledOnce();
  expect(onCancel).not.toHaveBeenCalled();
  expect(mergeButton().disabled).toBe(true);
  await act(async () => refreshed());
  expect(onCancel).toHaveBeenCalledOnce();
  expect(onMerged).not.toHaveBeenCalled();
});

it('focuses the picker, contains Tab, handles Escape and restores focus', async () => {
  expect(document.activeElement).toBe(input());
  await pick();
  await reason();
  mergeButton().focus();
  const tab = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
  document.dispatchEvent(tab);
  expect(tab.defaultPrevented).toBe(true);
  expect(document.activeElement).toBe(input());
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  expect(onCancel).toHaveBeenCalledOnce();
  act(() => root.render(null));
  expect(document.activeElement).toBe(trigger);
});
