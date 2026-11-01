// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { RecordedClip } from './useAudioRecorder';
import { ApiError } from '../../core/api/client';
import { VoiceMicButton, type VoiceMicButtonProps } from './VoiceMicButton';
import { VoiceReadyContext } from './VoiceReadyContext';
import { transcribeClip } from './transcribe';
import { stopSpeech } from './speechPlayer';
import { CANCEL_DX } from './dictationGesture';
const recorder = vi.hoisted(() => ({
  supported: true, recording: false, requesting: false, seconds: 0, level: 0, error: null,
  start: vi.fn(async () => { recorder.recording = true; return true; }), stop: vi.fn(), cancel: vi.fn(),
  onClip: null as null | ((clip: RecordedClip) => void),
}));
vi.mock('./useAudioRecorder', () => ({ useAudioRecorder: (o: { onClip: (clip: RecordedClip) => void }) => { recorder.onClip = o.onClip; return recorder; } }));
vi.mock('./transcribe', async importOriginal => ({ ...await importOriginal<typeof import('./transcribe')>(), transcribeClip: vi.fn() }));
vi.mock('./speechPlayer', () => ({ stopSpeech: vi.fn() }));
let root: Root, container: HTMLDivElement;
const transcript = vi.fn();
beforeEach(() => {
  vi.clearAllMocks(); recorder.supported = true; recorder.recording = false; recorder.requesting = false;
  HTMLElement.prototype.setPointerCapture = vi.fn();
  vi.mocked(transcribeClip).mockResolvedValue({ text: 'hello', tidied: false });
  container = document.createElement('div'); root = createRoot(container);
});
afterEach(() => act(() => root.unmount()));
function render(ready: boolean | null = true, props: Partial<VoiceMicButtonProps> = {}) {
  act(() => root.render(<VoiceReadyContext.Provider value={ready}><VoiceMicButton mode="send" onTranscript={transcript} {...props} /></VoiceReadyContext.Provider>));
}
const mic = () => container.querySelector<HTMLButtonElement>('[aria-label^="Voice input"]')!;
function pointer(type: string, t: number, x = 100, y = 100) {
  const e = new MouseEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0 });
  Object.defineProperty(e, 'timeStamp', { value: 1000 + t });
  act(() => mic().dispatchEvent(e));
}
async function clip() {
  await act(async () => { recorder.onClip?.({ blob: new Blob(['audio']), mime: 'audio/webm', durationSeconds: 2 }); });
}
it.each([false, null])('hides when readiness is %s', ready => { render(ready); expect(mic()).toBeNull(); });
it('hides when recording is unsupported or disabled', () => {
  recorder.supported = false; render(); expect(mic()).toBeNull();
  recorder.supported = true; render(true, { disabled: true }); expect(mic()).toBeNull();
});
it('holds, releases and sends, stopping speech first', async () => {
  render(); pointer('pointerdown', 0); pointer('pointerup', 1500); await clip();
  expect(recorder.stop).toHaveBeenCalledTimes(1); expect(stopSpeech).toHaveBeenCalledTimes(1);
  expect(transcript).toHaveBeenCalledWith('hello', 'send');
});
it('slides left to cancel without upload', async () => {
  render(); pointer('pointerdown', 0); pointer('pointermove', 1500, 100 - CANCEL_DX); await clip();
  expect(recorder.cancel).toHaveBeenCalled(); expect(transcribeClip).not.toHaveBeenCalled();
});
it('locks on tap and stops into review', async () => {
  render(); pointer('pointerdown', 0); pointer('pointerup', 100);
  const stop = container.querySelector<HTMLButtonElement>('[aria-label="Stop recording and review the text"]')!;
  expect(stop).not.toBeNull(); act(() => stop.click()); await clip();
  expect(transcript).toHaveBeenCalledWith('hello', 'review');
});
it('treats keyboard and assistive clicks as hands-free', () => {
  render(); act(() => mic().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })));
  expect(container.querySelector('[aria-label="Stop recording and review the text"]')).not.toBeNull();
  act(() => container.querySelector<HTMLButtonElement>('[aria-label="Cancel recording"]')!.click());
  act(() => mic().click()); expect(recorder.start).toHaveBeenCalledTimes(2);
});
it('does not send silence', async () => {
  vi.mocked(transcribeClip).mockResolvedValue({ text: '  ', tidied: false });
  render(); pointer('pointerdown', 0); pointer('pointerup', 1500); await clip();
  expect(transcript).not.toHaveBeenCalled(); expect(container.textContent).toContain("Didn't catch that");
});
it('offers discard without retry on 413', async () => {
  vi.mocked(transcribeClip).mockRejectedValue(new ApiError('too large', 413, ''));
  render(); pointer('pointerdown', 0); pointer('pointerup', 1500); await clip();
  expect(container.textContent).toContain('Discard'); expect(container.textContent).not.toContain('Retry');
});
it('retries transient failures with the original intent', async () => {
  vi.mocked(transcribeClip).mockRejectedValueOnce(Error('network'));
  render(); pointer('pointerdown', 0); pointer('pointerup', 1500); await clip();
  await act(async () => { [...container.querySelectorAll('button')].find(b => b.textContent === 'Retry')!.click(); });
  expect(transcript).toHaveBeenCalledWith('hello', 'send'); expect(transcribeClip).toHaveBeenCalledTimes(2);
});
it('always reviews chatter and requests tidy', async () => {
  render(true, { mode: 'review', tidy: true }); pointer('pointerdown', 0); pointer('pointerup', 1500); await clip();
  expect(transcript).toHaveBeenCalledWith('hello', 'review');
  expect(transcribeClip).toHaveBeenCalledWith(expect.any(Blob), 'audio/webm', true, expect.any(AbortSignal));
});
it('cancels recording on unmount and ignores a late clip', async () => {
  render(); pointer('pointerdown', 0); act(() => root.render(null)); await clip();
  expect(recorder.cancel).toHaveBeenCalled(); expect(transcribeClip).not.toHaveBeenCalled();
});
it('aborts upload and ignores completion after unmount', async () => {
  let resolve!: (r: { text: string; tidied: boolean }) => void;
  vi.mocked(transcribeClip).mockReturnValue(new Promise(r => { resolve = r; }));
  render(); pointer('pointerdown', 0); pointer('pointerup', 1500); await clip();
  const signal = vi.mocked(transcribeClip).mock.calls[0][3]!;
  act(() => root.render(null));
  await act(async () => resolve({ text: 'late', tidied: false }));
  expect(signal.aborted).toBe(true); expect(transcript).not.toHaveBeenCalled();
});
