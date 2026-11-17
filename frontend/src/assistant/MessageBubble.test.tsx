// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { MessageBubble } from './MessageBubble';
import type { ChatMessage } from './types';
vi.mock('./MarkdownContent', () => ({ MarkdownContent: ({ content }: { content: string }) => <span>{content}</span> }));
vi.mock('./ChangeList', () => ({ ChangeList: ({ batchId, fallbackLines }: { batchId: string; fallbackLines?: string[] }) => <div data-testid="change-list">{batchId}|{(fallbackLines ?? []).join(';')}</div> }));
let root: Root, container: HTMLDivElement;
beforeEach(() => { container = document.createElement('div'); root = createRoot(container); });
afterEach(() => act(() => root.unmount()));
const onSpeak = vi.fn();
function render(message: ChatMessage, speaking = false) {
  act(() => root.render(<MessageBubble message={message} speaking={speaking} onSpeak={onSpeak} onApprove={() => {}} onDeny={() => {}} />));
}
it('offers read and stop controls for a completed assistant reply', () => {
  render({ id: 'a', role: 'assistant', content: 'Hello' });
  act(() => container.querySelector<HTMLButtonElement>('[aria-label="Read aloud"]')!.click());
  expect(onSpeak).toHaveBeenCalled();
  render({ id: 'a', role: 'assistant', content: 'Hello' }, true);
  expect(container.querySelector('[aria-label="Stop reading"]')?.getAttribute('aria-pressed')).toBe('true');
});
it.each([
  { id: 'a', role: 'assistant' as const, content: '' },
  { id: 'a', role: 'assistant' as const, content: 'Hello', streaming: true },
  { id: 'a', role: 'assistant' as const, content: 'Hello', error: true },
  { id: 'u', role: 'user' as const, content: 'Hello' },
])('does not offer speech for an ineligible message %j', message => {
  render(message); expect(container.querySelector('[aria-label="Read aloud"]')).toBeNull();
});

it.each(['read this', ''])('shows filename chips with user text %j', content => {
  render({ id: 'u', role: 'user', content, attachments: ['card.jpg'] });
  const chips = container.querySelectorAll('[data-testid="user-attachment"]');
  expect(chips).toHaveLength(1);
  expect(chips[0].textContent?.trim()).toBe('card.jpg');
  const bubble = chips[0].parentElement!.parentElement!;
  expect(bubble.children).toHaveLength(content ? 2 : 1);
  expect(container.querySelector('img')).toBeNull();
});


it.each([true, false])('renders the server summary only when present: %s', withSummary => {
  const summary = { headline: 'Import 2 leads', lines: ['Ana · Northwind', 'Bo · Harbor'] };
  render({ id: 'a', role: 'assistant', content: '', pendingConfirmations: [{
    tool: 'crm_execute_lead_import', toolUseId: 't1', args: { batch_id: 'b', note: 'Met them' },
    status: 'pending', summary: withSummary ? summary : undefined,
  }] });
  const card = container.querySelector('[data-testid="confirm-summary"]');
  if (withSummary) {
    expect(card?.textContent).toContain(summary.headline);
    expect([...card!.querySelectorAll('li')].map(li => li.textContent)).toEqual(summary.lines);
  } else {
    expect(card).toBeNull();
  }
  expect(container.textContent).toContain('batch_id: b');
  expect(container.textContent).toContain('note: Met them');
});


it('a change-list card shows the staged batch instead of the argument blob', () => {
  const changes = [{ record_type: 'deal', id: 7, field: 'stage', value: 'proposal' }];
  const summary = { headline: 'Change 1 field on 1 deal; 1 row will not change', lines: ['deal 9 stage: archived'] };
  render({ id: 'a', role: 'assistant', content: '', pendingConfirmations: [{
    tool: 'crm_apply_change_list', toolUseId: 't1', args: { changes }, status: 'pending',
    summary, changeBatchId: 'chg_1',
  }] });
  expect(container.querySelector('[data-testid="change-list"]')?.textContent).toBe('chg_1|deal 9 stage: archived');
  const card = container.querySelector('[data-testid="confirm-summary"]')!;
  expect(card.textContent).toBe(summary.headline); // the lines are the list's fallback, not shown twice
  expect(container.textContent).not.toContain('record_type');
  expect(container.textContent).toContain('Approve');
});

it.each([
  ['approved', { ok: true, change_batch_id: 'chg_1', applied: 1 }, 'chg_1|'],
  ['failed', { error: 'Those records were busy, so nothing was changed. Ask Baker to send the change list again.' }, null],
  ['denied', { status: 'denied_by_user' }, null],
] as const)('a resolved change-list card (%s) shows the batch only from its result', (status, result, shown) => {
  render({ id: 'a', role: 'assistant', content: '', pendingConfirmations: [{
    tool: 'crm_apply_change_list', toolUseId: 't1', args: {}, status, result, changeBatchId: 'chg_1',
  }] });
  expect(container.querySelector('[data-testid="change-list"]')?.textContent ?? null).toBe(shown);
});

it('an applied change list reloaded as a plain tool card keeps its outcome and Undo', () => {
  render({ id: 'a', role: 'assistant', content: '', toolCalls: [
    { tool: 'crm_apply_change_list', toolUseId: 't1', status: 'done', result: { ok: true, change_batch_id: 'chg_1' } },
    { tool: 'crm_update_deal', toolUseId: 't2', status: 'done', result: { ok: true } },
  ] });
  expect([...container.querySelectorAll('[data-testid="change-list"]')].map(n => n.textContent)).toEqual(['chg_1|']);
});
