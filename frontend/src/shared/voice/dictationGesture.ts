/**
 * The composer mic's gesture and lock logic (#338 (port of cake_os #3832)), pure so vitest (node env) can drive every
 * row of the gesture table — the same reason `voiceRecorder.ts` exists.
 *
 * | Gesture                              | Result                                       |
 * |--------------------------------------|----------------------------------------------|
 * | press and hold, release              | transcribe, then SEND                        |
 * | hold, swipe up past `LOCK_DY`        | LOCKED; Stop puts the text in the box        |
 * | hold, slide left past `CANCEL_DX`    | CANCEL — nothing uploaded, mic released      |
 * | quick tap / click                    | starts LOCKED, the same as swipe-up          |
 * | the time or byte cap stops it        | the text goes in the box, even mid-hold      |
 *
 * The reducer decides; the component performs the returned `effect` against the recorder and the
 * upload. Nothing here touches a browser API.
 */

export const LOCK_DY = 60;
export const CANCEL_DX = 80;
export const TAP_MS = 250;
const TAP_SLOP = 10;

/** What a finished clip is for: send it, or drop the text in the box for review. */
export type DictationIntent = 'send' | 'review';

export type DictationState =
  | { phase: 'idle' }
  | { phase: 'holding'; originX: number; originY: number; pressedAt: number; dx: number }
  | { phase: 'locked' }
  | { phase: 'transcribing'; intent: DictationIntent }
  | { phase: 'failed'; intent: DictationIntent; retryable: boolean };

export type DictationEvent =
  | { type: 'press'; x: number; y: number; t: number }
  | { type: 'move'; x: number; y: number }
  /** `recording` is false while the mic permission is still pending. */
  | { type: 'release'; x: number; y: number; t: number; recording: boolean }
  /** The browser took the pointer (pointercancel / lost capture). */
  | { type: 'pointerLost' }
  | { type: 'stop' }
  | { type: 'cancel' }
  /** The recorder produced a clip — after a Stop, or because a cap stopped it. */
  | { type: 'clip' }
  /** The recorder failed, came back empty, or the mic was refused. */
  | { type: 'recorderFailed' }
  | { type: 'transcribed' }
  | { type: 'uploadFailed'; retryable: boolean }
  | { type: 'retry' }
  | { type: 'discard' };

export type DictationEffect = 'start' | 'stop' | 'cancel' | 'upload' | null;

export const IDLE: DictationState = { phase: 'idle' };

export function dictationReducer(
  state: DictationState,
  event: DictationEvent,
): { state: DictationState; effect: DictationEffect } {
  const stay = { state, effect: null };
  switch (event.type) {
    case 'press':
      if (state.phase !== 'idle') return stay;
      return {
        state: { phase: 'holding', originX: event.x, originY: event.y, pressedAt: event.t, dx: 0 },
        effect: 'start',
      };

    case 'move': {
      if (state.phase !== 'holding') return stay;
      const dx = Math.max(0, state.originX - event.x);
      const dy = state.originY - event.y;
      if (dx >= CANCEL_DX) return { state: IDLE, effect: 'cancel' };
      if (dy >= LOCK_DY) return { state: { phase: 'locked' }, effect: null };
      return { state: { ...state, dx }, effect: null };
    }

    case 'release': {
      if (state.phase !== 'holding') return stay;
      const quick = event.t - state.pressedAt < TAP_MS
        && Math.hypot(event.x - state.originX, event.y - state.originY) < TAP_SLOP;
      // A tap starts hands-free mode. So does letting go before the mic was granted: the first
      // time, the permission prompt is what the finger lets go FOR, and treating that release
      // as "send" would stop a recording that never began.
      if (quick || !event.recording) return { state: { phase: 'locked' }, effect: null };
      return { state: { phase: 'transcribing', intent: 'send' }, effect: 'stop' };
    }

    case 'pointerLost':
      // Never a silent send: keep recording, hands-free, with Stop and Cancel on screen.
      if (state.phase !== 'holding') return stay;
      return { state: { phase: 'locked' }, effect: null };

    case 'stop':
      if (state.phase !== 'locked') return stay;
      return { state: { phase: 'transcribing', intent: 'review' }, effect: 'stop' };

    case 'cancel':
      if (state.phase !== 'holding' && state.phase !== 'locked') return stay;
      return { state: IDLE, effect: 'cancel' };

    case 'clip':
      if (state.phase === 'transcribing') return { state, effect: 'upload' };
      // A clip while still recording means a cap stopped it: review, even mid-hold.
      if (state.phase === 'holding' || state.phase === 'locked') {
        return { state: { phase: 'transcribing', intent: 'review' }, effect: 'upload' };
      }
      return stay;

    case 'recorderFailed':
      if (state.phase === 'idle' || state.phase === 'failed') return stay;
      return { state: IDLE, effect: null };

    case 'transcribed':
      if (state.phase !== 'transcribing') return stay;
      return { state: IDLE, effect: null };

    case 'uploadFailed':
      if (state.phase !== 'transcribing') return stay;
      return { state: { phase: 'failed', intent: state.intent, retryable: event.retryable }, effect: null };

    case 'retry':
      if (state.phase !== 'failed' || !state.retryable) return stay;
      return { state: { phase: 'transcribing', intent: state.intent }, effect: 'upload' };

    case 'discard':
      if (state.phase !== 'failed') return stay;
      return { state: IDLE, effect: null };
  }
}

/** Joins typed text and a transcript into one message, typed text first. */
export function joinDraft(typed: string, transcript: string): string {
  return [typed.trim(), transcript.trim()].filter(Boolean).join(' ');
}
