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
