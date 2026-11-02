import { useEffect, useRef, useState } from 'react';

// Local interfaces: Web Speech recognition is not in TypeScript's DOM library.
export interface PreviewResultEvent {
  results: ArrayLike<ArrayLike<{ transcript: string }>>;
}
export interface PreviewRecognition {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onresult: ((event: PreviewResultEvent) => void) | null;
  onerror: (() => void) | null;
  onend: (() => void) | null;
  start(): void;
  abort(): void;
}
type SpeechWindow = Window & {
  SpeechRecognition?: new () => PreviewRecognition;
  webkitSpeechRecognition?: new () => PreviewRecognition;
};
function recognitionConstructor() {
  if (typeof window === 'undefined') return undefined;
  const browser = window as SpeechWindow;
  return browser.SpeechRecognition ?? browser.webkitSpeechRecognition;
}
export function previewSupported(): boolean {
  // Android is skipped because Chrome lets SpeechRecognition take the mic from
  // MediaRecorder, blanking the batch clip; upgrade path: detect that conflict.
  return !!recognitionConstructor() && !/Android/i.test(navigator.userAgent);
}

/** Best-effort display only. Never feeds the recorder or the batch transcript. */
export function useSpeechPreview(active: boolean): string {
  const [text, setText] = useState('');
  const generation = useRef(0);
  useEffect(() => {
    const id = ++generation.current;
    let recognition: PreviewRecognition | undefined;
    const retire = () => {
      if (generation.current !== id) return;
      ++generation.current;
      if (recognition) {
        recognition.onresult = recognition.onerror = recognition.onend = null;
        try { recognition.abort(); } catch { /* best effort */ }
      }
      setText('');
    };
    if (active && previewSupported()) {
      try {
        const Constructor = recognitionConstructor()!;
        recognition = new Constructor();
        recognition.continuous = true;
        recognition.interimResults = true;
        recognition.lang = navigator.language;
        recognition.onresult = event => {
          if (generation.current === id) {
            setText(Array.from(event.results, result => result[0]?.transcript ?? '').join(' ').trim());
          }
        };
        recognition.onerror = recognition.onend = retire;
        recognition.start();
      } catch { retire(); }
    }
    return retire;
  }, [active]);
  return active ? text : '';
}
