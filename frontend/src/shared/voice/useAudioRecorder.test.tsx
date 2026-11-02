// @vitest-environment jsdom
import { act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useAudioRecorder } from './useAudioRecorder';

class FakeRecorder {
  static isTypeSupported = (t: string) => t === 'audio/webm';
  state = 'inactive';
  ondataavailable?: (e: { data: Blob }) => void;
  onstop?: () => void;
  onerror?: () => void;
  start = vi.fn(() => { this.state = 'recording'; });
  stop() {
    this.state = 'inactive';
    this.ondataavailable?.({ data: new Blob(['x']) }); this.onstop?.();
  }
}
let root: Root, container: HTMLDivElement;
let value: ReturnType<typeof useAudioRecorder>;
let stopTrack: ReturnType<typeof vi.fn>;
let stream: MediaStream;
let getUserMedia: ReturnType<typeof vi.fn>;
const onClip = vi.fn(), onError = vi.fn();
function Harness() {
  const result = useAudioRecorder({ maxSeconds: 180, maxBytes: 1024, withLevel: true, onClip, onError });
  useEffect(() => { value = result; });
  return null;
}
beforeEach(() => {
  vi.clearAllMocks();
  stopTrack = vi.fn(); stream = { getTracks: () => [{ stop: stopTrack }] } as unknown as MediaStream;
  getUserMedia = vi.fn(async () => stream);
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia } });
  vi.stubGlobal('MediaRecorder', FakeRecorder);
  vi.stubGlobal('AudioContext', class {
    resume = async () => {}; close = async () => {};
    createAnalyser = () => ({ fftSize: 0, getByteTimeDomainData: (b: Uint8Array) => b.fill(128) });
    createMediaStreamSource = () => ({ connect: () => {} });
  });
  container = document.createElement('div'); root = createRoot(container);
});
afterEach(() => { act(() => root.unmount()); vi.unstubAllGlobals(); });
const render = () => act(() => root.render(<Harness />));
it('hides unsupported browsers', () => {
  vi.stubGlobal('MediaRecorder', undefined); render(); expect(value.supported).toBe(false);
});
it('requests the mic and stops with one clip and released tracks', async () => {
  render(); await act(async () => { await value.start(); });
  expect(getUserMedia).toHaveBeenCalledWith({ audio: true }); expect(value.recording).toBe(true);
  act(() => value.stop());
  expect(onClip).toHaveBeenCalledWith(expect.objectContaining({ mime: 'audio/webm', durationSeconds: 0 }), 'stopped');
  expect(onClip).toHaveBeenCalledTimes(1); expect(stopTrack).toHaveBeenCalledTimes(1);
});
it('cancels without a clip', async () => {
  render(); await act(async () => { await value.start(); }); act(() => value.cancel());
  expect(stopTrack).toHaveBeenCalled(); expect(onClip).not.toHaveBeenCalled();
});
it('reports permission denial', async () => {
  getUserMedia.mockRejectedValueOnce(Error('denied')); render(); await act(async () => { await value.start(); });
  expect(onError).toHaveBeenCalledWith('denied'); expect(value.requesting).toBe(false);
});
it('releases a stream whose permission resolves after cancellation', async () => {
  let resolve!: (s: MediaStream) => void;
  getUserMedia.mockImplementationOnce(() => new Promise<MediaStream>(r => { resolve = r; }));
  render(); let pending!: Promise<boolean>;
  act(() => { pending = value.start(); }); act(() => value.cancel());
  await act(async () => { resolve(stream); await pending; });
  expect(stopTrack).toHaveBeenCalledTimes(1); expect(onClip).not.toHaveBeenCalled(); expect(value.recording).toBe(false);
});
it('closes the level context when cancelled while permission is still pending', () => {
  const close = vi.fn(async () => {});
  vi.stubGlobal('AudioContext', class { resume = async () => {}; close = close; });
  getUserMedia.mockImplementationOnce(() => new Promise<MediaStream>(() => {}));
  render(); act(() => { void value.start(); }); act(() => value.cancel());
  expect(close).toHaveBeenCalledTimes(1);
});
it('releases tracks on unmount and drops any late stop event', async () => {
  render(); await act(async () => { await value.start(); }); act(() => root.render(null));
  expect(stopTrack).toHaveBeenCalledTimes(1); expect(onClip).not.toHaveBeenCalled();
});
