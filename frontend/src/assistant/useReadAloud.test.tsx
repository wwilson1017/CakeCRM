// @vitest-environment jsdom
import { act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useReadAloud, type ReadAloud } from './useReadAloud';
import type { ChatMessage } from './types';
import { __resetReadAloudPrefsForTests, setReadAloudEnabled } from '../shared/voice/readAloudPrefs';
import { speechPlayer } from '../shared/voice/speechPlayer';
vi.mock('../shared/voice/speechPlayer', async importOriginal => {
  const real = await importOriginal<typeof import('../shared/voice/speechPlayer')>();
  const player = real.createSpeechPlayer({ speak: vi.fn(), cancel: vi.fn(), createUtterance: text => ({ text } as SpeechSynthesisUtterance) });
  vi.spyOn(player, 'play'); vi.spyOn(player, 'cancel');
  return { ...real, speechPlayer: () => player };
});
let root: Root, value: ReadAloud | null;
function Harness({ messages, isStreaming }: { messages: ChatMessage[]; isStreaming: boolean }) {
  const result = useReadAloud({ messages, isStreaming });
  useEffect(() => { value = result; });
  return null;
}
const player = speechPlayer();
const reply = (id: string, content = '**Hello**', streaming = false): ChatMessage => ({ id, content, role: 'assistant', streaming });
const user: ChatMessage = { id: 'u1', content: 'Hi', role: 'user' };
const render = (messages: ChatMessage[] = [], isStreaming = false) => act(() => root.render(<Harness messages={messages} isStreaming={isStreaming} />));
beforeEach(() => {
  __resetReadAloudPrefsForTests(); player.cancel(); vi.clearAllMocks();
  vi.stubGlobal('speechSynthesis', {}); vi.stubGlobal('SpeechSynthesisUtterance', class {});
  root = createRoot(document.createElement('div'));
});
afterEach(() => { act(() => root.unmount()); vi.unstubAllGlobals(); });
function send(id = 'a1') {
  act(() => value!.arm()); render([user, reply(id, '', true)], true); render([user, reply(id)]);
}
it('auto reads an armed send once and reads a new continuation', () => {
  setReadAloudEnabled(true); render(); send();
  expect(player.play).toHaveBeenCalledWith(expect.stringContaining('|a1'), ['Hello.']);
  render([user, reply('a1')], true); render([user, reply('a1')]);
  expect(player.play).toHaveBeenCalledTimes(1);
  render([user, reply('a1'), reply('a2', '', true)], true); render([user, reply('a1'), reply('a2', 'Next')]);
  expect(player.play).toHaveBeenCalledTimes(2);
  expect(player.play).toHaveBeenLastCalledWith(expect.stringContaining('|a2'), ['Next.']);
});
it('never reads a running history turn until locally armed', () => {
  setReadAloudEnabled(true); render([reply('old', '', true)], true); render([reply('old')]);
  expect(player.play).not.toHaveBeenCalled();
  send(); expect(player.play).toHaveBeenCalledTimes(1);
});
it('does not auto read when off, disarmed or errored', () => {
  render(); send(); expect(player.play).not.toHaveBeenCalled();
  act(() => value!.setEnabled(true)); act(() => value!.disarm());
  render([reply('a2', '', true)], true); render([reply('a2')]);
  expect(player.play).not.toHaveBeenCalled();
  act(() => value!.arm()); render([reply('a3', '', true)], true); render([{ ...reply('a3'), error: true }]);
  expect(player.play).not.toHaveBeenCalled();
});
it('plays and stops one message without changing the preference', () => {
  render([reply('a1')]); act(() => value!.playMessage('a1'));
  expect(value!.playingId).toBe('a1'); expect(value!.enabled).toBe(false);
  act(() => value!.playMessage('a1')); expect(value!.playingId).toBeNull();
});
it('cancels playback on new send, thread removal, disabling and unmount', () => {
  render([reply('a1')]); act(() => value!.playMessage('a1')); act(() => value!.arm());
  expect(player.getState().active).toBe(false);
  act(() => value!.playMessage('a1')); render(); expect(player.getState().active).toBe(false);
  render([reply('a1')]); act(() => value!.setEnabled(true)); act(() => value!.playMessage('a1'));
  act(() => value!.setEnabled(false)); expect(player.getState().active).toBe(false);
  act(() => value!.playMessage('a1')); act(() => root.render(null)); expect(player.getState().active).toBe(false);
});
it('hides when browser speech is absent', () => {
  vi.stubGlobal('SpeechSynthesisUtterance', undefined); render(); expect(value).toBeNull();
});
