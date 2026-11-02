import { useCallback, useEffect, useId, useRef, useSyncExternalStore } from 'react';
import { useReadAloudEnabled } from '../shared/voice/readAloudPrefs';
import { speechPlayer, speechSupported } from '../shared/voice/speechPlayer';
import { speakable } from '../shared/voice/speakable';
import { splitProse } from '../shared/voice/speechSegments';
import type { ChatMessage } from './types';
export interface ReadAloud {
  enabled: boolean; setEnabled: (on: boolean) => void; playingId: string | null;
  playMessage: (id: string) => void; arm: () => void; disarm: () => void;
}
export function useReadAloud({ messages, isStreaming }: { messages: ChatMessage[]; isStreaming: boolean }): ReadAloud | null {
  const supported = speechSupported();
  const player = speechPlayer();
  const prefix = `${useId()}|`;
  const [enabled, setEnabledPref] = useReadAloudEnabled();
  const state = useSyncExternalStore(player.subscribe, player.getState, player.getState);
  const playingId = state.active && state.owner?.startsWith(prefix) ? state.owner.slice(prefix.length) : null;
  const armed = useRef(false);
  const lastSpoken = useRef<string | null>(null);
  const prevStreaming = useRef(isStreaming);
  const speak = useCallback((message: ChatMessage) => {
    if (supported) player.play(prefix + message.id, splitProse(speakable(message.content)));
  }, [player, prefix, supported]);
  useEffect(() => {
    if (!supported) return;
    if (state.owner?.startsWith(prefix) && !messages.some(m => prefix + m.id === state.owner)) player.cancel(prefix);
  }, [messages, player, prefix, state.owner, supported]);
  useEffect(() => {
    if (prevStreaming.current && !isStreaming && enabled && armed.current) {
      const last = messages.at(-1);
      if (last?.role === 'assistant' && last.content && !last.error && last.id !== lastSpoken.current) {
        lastSpoken.current = last.id;
        speak(last);
      }
    }
    prevStreaming.current = isStreaming;
  }, [isStreaming, messages, enabled, speak]);
  useEffect(() => { if (supported && !enabled) player.cancel(prefix); }, [enabled, player, prefix, supported]);
  useEffect(() => () => { if (supported) player.cancel(prefix); }, [player, prefix, supported]);
  if (!supported) return null;
  return {
    enabled, playingId,
    setEnabled(on) { player.prime(); setEnabledPref(on); },
    arm() { armed.current = true; player.cancel(prefix); },
    disarm() { armed.current = false; lastSpoken.current = null; player.cancel(prefix); },
    playMessage(id) {
      if (player.getState().active && player.getState().owner === prefix + id) { player.cancel(prefix); return; }
      const message = messages.find(m => m.id === id);
      if (message?.role === 'assistant' && message.content && !message.error && !message.streaming) {
        player.prime(); speak(message);
      }
    },
  };
}
