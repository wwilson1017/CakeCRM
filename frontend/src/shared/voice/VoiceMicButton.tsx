import { useCallback, useEffect, useRef, useState } from 'react';
import { IconMic, IconX } from '../icons';
import { ACCENT_INK, BG_CARD, CORAL_FILL, INK_MUTE } from '../styles';
import { useVoiceReady } from './VoiceReadyContext';
import { IDLE, dictationReducer, type DictationEvent, type DictationEffect, type DictationIntent, type DictationState } from './dictationGesture';
import { useAudioRecorder, type RecordedClip, type RecorderError } from './useAudioRecorder';
import { DICTATION_BITS_PER_SECOND, DICTATION_MAX_BYTES, DICTATION_MAX_SECONDS, DICTATION_UPLOAD_CAP_BYTES, formatDuration } from './voiceRecorder';
import { transcribeClip, transcribeRetryable } from './transcribe';
import { stopSpeech } from './speechPlayer';

const ERROR_COPY: Record<RecorderError, string> = {
  unsupported: 'This browser cannot record audio.',
  denied: 'Microphone blocked — allow it for this site in your browser settings, then try again.',
  'start-failed': 'The microphone could not start — try again.',
  failed: 'Recording failed — try again.', empty: 'That recording came back empty — try again.',
};
const WAVE = [0.35, 0.6, 0.9, 0.55, 0.75, 1, 0.65, 0.4, 0.8, 0.95, 0.5, 0.7, 0.45, 0.85, 0.6, 0.3];
export interface VoiceMicButtonProps {
  mode: 'send' | 'review'; tidy?: boolean;
  onTranscript: (text: string, intent: DictationIntent) => void; disabled?: boolean;
}
export function VoiceMicButton({ mode, tidy = false, onTranscript, disabled }: VoiceMicButtonProps) {
  const ready = useVoiceReady();
  const [state, setState] = useState<DictationState>(IDLE);
  const [notice, setNotice] = useState('');
  const stateRef = useRef<DictationState>(IDLE);
  const clipRef = useRef<RecordedClip | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);
  const performRef = useRef<(effect: DictationEffect, next: DictationState) => void>(() => undefined);
  const dispatch = useCallback((event: DictationEvent) => {
    if (!mountedRef.current) return;
    const { state: next, effect } = dictationReducer(stateRef.current, event);
    stateRef.current = next; setState(next);
    if (effect) performRef.current(effect, next);
  }, []);
  const recorder = useAudioRecorder({
    maxSeconds: DICTATION_MAX_SECONDS, maxBytes: DICTATION_MAX_BYTES,
    bitsPerSecond: DICTATION_BITS_PER_SECOND, withLevel: true,
    onClip: clip => { if (mountedRef.current) { clipRef.current = clip; dispatch({ type: 'clip' }); } },
    onError: code => { if (mountedRef.current) { setNotice(ERROR_COPY[code]); dispatch({ type: 'recorderFailed' }); } },
  });
  const callbacksRef = useRef({ onTranscript, mode });
  useEffect(() => { callbacksRef.current = { onTranscript, mode }; });
  const cancelRecording = recorder.cancel;
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      abortRef.current?.abort(); cancelRecording(); clipRef.current = null;
    };
  }, [cancelRecording]);

  const upload = async (intent: DictationIntent) => {
    const clip = clipRef.current;
    if (!clip) { dispatch({ type: 'recorderFailed' }); return; }
    if (clip.blob.size > DICTATION_UPLOAD_CAP_BYTES) {
      setNotice('That recording is too long to transcribe.');
      dispatch({ type: 'uploadFailed', retryable: false }); return;
    }
    const abort = new AbortController(); abortRef.current = abort;
    try {
      const result = await transcribeClip(clip.blob, clip.mime, tidy, abort.signal);
      if (!mountedRef.current || abort.signal.aborted) return;
      clipRef.current = null;
      dispatch({ type: 'transcribed' });
      const text = result.text.trim();
      if (!text) setNotice("Didn't catch that — try again.");
      else callbacksRef.current.onTranscript(text, callbacksRef.current.mode === 'send' ? intent : 'review');
    } catch (err) {
      if (!mountedRef.current || abort.signal.aborted) return;
      dispatch({ type: 'uploadFailed', retryable: transcribeRetryable(err) });
    }
  };
  useEffect(() => {
    performRef.current = (effect, next) => {
      if (effect === 'start') { stopSpeech(); void recorder.start(); }
      else if (effect === 'stop') recorder.stop();
      else if (effect === 'cancel') { recorder.cancel(); clipRef.current = null; }
      else if (effect === 'upload' && next.phase === 'transcribing') void upload(next.intent);
    };
  });
  if (!ready || !recorder.supported || disabled) return null;
  const tap = (t: number) => {
    if (stateRef.current.phase !== 'idle') return;
    setNotice(''); dispatch({ type: 'press', x: 0, y: 0, t });
    dispatch({ type: 'release', x: 0, y: 0, t, recording: false });
  };
  const buttonStyle = { width: 30, height: 30, flexShrink: 0, borderRadius: 7, border: 'none', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', color: INK_MUTE, background: 'none' } as const;
  return <>
    {state.phase !== 'idle' && <div style={{ position: 'absolute', inset: 0, zIndex: 5, display: 'flex', alignItems: 'center', gap: 8, background: BG_CARD, borderRadius: 'inherit', padding: '0 8px', paddingRight: 40 }}>
      {(state.phase === 'holding' || state.phase === 'locked') && <>
        {state.phase === 'locked'
          ? <button type="button" aria-label="Cancel recording" onClick={() => dispatch({ type: 'cancel' })} style={buttonStyle}><IconX size={16} /></button>
          : <span aria-hidden style={{ width: 8, height: 8, flexShrink: 0, borderRadius: '50%', background: CORAL_FILL }} />}
        <span style={{ fontSize: 12, whiteSpace: 'nowrap' }}>{recorder.requesting ? 'Allow mic…' : formatDuration(recorder.seconds)}</span>
        <div aria-hidden style={{ display: 'flex', flex: 1, minWidth: 0, height: 24, alignItems: 'center', gap: 2, overflow: 'hidden' }}>
          {WAVE.map((w, i) => <span key={i} style={{ flexShrink: 0, width: 3, borderRadius: 2, background: CORAL_FILL, height: `${Math.max(12, Math.round(recorder.level * w * 100))}%` }} />)}
        </div>
        {state.phase === 'holding' && <span style={{ fontSize: 11, color: INK_MUTE }}>{mode === 'review' || state.dx > 0 ? '‹ Slide to cancel' : '‹ cancel · swipe up to lock'}</span>}
      </>}
      {state.phase === 'transcribing' && <span role="status">Transcribing…</span>}
      {state.phase === 'failed' && <div role="alert" style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
        <span>{notice || "Couldn't transcribe."}</span>
        {state.retryable && <button type="button" onClick={() => dispatch({ type: 'retry' })}>Retry</button>}
        <button type="button" onClick={() => { clipRef.current = null; dispatch({ type: 'discard' }); }}>Discard</button>
      </div>}
    </div>}
    {state.phase === 'locked' ? <button key="stop" type="button" aria-label="Stop recording and review the text"
      style={{ ...buttonStyle, position: 'absolute', right: 4, bottom: 0, zIndex: 6, background: CORAL_FILL, color: ACCENT_INK }}
      onClick={() => dispatch({ type: recorder.requesting ? 'cancel' : 'stop' })}>■</button>
      : (state.phase === 'idle' || state.phase === 'holding') && <button key="mic" type="button"
        style={{ ...buttonStyle, position: 'relative', zIndex: 6, touchAction: 'none', userSelect: 'none', WebkitTouchCallout: 'none', ...(state.phase === 'holding' ? { background: CORAL_FILL, color: ACCENT_INK } : {}) }}
        aria-label={mode === 'send' ? 'Voice input: hold to talk and release to send, or tap for hands-free' : 'Voice input: hold to talk, or tap for hands-free — the text lands in the box to check'}
        onPointerDown={e => {
          if (e.button !== 0) return;
          e.preventDefault(); e.currentTarget.setPointerCapture(e.pointerId); setNotice('');
          dispatch({ type: 'press', x: e.clientX, y: e.clientY, t: e.timeStamp });
        }}
        onPointerMove={e => { if (stateRef.current.phase === 'holding') dispatch({ type: 'move', x: e.clientX, y: e.clientY }); }}
        onPointerUp={e => dispatch({ type: 'release', x: e.clientX, y: e.clientY, t: e.timeStamp, recording: recorder.recording })}
        onPointerCancel={() => dispatch({ type: 'pointerLost' })} onLostPointerCapture={() => dispatch({ type: 'pointerLost' })}
        onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); tap(e.timeStamp); } }}
        onClick={e => { if (e.detail === 0) tap(e.timeStamp); }} onContextMenu={e => e.preventDefault()}><IconMic size={18} /></button>}
    {notice && state.phase === 'idle' && <span role="status" style={{ position: 'absolute', bottom: '100%', right: 0, fontSize: 12, background: BG_CARD, color: INK_MUTE, padding: 6 }}>{notice}</span>}
  </>;
}
