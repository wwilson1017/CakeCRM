// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { MessageBubble } from './MessageBubble';
import type { ChatMessage } from './types';
vi.mock('./MarkdownContent', () => ({ MarkdownContent: ({ content }: { content: string }) => <span>{content}</span> }));
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
