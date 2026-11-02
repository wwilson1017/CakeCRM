/**
 * The composer mic's gesture table (#338 (port of cake_os #3832)), one row at a time.
 */
import { describe, expect, it } from 'vitest';
import {
  CANCEL_DX, IDLE, LOCK_DY, TAP_MS, dictationReducer, joinDraft,
  type DictationEvent, type DictationState,
} from './dictationGesture';

function run(events: DictationEvent[], from: DictationState = IDLE) {
  let state = from;
  const effects: string[] = [];
  for (const event of events) {
    const next = dictationReducer(state, event);
    state = next.state;
    if (next.effect) effects.push(next.effect);
  }
  return { state, effects };
}

const press = { type: 'press', x: 300, y: 500, t: 0 } as const;

describe('dictation gestures', () => {
  it('press-and-hold then release transcribes and SENDS', () => {
    const { state, effects } = run([
      press,
      { type: 'release', x: 302, y: 498, t: 1500, recording: true },
      { type: 'clip' },
    ]);
    expect(state).toEqual({ phase: 'transcribing', intent: 'send' });
    expect(effects).toEqual(['start', 'stop', 'upload']);
  });

  it('swipe up past the threshold locks, and Stop puts the text up for REVIEW', () => {
    const locked = run([press, { type: 'move', x: 300, y: 500 - LOCK_DY }]);
    expect(locked.state).toEqual({ phase: 'locked' });
    // Letting go after locking does nothing — that is the point of locking.
    const after = run([{ type: 'release', x: 300, y: 300, t: 2000, recording: true }, { type: 'stop' }, { type: 'clip' }], locked.state);
    expect(after.state).toEqual({ phase: 'transcribing', intent: 'review' });
    expect(after.effects).toEqual(['stop', 'upload']);
  });

  it('slide left past the threshold cancels with nothing uploaded', () => {
    const { state, effects } = run([
      press,
      { type: 'move', x: 300 - CANCEL_DX + 1, y: 500 },
      { type: 'move', x: 300 - CANCEL_DX, y: 500 },
      { type: 'release', x: 200, y: 500, t: 1500, recording: true },
      { type: 'clip' },
    ]);
    expect(state).toEqual(IDLE);
    expect(effects).toEqual(['start', 'cancel']);
  });

  it('a short drag left tracks progress without cancelling', () => {
    const { state } = run([press, { type: 'move', x: 260, y: 500 }]);
    expect(state).toMatchObject({ phase: 'holding', dx: 40 });
  });

  it('a quick tap (or desktop click) starts LOCKED mode', () => {
    const { state, effects } = run([press, { type: 'release', x: 301, y: 500, t: TAP_MS - 1, recording: true }]);
    expect(state).toEqual({ phase: 'locked' });
    expect(effects).toEqual(['start']);
  });

  it('a slow press that never moved still sends — only a QUICK release is a tap', () => {
    const { state } = run([press, { type: 'release', x: 300, y: 500, t: TAP_MS, recording: true }]);
    expect(state).toEqual({ phase: 'transcribing', intent: 'send' });
  });

  it('letting go before the mic was granted locks instead of stopping a recording that never began', () => {
    const { state, effects } = run([press, { type: 'release', x: 300, y: 500, t: 3000, recording: false }]);
    expect(state).toEqual({ phase: 'locked' });
    expect(effects).toEqual(['start']);
  });

  it('the browser stealing the pointer mid-hold locks rather than sending', () => {
    expect(run([press, { type: 'pointerLost' }]).state).toEqual({ phase: 'locked' });
  });

  it('Cancel from locked mode releases the mic', () => {
    const { state, effects } = run([{ type: 'cancel' }], { phase: 'locked' });
    expect(state).toEqual(IDLE);
    expect(effects).toEqual(['cancel']);
  });

  it('hitting the cap drops the text in the box — even mid-hold', () => {
    for (const from of [run([press]).state, { phase: 'locked' } as const]) {
      const { state, effects } = run([{ type: 'clip' }], from);
      expect(state).toEqual({ phase: 'transcribing', intent: 'review' });
      expect(effects).toEqual(['upload']);
    }
  });

  it('a recorder failure returns to idle', () => {
    expect(run([press, { type: 'recorderFailed' }]).state).toEqual(IDLE);
  });
});

describe('failure, retry and discard', () => {
  it('a failed send keeps its intent through Retry', () => {
    const { state, effects } = run([
      { type: 'uploadFailed', retryable: true },
      { type: 'retry' },
    ], { phase: 'transcribing', intent: 'send' });
    expect(state).toEqual({ phase: 'transcribing', intent: 'send' });
    expect(effects).toEqual(['upload']);
  });

  it('a failed review keeps its intent too', () => {
    const failed = run([{ type: 'uploadFailed', retryable: true }], { phase: 'transcribing', intent: 'review' });
    expect(failed.state).toEqual({ phase: 'failed', intent: 'review', retryable: true });
  });

  it('a clip that can never succeed offers no Retry', () => {
    const { state, effects } = run([
      { type: 'uploadFailed', retryable: false },
      { type: 'retry' },
    ], { phase: 'transcribing', intent: 'send' });
    expect(state).toEqual({ phase: 'failed', intent: 'send', retryable: false });
    expect(effects).toEqual([]);
  });

  it('Discard drops the clip and returns to idle', () => {
    expect(run([{ type: 'discard' }], { phase: 'failed', intent: 'send', retryable: true }).state).toEqual(IDLE);
  });

  it('a transcript ends the turn', () => {
    expect(run([{ type: 'transcribed' }], { phase: 'transcribing', intent: 'send' }).state).toEqual(IDLE);
  });

  it('a second press while busy is ignored', () => {
    const busy: DictationState = { phase: 'transcribing', intent: 'send' };
    expect(dictationReducer(busy, press)).toEqual({ state: busy, effect: null });
  });
});

describe('joinDraft', () => {
  it('puts typed text first, then the transcript, as one message', () => {
    expect(joinDraft('  Call back ', 'Tuesday morning ')).toBe('Call back Tuesday morning');
    expect(joinDraft('', 'hola')).toBe('hola');
    expect(joinDraft('typed', '')).toBe('typed');
  });
});
