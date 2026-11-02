// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { VisionReadyContext } from '../shared/VisionReadyContext';
import AssistantPanelBody from './AssistantPanelBody';

const mocks = vi.hoisted(() => ({ sendMessage: vi.fn(), load: vi.fn(), error: vi.fn() }));
vi.mock('./useAssistantChat', () => ({ useAssistantChat: () => ({
  messages: [], isStreaming: false, toolMode: 'normal', contextUsage: null,
  sendMessage: mocks.sendMessage,
}) }));
vi.mock('./useConversations', () => ({ useConversations: () => ({ conversations: [], load: mocks.load }) }));
vi.mock('./useReadAloud', () => ({ useReadAloud: () => null }));
vi.mock('../shared/voice/VoiceMicButton', () => ({ VoiceMicButton: () => null }));
vi.mock('../shared/toast', () => ({ toast: { error: mocks.error } }));

let root: Root;
let container: HTMLDivElement;
beforeEach(() => {
  vi.clearAllMocks();
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
  container = document.createElement('div');
  root = createRoot(container);
});
afterEach(() => { act(() => root.unmount()); vi.restoreAllMocks(); });

function render(ready: boolean | null) {
  act(() => root.render(<VisionReadyContext.Provider value={ready}><AssistantPanelBody /></VisionReadyContext.Provider>));
}
function stage(input: HTMLInputElement, files: File[]) {
  Object.defineProperty(input, 'files', { configurable: true, value: files });
  act(() => input.dispatchEvent(new Event('change', { bubbles: true })));
}

it('offers supported photo types and stages camera files synchronously', () => {
  render(true);
  const attach = container.querySelector<HTMLInputElement>('input[multiple]')!;
  for (const ext of ['.jpg', '.jpeg', '.png', '.webp']) expect(attach.accept).toContain(ext);
  expect(attach.accept).not.toContain('.gif');
  const camera = container.querySelector<HTMLInputElement>('[data-testid="assistant-camera-input"]')!;
  expect(camera.getAttribute('capture')).toBe('environment');
  const click = vi.spyOn(camera, 'click');
  act(() => container.querySelector<HTMLButtonElement>('[title="Take photo"]')!.click());
  expect(click).toHaveBeenCalledOnce();
  vi.spyOn(Date, 'now').mockReturnValue(1234);
  stage(camera, [new File(['camera'], 'capture.jpg', { type: 'image/jpeg' })]);
  expect(camera.value).toBe('');
  expect(container.textContent).toContain('photo-1234.jpg');
  act(() => container.querySelector<HTMLButtonElement>('[title="Send"]')!.click());
  const [text, files] = mocks.sendMessage.mock.calls[0];
  expect(text).toBe('');
  expect(files[0].name).toBe('photo-1234.jpg');
});

it('passes original staged photos to sendMessage and enforces the raw 10 MB pre-check', () => {
  render(true);
  const file = new File(['photo'], 'card.png', { type: 'image/png' });
  const big = new File(['big'], 'big.png', { type: 'image/png' });
  Object.defineProperty(big, 'size', { value: 10 * 1024 * 1024 + 1 });
  stage(container.querySelector<HTMLInputElement>('input[multiple]')!, [file, big]);
  expect(container.textContent).toContain('card.png');
  expect(mocks.error).toHaveBeenCalledWith('big.png: exceeds 10 MB.');
  act(() => container.querySelector<HTMLButtonElement>('[title="Send"]')!.click());
  expect(mocks.sendMessage).toHaveBeenCalledWith('', [file]);
});

it.each([false, null])('hides photo controls and refuses photos when readiness is %s', ready => {
  render(ready);
  expect(container.querySelector('[title="Take photo"]')).toBeNull();
  const input = container.querySelector<HTMLInputElement>('input[multiple]')!;
  for (const ext of ['.jpg', '.jpeg', '.png', '.webp']) expect(input.accept).not.toContain(ext);
  stage(input, [new File(['photo'], 'card.png', { type: 'image/png' })]);
  expect(mocks.error).toHaveBeenCalledWith("card.png: this model can't see photos.");
  expect(container.textContent).not.toContain('card.png');
  expect(container.querySelector<HTMLButtonElement>('[title="Send"]')!.disabled).toBe(true);
});
