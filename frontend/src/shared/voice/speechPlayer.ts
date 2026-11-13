/** One browser speech queue for the whole page. Generation changes precede cancellation. */
export interface SynthDeps {
  speak(u: SpeechSynthesisUtterance): void;
  cancel(): void;
  createUtterance(text: string): SpeechSynthesisUtterance;
}
export interface SpeechState { owner: string | null; active: boolean }
export interface SpeechPlayer {
  play(owner: string, segments: string[]): void;
  cancel(ownerPrefix?: string): void;
  prime(): void;
  getState(): SpeechState;
  subscribe(listener: () => void): () => void;
}
export function speechSupported(): boolean {
  return typeof window !== 'undefined' && 'speechSynthesis' in window
    && typeof SpeechSynthesisUtterance === 'function';
}
export function createSpeechPlayer(deps: SynthDeps): SpeechPlayer {
  let generation = 0;
  let queue: string[] = [];
  let state: SpeechState = { owner: null, active: false };
  const listeners = new Set<() => void>();
  const publish = (owner: string | null, active: boolean) => {
    state = { owner, active };
    listeners.forEach(l => l());
  };
  const next = () => {
    const text = queue.shift();
    if (text === undefined) { publish(state.owner, false); return; }
    const gen = generation;
    const u = deps.createUtterance(text);
    // Some engines can emit both callbacks for the same utterance.
    let settled = false;
    u.onend = u.onerror = () => {
      if (settled || gen !== generation) return;
      settled = true;
      next();
    };
    publish(state.owner, true);
    deps.speak(u);
  };
  return {
    play(owner, segments) {
      generation++;
      deps.cancel();
      queue = segments.filter(s => s.trim());
      publish(owner, false);
      next();
    },
    cancel(prefix) {
      if (prefix !== undefined && !state.owner?.startsWith(prefix)) return;
      generation++;
      deps.cancel();
      queue = [];
      publish(null, false);
    },
    prime() { deps.speak(deps.createUtterance('')); },
    getState: () => state,
    subscribe(listener) { listeners.add(listener); return () => { listeners.delete(listener); }; },
  };
}
let singleton: SpeechPlayer | undefined;
export function speechPlayer(): SpeechPlayer {
  singleton ??= createSpeechPlayer({
    speak: u => window.speechSynthesis.speak(u),
    cancel: () => window.speechSynthesis.cancel(),
    createUtterance: text => new SpeechSynthesisUtterance(text),
  });
  return singleton;
}
export function stopSpeech(): void { if (speechSupported()) speechPlayer().cancel(); }
