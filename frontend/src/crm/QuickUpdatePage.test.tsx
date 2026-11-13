// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CrmDeal } from '../core/types';
import type { QuickUpdatePreview } from './quickUpdate';

const apiMock = vi.fn();
vi.mock('../core/api/client', () => ({
  api: (...args: unknown[]) => apiMock(...args),
  ApiError: class extends Error {
    status: number; detail: string;
    constructor(message: string, status: number, detail: string) { super(message); this.status = status; this.detail = detail; }
  },
}));
vi.mock('./useUsers', () => ({ useUsers: () => ({ nameFor: () => 'Ada' }) }));
vi.mock('../shared/toast', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));
const { QuickUpdatePage } = await import('./QuickUpdatePage');
const { ApiError } = await import('../core/api/client');
const { toast } = await import('../shared/toast');
const DEAL: CrmDeal = { id: 5, title: 'Canopy Foods', stage: 'qualified', value: 1000, currency: 'USD', company_name: 'Canopy', contact_id: 9, owner_id: 3, company_id: null, notes: '', expected_close_date: '', probability: 50, created_at: '', updated_at: '' };
const PREVIEW: QuickUpdatePreview = { deal: DEAL, body: { value: 2500, stage: 'proposal', note: 'sent samples' }, changes: ['Value → $2,500', 'Move to Proposal', 'Note: sent samples'], needs: [], warnings: [] };
let container: HTMLDivElement;
let root: Root;
async function settle() { for (let i = 0; i < 10; i++) await act(async () => { await Promise.resolve(); }); }
function button(text: string, scope: ParentNode = document): HTMLButtonElement {
  const el = [...scope.querySelectorAll('button')].find(b => b.textContent?.trim() === text);
  if (!el) throw new Error(`no button ${text}`);
  return el;
}
async function click(text: string, scope?: ParentNode) { await act(async () => button(text, scope).click()); await settle(); }
async function type(selector: string, value: string) {
  const el = document.querySelector(selector) as HTMLInputElement | HTMLTextAreaElement;
  if (!el) throw new Error(`no input ${selector}`);
  await act(async () => {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
}
async function pick() {
  const row = [...container.querySelectorAll('li button')].find(b => b.textContent?.includes('Canopy Foods')) as HTMLButtonElement;
  await act(async () => row.click()); await settle();
}
async function mount({ aiReady = false, deals = [DEAL], onWrite = vi.fn(), onPreview = vi.fn().mockResolvedValue(PREVIEW) } = {}) {
  apiMock.mockImplementation(async (url: string, init?: { body?: string }) => {
    if (url === '/api/setup/status') return { ai_ready: aiReady };
    if (url.startsWith('/api/crm/quick-update/deals?q=')) return { deals };
    const body = JSON.parse(init?.body ?? '{}');
    if (url.endsWith('/preview')) return onPreview(body);
    if (url === '/api/crm/quick-update/deals/5') { await onWrite(body); return { deal_id: 5, saved: [] }; }
    throw new Error(`unexpected ${url}`);
  });
  await act(async () => root.render(<QuickUpdatePage />)); await settle();
  return { onWrite, onPreview };
}
async function ask() { await type('#quick-update-baker', 'sent samples, move to Proposal, value 2500'); await click('Ask'); }

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  vi.stubGlobal('matchMedia', (query: string) => ({ matches: false, media: query, onchange: null, addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false }));
  container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
  apiMock.mockReset(); vi.mocked(toast.success).mockClear();
});
afterEach(() => { act(() => root.unmount()); container.remove(); vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('QuickUpdatePage', () => {
  it('uses server order with closed deals pickable and picking shows the card', async () => {
    await mount({ deals: [DEAL, { ...DEAL, id: 6, title: 'Closed Canopy', stage: 'lost' }] });
    expect([...container.querySelectorAll('li strong')].map(e => e.textContent)).toEqual(['Canopy Foods', 'Closed Canopy · LOST']);
    await pick(); expect(container.textContent).toContain('owner Ada');
    expect(container.querySelector('[aria-label="Selected deal"]')?.textContent).toContain('$1,000');
  });
  it('debounces search and passes the typed query', async () => {
    await mount(); vi.useFakeTimers();
    await type('input[aria-label="Search a lead, company or contact"]', 'Canopy');
    expect(apiMock.mock.calls.filter(c => String(c[0]).includes('?q=Canopy'))).toHaveLength(0);
    await act(async () => { vi.advanceTimersByTime(250); }); await settle();
    expect(apiMock.mock.calls.filter(c => String(c[0]).includes('?q=Canopy'))).toHaveLength(1);
  });
  it('note + value posts exactly the fields and returns to refreshed search with a toast', async () => {
    const deals = [{ ...DEAL }];
    const onWrite = vi.fn().mockImplementation(() => { deals[0] = { ...DEAL, value: 2500 }; });
    await mount({ deals, onWrite }); await pick();
    await click('Note'); await type('textarea[aria-label="Note"]', 'sent samples');
    await click('Value'); await type('input[aria-label="Value"]', '2500'); await click('Save');
    expect(onWrite).toHaveBeenCalledExactlyOnceWith({ note: 'sent samples', value: 2500 });
    expect(toast.success).toHaveBeenCalledWith('Saved to Canopy Foods.');
    expect(container.querySelector('input[aria-label="Search a lead, company or contact"]')).not.toBeNull();
    expect(apiMock.mock.calls.filter(c => String(c[0]).includes('/deals?q='))).toHaveLength(2);
    await pick(); expect(container.querySelector('[aria-label="Selected deal"]')?.textContent).toContain('$2,500');
  });
  it('a stage chip posts stage', async () => {
    const { onWrite } = await mount(); await pick(); await click('Stage'); await click('Proposal'); await click('Save');
    expect(onWrite).toHaveBeenCalledExactlyOnceWith({ stage: 'proposal' });
  });
  it('Save, Won and Lost stay disabled for blank or garbage value and zero is valid', async () => {
    await mount(); await pick(); await click('Value');
    for (const value of ['', 'abc']) {
      await type('input[aria-label="Value"]', value);
      for (const text of ['Save', 'Mark Won', 'Mark Lost']) expect(button(text).disabled).toBe(true);
    }
    await type('input[aria-label="Value"]', '0');
    for (const text of ['Save', 'Mark Won', 'Mark Lost']) expect(button(text).disabled).toBe(false);
  });
  it('Mark Won asks the day and drops the open stage', async () => {
    const { onWrite } = await mount(); await pick(); await click('Stage'); await click('Proposal'); await click('Mark Won');
    expect(document.querySelector('#crm-closed-on-input')).not.toBeNull();
    await type('#crm-closed-on-input', '2026-10-01'); await click('Confirm');
    expect(onWrite).toHaveBeenCalledExactlyOnceWith({ won: true, closed_on: '2026-10-01' });
  });
  it.each(['price', ''])('Mark Lost asks for an optional reason: %s', async reason => {
    const { onWrite } = await mount(); await pick(); await click('Mark Lost');
    await type('#crm-lost-reason-input', reason); await click('Mark Lost', document.querySelector('[role="dialog"]')!);
    expect(onWrite).toHaveBeenCalledExactlyOnceWith({ lost: true, lost_reason: reason });
  });
  it('a follow-up posts followup', async () => {
    const { onWrite } = await mount(); await pick(); await click('Follow-up'); await type('input[aria-label="Follow-up"]', 'Call Ada'); await click('Save');
    expect(onWrite).toHaveBeenCalledExactlyOnceWith({ followup: 'Call Ada' });
  });
  it.each([new ApiError('refused', 400, 'That deal is archived'), new ApiError('offline', 500, 'db down'), new Error('network')])('a failed save keeps tiles and explains the outcome: %s', async error => {
    await mount({ onWrite: vi.fn().mockRejectedValue(error) }); await pick(); await click('Note'); await type('textarea[aria-label="Note"]', 'called'); await click('Save');
    expect(container.querySelector('[role="alert"]')?.textContent).toBe(error instanceof ApiError && error.status === 400 ? 'Not saved: That deal is archived' : "Couldn't confirm the save — open the deal and check before trying again.");
    expect(button('Note').getAttribute('aria-pressed')).toBe('true');
    expect((container.querySelector('textarea') as HTMLTextAreaElement).value).toBe('called');
  });
  it.each([false, true])('shows Baker only when ai_ready is %s', async aiReady => {
    await mount({ aiReady }); await pick(); expect(!!container.querySelector('#quick-update-baker')).toBe(aiReady);
  });
  it('typing calls nothing and one Ask is one preview call', async () => {
    const { onPreview } = await mount({ aiReady: true }); await pick(); await type('#quick-update-baker', 'called');
    expect(onPreview).not.toHaveBeenCalled(); await click('Ask'); expect(onPreview).toHaveBeenCalledExactlyOnceWith({ sentence: 'called' });
  });
  it('preview lists every change and Confirm posts exactly its body', async () => {
    const { onWrite } = await mount({ aiReady: true }); await pick(); await ask();
    for (const line of PREVIEW.changes) expect(container.querySelector('[aria-label="Baker\'s preview"]')?.textContent).toContain(line);
    await click('Confirm'); expect(onWrite).toHaveBeenCalledExactlyOnceWith(PREVIEW.body);
  });
  it('preview requires a note before Confirm then merges the trimmed line', async () => {
    const { onWrite } = await mount({ aiReady: true, onPreview: vi.fn().mockResolvedValue({ ...PREVIEW, body: { value: 2500 }, needs: ['note'] }) });
    await pick(); await ask(); expect(button('Confirm').disabled).toBe(true);
    await type('input[aria-label="What happened? One line for the note"]', ' Called Ada about pricing '); await click('Confirm');
    expect(onWrite).toHaveBeenCalledExactlyOnceWith({ value: 2500, note: 'Called Ada about pricing' });
  });
  it('Cancel drops the preview without writing', async () => {
    const { onWrite } = await mount({ aiReady: true }); await pick(); await ask(); await click('Cancel');
    expect(container.querySelector('#quick-update-baker')).not.toBeNull(); expect(onWrite).not.toHaveBeenCalled();
  });
  it('locks tiles while a preview is open', async () => {
    await mount({ aiReady: true }); await pick(); await ask();
    expect(container.querySelector('fieldset')?.disabled).toBe(true);
    expect(button('Mark Won').disabled).toBe(true); expect(button('Mark Lost').disabled).toBe(true);
  });
  it('shows a refused preview sentence under the bar', async () => {
    const text = 'Baker is unavailable right now — use the buttons above';
    await mount({ aiReady: true, onPreview: vi.fn().mockRejectedValue(new ApiError(text, 503, text)) }); await pick(); await ask();
    expect(container.querySelector('[role="alert"]')?.textContent).toBe(text);
  });
  it('ignores a late preview after leaving and refetches search', async () => {
    let resolve!: (value: QuickUpdatePreview) => void;
    await mount({ aiReady: true, onPreview: vi.fn().mockReturnValue(new Promise<QuickUpdatePreview>(r => { resolve = r; })) });
    await pick(); await ask(); await click('← pick a different deal'); await pick();
    await act(async () => resolve(PREVIEW)); await settle();
    expect(container.querySelector('[aria-label="Baker\'s preview"]')).toBeNull();
    expect(apiMock.mock.calls.filter(c => String(c[0]).includes('/deals?q='))).toHaveLength(2);
  });
  it('sends only one save while a request is pending', async () => {
    let resolve!: () => void;
    const onWrite = vi.fn().mockReturnValue(new Promise<void>(r => { resolve = r; }));
    await mount({ onWrite }); await pick(); await click('Note'); await type('textarea[aria-label="Note"]', 'called');
    const save = button('Save');
    await act(async () => { save.click(); save.click(); });
    expect(onWrite).toHaveBeenCalledExactlyOnceWith({ note: 'called' });
    expect(button('Mark Won').disabled).toBe(true);
    await act(async () => resolve()); await settle();
  });
  it('ignores a pending preview when a manual save completes', async () => {
    let resolve!: (value: QuickUpdatePreview) => void;
    const { onWrite } = await mount({ aiReady: true, onPreview: vi.fn().mockReturnValue(new Promise<QuickUpdatePreview>(r => { resolve = r; })) });
    await pick(); await ask(); await click('Note'); await type('textarea[aria-label="Note"]', 'called'); await click('Save');
    await act(async () => resolve(PREVIEW)); await settle();
    expect(onWrite).toHaveBeenCalledExactlyOnceWith({ note: 'called' });
    expect(container.querySelector('[aria-label="Baker\'s preview"]')).toBeNull();
    expect(container.querySelector('input[aria-label="Search a lead, company or contact"]')).not.toBeNull();
  });
  it('never confirms a preview for another deal', async () => {
    const { onWrite } = await mount({ aiReady: true, onPreview: vi.fn().mockResolvedValue({ ...PREVIEW, deal: { ...DEAL, id: 6 } }) });
    await pick(); await ask(); await click('Confirm'); expect(onWrite).not.toHaveBeenCalled();
  });
});
