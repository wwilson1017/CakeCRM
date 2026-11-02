// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { previewSupported, useSpeechPreview, type PreviewRecognition, type PreviewResultEvent } from './useSpeechPreview';
class Recognition implements PreviewRecognition {
  static instances: Recognition[] = [];
  continuous = false; interimResults = false; lang = '';
  onresult: PreviewRecognition['onresult'] = null;
  onerror: PreviewRecognition['onerror'] = null;
  onend: PreviewRecognition['onend'] = null;
  start = vi.fn(); abort = vi.fn();
  constructor() { Recognition.instances.push(this); }
}
const result = (text: string): PreviewResultEvent => ({ results: [[{ transcript: text }], [{ transcript: 'interim' }]] });
let root: Root, container: HTMLDivElement;
function Harness({ active }: { active: boolean }) { return <span>{useSpeechPreview(active)}</span>; }
function render(active: boolean) { act(() => root.render(<Harness active={active} />)); }
beforeEach(() => {
  Recognition.instances = [];
  vi.stubGlobal('SpeechRecognition', Recognition);
  container = document.createElement('div'); root = createRoot(container);
});
afterEach(() => { act(() => root.unmount()); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
it('starts only while active, joins all results and clears on deactivate', () => {
  render(false); expect(Recognition.instances).toHaveLength(0);
  render(true); const r = Recognition.instances[0];
  expect(r.start).toHaveBeenCalledOnce(); expect(r.continuous && r.interimResults).toBe(true);
  expect(r.lang).toBe(navigator.language);
  act(() => r.onresult?.(result('final'))); expect(container.textContent).toBe('final interim');
  render(false); expect(r.abort).toHaveBeenCalledOnce(); expect(container.textContent).toBe('');
  expect(r.onresult).toBeNull();
});
it('ignores late results across rapid deactivate/reactivate and unmount', () => {
  render(true); const old = Recognition.instances[0]; const late = old.onresult!;
  render(false); render(true); const current = Recognition.instances[1];
  act(() => { current.onresult?.(result('new')); late(result('old')); });
  expect(container.textContent).toBe('new interim');
  act(() => root.render(null)); expect(current.abort).toHaveBeenCalledOnce();
  expect(current.onresult).toBeNull(); act(() => late(result('late')));
  expect(container.textContent).toBe('');
});
it.each(['onerror', 'onend'] as const)('clears and retires on %s without restarting', handler => {
  render(true); const r = Recognition.instances[0]; const late = r.onresult!;
  act(() => r.onresult?.(result('words')));
  act(() => r[handler]?.()); act(() => late(result('late')));
  expect(container.textContent).toBe(''); expect(r.abort).toHaveBeenCalledOnce();
  expect(Recognition.instances).toHaveLength(1);
});
it('handles missing API and Android without starting', () => {
  vi.stubGlobal('SpeechRecognition', undefined); render(true);
  expect(previewSupported()).toBe(false); expect(container.textContent).toBe('');
  render(false); vi.stubGlobal('SpeechRecognition', Recognition);
  vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue('Chrome Android'); render(true);
  expect(previewSupported()).toBe(false); expect(Recognition.instances).toHaveLength(0);
});
it('supports the prefixed API', () => {
  vi.stubGlobal('SpeechRecognition', undefined); vi.stubGlobal('webkitSpeechRecognition', Recognition);
  render(true); expect(Recognition.instances[0].start).toHaveBeenCalledOnce();
});
it.each(['constructor', 'start'])('silently handles a throwing %s', failure => {
  class Broken extends Recognition {
    constructor() { super(); if (failure === 'constructor') throw Error('unavailable'); }
    start = vi.fn(() => { throw Error('unavailable'); });
  }
  vi.stubGlobal('SpeechRecognition', Broken); render(true);
  expect(container.textContent).toBe('');
  if (failure === 'start') expect(Recognition.instances[0].abort).toHaveBeenCalledOnce();
});
