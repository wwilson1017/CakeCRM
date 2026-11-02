// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../../core/api/client';
import { toast } from '../../shared/toast';
import { VoiceLivePreviewSetterContext } from '../../shared/voice/VoiceReadyContext';
import { VoiceLiveCard } from './VoiceLiveCard';
vi.mock('../../core/api/client', () => ({ api: vi.fn() }));
vi.mock('../../shared/toast', () => ({ toast: { error: vi.fn() } }));
let root: Root, container: HTMLDivElement;
const setLive = vi.fn();
beforeEach(() => {
  vi.resetAllMocks(); container = document.createElement('div'); root = createRoot(container);
});
afterEach(() => act(() => root.unmount()));
async function render(ready = true, enabled = false) {
  vi.mocked(api).mockResolvedValueOnce({ voice_ready: ready, enabled });
  await act(async () => root.render(<VoiceLivePreviewSetterContext.Provider value={setLive}><VoiceLiveCard isMobile={false} /></VoiceLivePreviewSetterContext.Provider>));
}
const toggle = () => container.querySelector<HTMLInputElement>('input')!;
it('has no switch without a transcriber and discloses the direct audio path', async () => {
  await render(false);
  expect(toggle()).toBeNull(); expect(container.textContent).toContain('Needs an AI provider');
  expect(container.textContent).toContain('not through this install');
  expect(container.textContent).toContain('cancelling a recording cannot take back audio');
});
it('updates the current page after successful enable and disable', async () => {
  await render(); expect(toggle().checked).toBe(false);
  for (const enabled of [true, false]) {
    vi.mocked(api).mockResolvedValueOnce({ enabled });
    await act(async () => toggle().click());
    expect(api).toHaveBeenLastCalledWith('/api/voice/live-preview', { method: 'POST', body: JSON.stringify({ enabled }) });
    expect(toggle().checked).toBe(enabled); expect(setLive).toHaveBeenLastCalledWith(enabled);
  }
});
it('retains the previous setting when saving fails', async () => {
  await render(); vi.mocked(api).mockRejectedValueOnce(Error('failed'));
  await act(async () => toggle().click());
  expect(toggle().checked).toBe(false); expect(setLive).not.toHaveBeenCalled();
  expect(toast.error).toHaveBeenCalled(); expect(toggle().disabled).toBe(false);
});
it('shows a load error without a switch', async () => {
  vi.mocked(api).mockRejectedValueOnce(Error('failed'));
  await act(async () => root.render(<VoiceLiveCard isMobile />));
  expect(toggle()).toBeNull(); expect(container.textContent).toContain('Could not load');
});

it('disables the switch while saving and waits to update the context', async () => {
  await render();
  let resolve!: (value: { enabled: boolean }) => void;
  vi.mocked(api).mockReturnValueOnce(new Promise(r => { resolve = r; }));
  act(() => toggle().click());
  expect(toggle().disabled).toBe(true); expect(setLive).not.toHaveBeenCalled();
  await act(async () => resolve({ enabled: true }));
  expect(toggle().disabled).toBe(false); expect(setLive).toHaveBeenCalledWith(true);
});
